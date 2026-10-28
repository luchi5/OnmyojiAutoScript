"""Migration contracts, using fake inference only: no models, services or game state."""
import builtins
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_backend():
    spec = importlib.util.spec_from_file_location(
        "isolated_ocr_backend", ROOT / "module/ocr/backend.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_rapid(monkeypatch, tmp_path):
    """Only model-file existence is real; every inference entry point is mocked."""
    package = tmp_path / "rapidocr"
    models = package / "models"
    models.mkdir(parents=True)
    for name in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"):
        (models / name).write_bytes(b"placeholder; never passed to an inference engine")
    rapid = ModuleType("rapidocr")
    rapid.__file__ = str(package / "__init__.py")
    rapid.EngineType = SimpleNamespace(ONNXRUNTIME="onnxruntime")
    rapid.ModelType = SimpleNamespace(SMALL="small")
    rapid.OCRVersion = SimpleNamespace(PPOCRV6="PP-OCRv6")
    engine = Mock(name="fake_inference")
    engine.recognize_txt.return_value = SimpleNamespace(
        txts=("萤草",), scores=(np.float32(.9),), elapse=.012
    )
    engine.return_value = SimpleNamespace(boxes=None, elapse=.021)
    rapid.RapidOCR = Mock(name="RapidOCR_constructor", return_value=engine)
    monkeypatch.setitem(sys.modules, "rapidocr", rapid)
    adapter = importlib.import_module("module.ocr.rapidocr_adapter")
    # Reload so a module-level optional import cannot retain another test's fake.
    adapter = importlib.reload(adapter)
    return SimpleNamespace(module=adapter, package=rapid, engine=engine, models=models)


def colored_image(height=4, width=10):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = 17
    image[:, :, 1] = 43
    image[:, :, 2] = 211
    return image


def rectangle(x, y, width=12, height=6):
    return np.array([[x, y], [x + width, y], [x + width, y + height],
                     [x, y + height]], dtype=np.float32)


def test_default_backend_stays_legacy_without_importing_rapid(monkeypatch):
    legacy = Mock(return_value=object())
    monkeypatch.delenv("OAS_OCR_BACKEND", raising=False)
    monkeypatch.setitem(sys.modules, "module.ocr.ppocr", SimpleNamespace(TextSystem=legacy))
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "rapidocr" or name.startswith("rapidocr.") or name.endswith("rapidocr_adapter"):
            raise AssertionError("The legacy path must not load optional RapidOCR")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = load_backend().create_text_system(box_thresh=.7)
    assert result is legacy.return_value
    legacy.assert_called_once_with(box_thresh=.7)


def test_explicit_backend_overrides_environment(monkeypatch):
    legacy = Mock(return_value=object())
    rapid = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "module.ocr.ppocr", SimpleNamespace(TextSystem=legacy))
    monkeypatch.setitem(sys.modules, "module.ocr.rapidocr_adapter",
                        SimpleNamespace(RapidTextSystem=rapid))
    monkeypatch.setenv("OAS_OCR_BACKEND", "rapid-small")
    backend = load_backend()
    assert backend.create_text_system() is rapid.return_value
    assert backend.create_text_system("legacy") is legacy.return_value
    rapid.assert_called_once_with()
    legacy.assert_called_once_with()


def test_unknown_backend_cannot_silently_fall_back(monkeypatch):
    monkeypatch.setenv("OAS_OCR_BACKEND", "rapdi-small")
    with pytest.raises(ValueError, match="rapdi-small"):
        load_backend().create_text_system()


def test_process_override_takes_precedence_over_shared_config(monkeypatch):
    backend = load_backend()
    create = Mock(return_value=object())
    monkeypatch.setattr(backend, "create_text_system", create)
    monkeypatch.setenv("OAS_OCR_BACKEND", "rapid-small")
    config = SimpleNamespace(OcrBackend="legacy")
    assert backend.configured_text_system(config, threads=1) is create.return_value
    create.assert_called_once_with("rapid-small", threads=1)
    assert config.OcrBackend == "legacy"


def test_disabled_auto_install_defaults_to_legacy(monkeypatch):
    backend = load_backend()
    create = Mock()
    monkeypatch.setattr(backend, "create_text_system", create)
    monkeypatch.delenv("OAS_OCR_BACKEND", raising=False)
    backend.configured_text_system(SimpleNamespace(AutoInstallRapidOcr=False))
    create.assert_called_once_with("legacy")


@pytest.mark.parametrize("selected", ["legacy", "rapid-small"])
def test_configured_bgr_hint_is_only_forwarded_to_rapid(monkeypatch, selected):
    legacy = Mock(return_value=object())
    rapid = Mock(return_value=object())
    monkeypatch.delenv("OAS_OCR_BACKEND", raising=False)
    monkeypatch.setitem(sys.modules, "module.ocr.ppocr", SimpleNamespace(TextSystem=legacy))
    monkeypatch.setitem(sys.modules, "module.ocr.rapidocr_adapter",
                        SimpleNamespace(RapidTextSystem=rapid))
    config = SimpleNamespace(OcrBackend=selected, AutoInstallRapidOcr=False)
    result = load_backend().configured_text_system(config, input_color="BGR", box_thresh=.7)
    if selected == "legacy":
        assert result is legacy.return_value
        legacy.assert_called_once_with(box_thresh=.7)
        rapid.assert_not_called()
    else:
        assert result is rapid.return_value
        rapid.assert_called_once_with(input_color="BGR", box_thresh=.7)
        legacy.assert_not_called()
    assert config.OcrBackend == selected


def test_missing_optional_dependency_has_actionable_error(monkeypatch):
    original_import = builtins.__import__

    def missing_rapid(name, *args, **kwargs):
        if name == "rapidocr" or name.startswith("rapidocr."):
            raise ModuleNotFoundError("No module named 'rapidocr'", name="rapidocr")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_rapid)
    with pytest.raises(RuntimeError) as error:
        load_backend().create_text_system("rapid-small")
    message = str(error.value).lower()
    assert "rapid" in message
    assert any(hint in message for hint in ("requirements", "pip", "install", "安装"))


def test_initialization_uses_local_models_and_cpu_thread_limits(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    params = fake_rapid.package.RapidOCR.call_args.kwargs["params"]
    assert Path(params["Det.model_path"]) == fake_rapid.models / "PP-OCRv6_det_small.onnx"
    assert Path(params["Rec.model_path"]) == fake_rapid.models / "PP-OCRv6_rec_small.onnx"
    assert params["Global.use_cls"] is False
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 1
    assert params["EngineConfig.onnxruntime.inter_op_num_threads"] == 1
    assert target.input_color == "RGB"
    fake_rapid.engine._load_rec_model.assert_not_called()
    fake_rapid.engine._load_det_model.assert_not_called()


def test_missing_model_fails_before_inference_or_download(fake_rapid):
    (fake_rapid.models / "PP-OCRv6_rec_small.onnx").unlink()
    with pytest.raises((FileNotFoundError, RuntimeError), match="(?i)(model|onnx|模型)"):
        fake_rapid.module.RapidTextSystem()
    fake_rapid.package.RapidOCR.assert_not_called()


@pytest.mark.parametrize("options", [
    {"use_angle_cls": True}, {"input_color": "XYZ"}, {"threads": 0},
    {"ort_providers": ["CUDAExecutionProvider"]},
])
def test_unsupported_configuration_is_explicit(fake_rapid, options):
    with pytest.raises(ValueError):
        fake_rapid.module.RapidTextSystem(**options)


def test_single_line_converts_rgb_and_keeps_input_unchanged(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    image = colored_image()
    original = image.copy()
    text, confidence = target.ocr_single_line(image)
    assert text == "萤草"
    assert isinstance(confidence, float)
    assert confidence == pytest.approx(.9)
    actual = fake_rapid.engine.recognize_txt.call_args.args[0][0]
    np.testing.assert_array_equal(actual, original[:, :, ::-1])
    np.testing.assert_array_equal(image, original)
    fake_rapid.engine.assert_not_called()


def test_bgr_input_is_not_swapped(fake_rapid):
    target = fake_rapid.module.RapidTextSystem(input_color="BGR")
    image = colored_image()
    target.ocr_single_line(image)
    actual = fake_rapid.engine.recognize_txt.call_args.args[0][0]
    np.testing.assert_array_equal(actual, image)


def test_line_batch_preserves_order_and_rotates_only_vertical_lines(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    horizontal = colored_image(4, 12)
    vertical = np.arange(12 * 4 * 3, dtype=np.uint8).reshape(12, 4, 3)
    fake_rapid.engine.recognize_txt.return_value = SimpleNamespace(
        txts=("123", "特殊"), scores=(.92, .88), elapse=.03
    )
    assert target.ocr_lines([horizontal, vertical]) == [("123", .92), ("特殊", .88)]
    actual = fake_rapid.engine.recognize_txt.call_args.args[0]
    assert len(actual) == 2
    np.testing.assert_array_equal(actual[0], horizontal[:, :, ::-1])
    np.testing.assert_array_equal(actual[1], np.rot90(vertical)[:, :, ::-1])
    fake_rapid.engine.assert_not_called()


def test_empty_batches_do_not_start_inference(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    assert target.ocr_lines([]) == []
    pairs, elapsed = target.text_recognizer([])
    assert pairs == []
    assert elapsed >= 0
    fake_rapid.engine.recognize_txt.assert_not_called()


def test_incomplete_batch_cannot_shift_labels_onto_other_images(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    with pytest.raises(RuntimeError, match="incomplete"):
        target.ocr_lines([colored_image(), colored_image()])


def test_zero_sized_image_is_rejected_before_inference(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    with pytest.raises(ValueError):
        target.ocr_single_line(np.empty((0, 10, 3), dtype=np.uint8))
    fake_rapid.engine.recognize_txt.assert_not_called()
    fake_rapid.engine.assert_not_called()


def test_detector_returns_legacy_shape_and_applies_temporary_overrides(fake_rapid):
    target = fake_rapid.module.RapidTextSystem(box_thresh=.6, unclip_ratio=1.6)
    boxes = np.array([rectangle(1, 2)])
    fake_rapid.engine.return_value = SimpleNamespace(boxes=boxes, elapse=.021)
    target.text_detector.box_thresh = .2
    image = colored_image(20, 40)
    result, elapsed = target.text_detector(image, unclip_ratio=2.0, box_thresh=.3)
    np.testing.assert_array_equal(result, boxes)
    assert elapsed >= 0
    assert fake_rapid.engine.call_args.kwargs["box_thresh"] == .3
    assert fake_rapid.engine.call_args.kwargs["unclip_ratio"] == 2.0
    assert fake_rapid.engine.call_args.kwargs["use_rec"] is False
    assert fake_rapid.engine.call_args.kwargs["use_cls"] is False
    np.testing.assert_array_equal(fake_rapid.engine.call_args.args[0], image[:, :, ::-1])
    target.text_detector(image)
    assert fake_rapid.engine.call_args.kwargs["box_thresh"] == .2
    assert fake_rapid.engine.call_args.kwargs["unclip_ratio"] == 1.6


def test_no_detection_returns_empty_legacy_result_without_recognition(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    assert target.detect_and_ocr(colored_image()) == []
    fake_rapid.engine.recognize_txt.assert_not_called()


def test_detector_discards_boxes_that_cannot_produce_finite_nonempty_crops(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    valid = rectangle(1, 2)
    nan_box, inf_box = rectangle(3, 4), rectangle(4, 5)
    nan_box[0, 0], inf_box[2, 1] = np.nan, np.inf
    fake_rapid.engine.return_value = SimpleNamespace(boxes=np.array([
        rectangle(1, 2, width=0), valid, rectangle(1, 2, height=0),
        nan_box, inf_box, rectangle(1, 2, width=.5),
    ]), elapse=.02)
    results = target.detect_and_ocr(colored_image(20, 40))
    assert len(results) == 1
    np.testing.assert_array_equal(results[0].box, valid)
    assert len(fake_rapid.engine.recognize_txt.call_args.args[0]) == 1


def test_all_collapsed_detections_return_empty_without_recognition(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    fake_rapid.engine.return_value = SimpleNamespace(
        boxes=np.array([rectangle(1, 2, width=0), rectangle(1, 2, height=0)]), elapse=.02
    )
    assert target.detect_and_ocr(colored_image(20, 40)) == []
    fake_rapid.engine.recognize_txt.assert_not_called()


def test_detector_restores_thresholds_even_when_inference_raises(fake_rapid):
    target = fake_rapid.module.RapidTextSystem(box_thresh=.6, unclip_ratio=1.6)
    target.text_detector.box_thresh = .2
    target.text_detector.unclip_ratio = 1.8
    fake_rapid.engine.side_effect = RuntimeError("inference failed")
    with pytest.raises(RuntimeError, match="inference failed"):
        target.text_detector(colored_image(20, 40), box_thresh=.4, unclip_ratio=2.2)
    fake_rapid.engine.update_params.assert_called_once_with(box_thresh=.2, unclip_ratio=1.8)
    assert target.text_detector.box_thresh == .2
    assert target.text_detector.unclip_ratio == 1.8
    fake_rapid.engine.side_effect = None
    target.text_detector(colored_image(20, 40))
    assert fake_rapid.engine.call_args.kwargs["box_thresh"] == .2
    assert fake_rapid.engine.call_args.kwargs["unclip_ratio"] == 1.8


def test_grayscale_detected_crop_can_be_recognized_and_reused(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    image = np.full((20, 40), 117, dtype=np.uint8)
    fake_rapid.engine.return_value = SimpleNamespace(
        boxes=np.array([rectangle(1, 2)]), elapse=.02
    )
    results = target.detect_and_ocr(image)
    assert len(results) == 1
    assert results[0].text_img.shape == (6, 12)
    np.testing.assert_array_equal(results[0].text_img, np.full((6, 12), 117, dtype=np.uint8))
    detector_input = fake_rapid.engine.call_args.args[0]
    recognized_crop = fake_rapid.engine.recognize_txt.call_args.args[0][0]
    assert detector_input.shape == (20, 40, 3)
    assert recognized_crop.shape == (6, 12, 3)
    np.testing.assert_array_equal(recognized_crop, np.full((6, 12, 3), 117, dtype=np.uint8))
    assert target.ocr_single_line(results[0].text_img)[0] == "萤草"
    np.testing.assert_array_equal(image, np.full((20, 40), 117, dtype=np.uint8))


def test_detect_and_ocr_preserves_box_order_crops_and_drop_score(fake_rapid):
    from ppocronnx.predict_system import BoxedResult

    target = fake_rapid.module.RapidTextSystem()
    image = colored_image(40, 80)
    first, second, last = rectangle(2, 3), rectangle(30, 4), rectangle(1, 20)
    fake_rapid.engine.return_value = SimpleNamespace(
        boxes=np.array([last, second, first]), elapse=.02
    )
    fake_rapid.engine.recognize_txt.return_value = SimpleNamespace(
        txts=("第一个", "低置信度", "最后一个"), scores=(.99, .49, .5), elapse=.03
    )
    results = target.detect_and_ocr(image, drop_score=.5)
    assert [item.ocr_text for item in results] == ["第一个", "最后一个"]
    assert all(isinstance(item, BoxedResult) for item in results)
    np.testing.assert_array_equal(results[0].box, first)
    np.testing.assert_array_equal(results[1].box, last)
    assert results[0].text_img.shape == (6, 12, 3)
    # Existing callers re-use returned crops as RGB input for a second reading.
    np.testing.assert_array_equal(results[0].text_img[2, 2], image[5, 4])
    recognized = fake_rapid.engine.recognize_txt.call_args.args[0]
    np.testing.assert_array_equal(recognized[0][2, 2], image[5, 4, ::-1])


def test_detect_path_honors_existing_replaceable_recognizer_hook(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    fake_rapid.engine.return_value = SimpleNamespace(
        boxes=np.array([rectangle(1, 2)]), elapse=.02
    )
    override = Mock(return_value=([("竖排补丁", .96)], .01))
    target.text_recognizer = override
    results = target.detect_and_ocr(colored_image(20, 40))
    assert results[0].ocr_text == "竖排补丁"
    override.assert_called_once()
    fake_rapid.engine.recognize_txt.assert_not_called()


def test_character_whitelist_cannot_be_silently_ignored(fake_rapid):
    target = fake_rapid.module.RapidTextSystem()
    target.set_char_whitelist(None)
    with pytest.raises(NotImplementedError):
        target.set_char_whitelist("0123456789")
