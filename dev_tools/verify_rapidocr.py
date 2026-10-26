"""Offline integration verification: no State, logger, device or server imports."""
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import statistics
import sys
import time
import unicodedata
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--isolated-python", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    import psutil
    process = psutil.Process()
    process.nice(psutil.IDLE_PRIORITY_CLASS)
    process.cpu_affinity([process.cpu_affinity()[-1]])
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    from module.ocr.backend import create_text_system
    from tasks.DraftDuel.ocr_names import read_name
    from tasks.DraftDuel.ocr_numbers import read_number

    def load(path):
        bgr = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f"Invalid image: {path}")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def norm(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))

    manifest_path = args.corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = manifest["entries"]
    cached = {}
    for e in entries:
        path = args.corpus / e["image"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == e["image_sha256"]
        cached[e["id"]] = (load(path), load(args.corpus/e["expanded_image"]) if e.get("expanded_image") else None)
    names = set(json.loads((ROOT/"tasks/DraftDuel/selection_policy.json").read_text(encoding="utf-8"))["profiles"])
    names.add("萤草")
    if args.isolated_python:
        from module.ocr.isolated_rapid import IsolatedRapidTextSystem
        model = IsolatedRapidTextSystem(args.isolated_python)
    else:
        model = create_text_system("rapid-small")

    class Reader:
        def __call__(self, pixels):
            return model.ocr_single_line(pixels)

        def name_readings(self, pixels):
            padded = cv2.copyMakeBorder(pixels, 15, 15, 15, 15, cv2.BORDER_CONSTANT)
            items = model.detect_and_ocr(padded)
            if len(items) != 1:
                return (("", 0.),)
            item = items[0]
            return ((item.ocr_text, float(item.score)), model.ocr_single_line(cv2.resize(item.text_img, None, fx=2, fy=2)))
    reader = Reader()
    model.ocr_single_line(cached[entries[0]["id"]][0])
    output = {"backend": "rapid-small", "input_color": "RGB (decoded PNG converted once)",
              "python": sys.executable, "rounds": args.rounds, "cv2": cv2.__version__,
              "versions": {},
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "raw": [], "pipeline": [], "full_frame_contract": []}
    for package in ("rapidocr", "onnxruntime", "numpy", "ppocr-onnx"):
        try:
            output["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            output["versions"][package] = "not installed in application process"
    if args.isolated_python:
        output["isolated_python"] = str(args.isolated_python)
        output["worker_pid"] = model._rapid.process.pid
    output["source_sha256"] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        for name in ("module/ocr/rapidocr_adapter.py", "module/ocr/backend.py", "module/ocr/models.py",
                     "module/ocr/rpc.py", "tasks/DraftDuel/local_draft.py", "tests/test_rapidocr_adapter.py",
                     "module/ocr/isolated_rapid.py", "module/ocr/rapid_worker.py", "module/ocr/account_runtime.py")}
    args.output.mkdir(parents=True, exist_ok=True)
    def save():
        (args.output/"integration-result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    for stage in ("raw", "pipeline"):
        cases = entries if stage == "raw" else [e for e in entries if e["split"] == "existing_regression" and e["category"] in ("name", "number")]
        for e in cases:
            pixels, expanded = cached[e["id"]]
            row = {"id": e["id"], "category": e["category"], "expected": e["expected"], "runs": []}
            for _ in range(args.rounds):
                start = time.perf_counter()
                if stage == "raw":
                    text, score = reader(pixels)
                    correct = norm(text) == norm(e["expected"])
                elif e["category"] == "name":
                    r = read_name(reader, pixels, names, threshold=e.get("threshold", .85), expanded=expanded)
                    text, score, correct = r.name, r.confidence, r.name == e["expected"]
                else:
                    field = e["field"]
                    minimum = 50 if field == "speed" else 0
                    maximum = 100 if field == "crit_chance" else (500 if field == "speed" else 100000)
                    r = read_number(reader, pixels, minimum, maximum, percent=e.get("percent", False), threshold=.8)
                    text, score, correct = r.value, r.confidence, r.value == e["expected_value"]
                row["runs"].append({"text": text, "score": float(score), "correct": bool(correct), "elapsed_ms": (time.perf_counter()-start)*1000})
                time.sleep(.01)
            output[stage].append(row)
            save()
        print(json.dumps({"stage": stage, "cases": len(cases), "all_rounds_correct": sum(all(r["correct"] for r in row["runs"]) for row in output[stage])}), flush=True)

    # Original six annotated frames: validate real boxes/crops, not full-frame accuracy.
    sources = sorted({e["source"] for e in entries if e["split"] == "existing_regression"})
    for source in sources:
        pixels = load(Path(source))
        start = time.perf_counter()
        items = model.detect_and_ocr(pixels)
        elapsed = (time.perf_counter()-start)*1000
        assert items, f"No detections in annotated frame {source}"
        for item in items:
            assert isinstance(item.box, np.ndarray) and item.box.shape == (4, 2)
            assert np.isfinite(item.box).all() and (item.box >= 0).all()
            assert (item.box[:, 0] <= pixels.shape[1]).all() and (item.box[:, 1] <= pixels.shape[0]).all()
            assert item.text_img is not None and item.text_img.size > 0
            assert 0 <= item.score <= 1 and isinstance(item.ocr_text, str)
        output["full_frame_contract"].append({"source": source, "detections": len(items), "elapsed_ms": elapsed, "valid": True})
        save()
        time.sleep(.05)
    output["summary"] = {}
    for stage in ("raw", "pipeline"):
        rows = [r for r in output[stage] if r["category"] != "blank"]
        output["summary"][stage] = {"n": len(rows), "correct": sum(all(run["correct"] for run in r["runs"]) for r in rows),
            "median_ms": statistics.median(run["elapsed_ms"] for r in rows for run in r["runs"])}
    # Exercise the real BGR TimedOCR path plus compatibility/filter rules.
    # Never construct the real deploy config (it writes deploy.yaml) or logger.
    import gc
    from types import SimpleNamespace
    from unittest.mock import Mock
    if hasattr(model, "close"):
        model.close()
    model = None
    gc.collect()
    if args.skip_tests:
        output["pytest_skipped"] = True
        output["complete"] = True
        save()
        print(json.dumps(output["summary"]), flush=True)
        return
    sys.modules["module.logger"] = SimpleNamespace(logger=Mock())
    from module.server.setting import State
    State.deploy_config = SimpleNamespace(OcrBackend="rapid-small", UseOcrServer=False)
    os.environ.pop("OAS_OCR_BACKEND", None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import pytest
    tests = ["tests/test_rapidocr_adapter.py", "tests/test_ocr_filter.py",
             "tests/test_draft_duel_ocr.py", "tests/test_draft_duel_admission_ocr.py"]
    result = pytest.main([str(ROOT/path) for path in tests] + ["-q", "-p", "no:cacheprovider",
                         "--basetemp", str(args.output/"pytest-temp")])
    output["pytest_exit_code"] = int(result)
    output["complete"] = result == 0
    save()
    print(json.dumps(output["summary"]), flush=True)
    if result:
        raise SystemExit(int(result))


if __name__ == "__main__":
    main()
