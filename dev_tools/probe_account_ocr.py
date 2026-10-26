"""Check one account's next-start OCR choice without importing or running a task."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--abrupt-exit", action="store_true")
    args = parser.parse_args()
    import psutil
    process = psutil.Process()
    process.nice(psutil.IDLE_PRIORITY_CLASS)
    process.cpu_affinity([process.cpu_affinity()[-1]])
    os.environ["OAS_ACCOUNT_NAME"] = args.account
    os.environ.pop("OAS_OCR_BACKEND", None)
    import cv2
    import numpy as np
    from module.ocr.backend import configured_text_system
    from module.ocr.isolated_rapid import IsolatedRapidTextSystem
    image = cv2.imdecode(np.frombuffer(args.image.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    model = configured_text_system(SimpleNamespace(OcrBackend="legacy"))
    if not isinstance(model, IsolatedRapidTextSystem):
        raise RuntimeError("Account did not select isolated rapid-small")
    try:
        text, score = model.ocr_single_line(image)
        result = {"account": args.account, "backend": model.backend, "text": text,
                  "score": score, "worker_pid": model._rapid.process.pid,
                  "application_numpy": np.__version__, "task_started": False}
        assert text == "萤草", result
        if args.abrupt_exit:
            print(json.dumps(result, ensure_ascii=True), flush=True)
            os._exit(0)  # Exercise this probe's own worker parent-exit cleanup.
    finally:
        model.close()
    result["worker_closed"] = model._rapid.process.poll() is not None
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
