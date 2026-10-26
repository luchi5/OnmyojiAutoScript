"""Private child entry point. No sockets, game, State, or production logger."""
import os
from pathlib import Path
import pickle
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main():
    import psutil
    parent = psutil.Process(int(sys.argv[1]))
    parent_created = parent.create_time()
    def watch_parent():
        while True:
            try:
                if not parent.is_running() or parent.create_time() != parent_created:
                    os._exit(0)
            except psutil.NoSuchProcess:
                os._exit(0)
            time.sleep(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # Library output cannot corrupt the private protocol.
    model = None
    while True:
        try:
            operation, payload = pickle.load(source)
        except EOFError:
            return
        try:
            if operation == "init" and model is None:
                import cv2
                cv2.setNumThreads(1)
                from module.ocr.rapidocr_adapter import RapidTextSystem
                model = RapidTextSystem(**payload)
                result = {"backend": "rapid-small", "pid": os.getpid()}
            elif operation == "recognize" and model is not None:
                value = model._rapid.recognize_txt(payload)
                result = {"txts": value.txts, "scores": value.scores}
            elif operation == "detect" and model is not None:
                image, kwargs = payload
                value = model._rapid(image, **kwargs)
                result = {"boxes": value.boxes}
            elif operation == "params" and model is not None:
                model._rapid.update_params(**payload)
                result = None
            else:
                raise ValueError(f"Unexpected worker operation: {operation}")
            response = (True, result)
        except Exception as exc:
            response = (False, f"{type(exc).__name__}: {exc}")
        pickle.dump(response, destination, protocol=4)
        destination.flush()


if __name__ == "__main__":
    main()
