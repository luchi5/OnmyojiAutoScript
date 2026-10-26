"""PP-OCRv6 small with the legacy TextSystem hooks and RGB application boundary.

RapidOCR stays optional: this module is only imported after explicit selection.
Models are bundled with the pinned wheel; a missing model fails without download.
"""
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from module.ocr.ppocr import TextSystem as LegacyTextSystem


class _Recognizer:
    def __init__(self, owner):
        self.owner = owner

    def __call__(self, images):
        if not images:
            return [], 0.0
        start = time.perf_counter()
        converted = [self.owner._to_bgr(image) for image in images]
        with self.owner._lock:
            result = self.owner._rapid.recognize_txt(converted)
        if result.txts is None or len(result.txts) != len(images) or len(result.scores) != len(images):
            raise RuntimeError("RapidOCR returned an incomplete recognition batch")
        return [(str(text), float(score)) for text, score in zip(result.txts, result.scores)], time.perf_counter() - start

    def set_char_whitelist(self, chars):
        if chars is not None:
            raise NotImplementedError("rapid-small does not support decoder character whitelists")


class _Detector:
    def __init__(self, owner, box_thresh, unclip_ratio):
        self.owner = owner
        self.box_thresh = box_thresh
        self.unclip_ratio = unclip_ratio

    def __call__(self, image, unclip_ratio=None, box_thresh=None):
        start = time.perf_counter()
        image = self.owner._to_bgr(image)
        box = self.box_thresh if box_thresh is None else box_thresh
        unclip = self.unclip_ratio if unclip_ratio is None else unclip_ratio
        with self.owner._lock:
            try:
                # Keep Rapid's bounded resize/padding and original-image box mapping,
                # but let the inherited pipeline call the public recognition hook.
                result = self.owner._rapid(image, use_det=True, use_cls=False, use_rec=False,
                                           box_thresh=box, unclip_ratio=unclip)
            finally:
                self.owner._rapid.update_params(box_thresh=self.box_thresh, unclip_ratio=self.unclip_ratio)
        boxes = np.empty((0, 4, 2), dtype=np.float32) if result.boxes is None else np.asarray(result.boxes, dtype=np.float32).reshape(-1, 4, 2)
        valid = []
        for box in boxes:
            # A box in Rapid's padding can collapse after mapping to the source.
            # The inherited perspective crop requires nonzero integer dimensions.
            if not np.isfinite(box).all():
                continue
            width = max(np.linalg.norm(box[0]-box[1]), np.linalg.norm(box[2]-box[3]))
            height = max(np.linalg.norm(box[0]-box[3]), np.linalg.norm(box[1]-box[2]))
            if width >= 1 and height >= 1 and abs(cv2.contourArea(box)) > 0:
                valid.append(box)
        boxes = np.asarray(valid, dtype=np.float32).reshape(-1, 4, 2)
        return boxes, time.perf_counter() - start


class RapidTextSystem(LegacyTextSystem):
    backend = "rapid-small"

    def __init__(self, use_angle_cls=False, box_thresh=0.6, unclip_ratio=1.6,
                 rec_model_path=None, det_model_path=None, ort_providers=None,
                 input_color="RGB", threads=1):
        # Deliberately do not construct the old detector or recognizer.
        if use_angle_cls:
            raise ValueError("rapid-small currently supports use_angle_cls=False only")
        if ort_providers is not None and list(ort_providers) != ["CPUExecutionProvider"]:
            raise ValueError("rapid-small currently supports CPUExecutionProvider only")
        if input_color not in ("RGB", "BGR"):
            raise ValueError("input_color must be RGB or BGR")
        if not isinstance(threads, int) or isinstance(threads, bool) or threads < 1:
            raise ValueError("threads must be a positive integer")
        try:
            import rapidocr
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("rapid-small requires the isolated RapidOCR runtime; install requirements-rapidocr.txt there, or select legacy") from exc
        model_dir = Path(rapidocr.__file__).resolve().parent / "models"
        det_path = Path(det_model_path) if det_model_path is not None else model_dir / "PP-OCRv6_det_small.onnx"
        rec_path = Path(rec_model_path) if rec_model_path is not None else model_dir / "PP-OCRv6_rec_small.onnx"
        for path in (det_path, rec_path):
            if not path.is_file():
                raise FileNotFoundError(f"Missing local rapid-small model: {path}; no automatic download")
        self.input_color = input_color
        self.threads = threads
        self.use_angle_cls = False
        self._lock = threading.RLock()
        params = {
            "Det.engine_type": rapidocr.EngineType.ONNXRUNTIME,
            "Rec.engine_type": rapidocr.EngineType.ONNXRUNTIME,
            "Det.ocr_version": rapidocr.OCRVersion.PPOCRV6,
            "Rec.ocr_version": rapidocr.OCRVersion.PPOCRV6,
            "Det.model_type": rapidocr.ModelType.SMALL, "Rec.model_type": rapidocr.ModelType.SMALL,
            "Det.model_path": str(det_path), "Rec.model_path": str(rec_path),
            "Global.model_root_dir": str(model_dir), "Global.use_cls": False,
            "Global.log_level": "warning", "Det.limit_type": "min", "Det.limit_side_len": 960,
            "Det.box_thresh": box_thresh, "Det.unclip_ratio": unclip_ratio,
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
            "EngineConfig.onnxruntime.use_dml": False,
        }
        self._rapid = RapidOCR(params=params)
        self.text_detector = _Detector(self, box_thresh, unclip_ratio)
        self.text_recognizer = _Recognizer(self)

    def _to_bgr(self, image):
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
            raise ValueError("OCR expects a nonempty uint8 image")
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("OCR expects grayscale or three-channel images")
        if self.input_color == "RGB":
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        return np.ascontiguousarray(image)

    def ocr_lines(self, img_list):
        # Validate before the inherited vertical-image aspect-ratio division.
        for image in img_list:
            if not isinstance(image, np.ndarray) or image.ndim not in (2, 3) or 0 in image.shape[:2]:
                raise ValueError("OCR expects nonempty images")
        return super().ocr_lines(img_list)
