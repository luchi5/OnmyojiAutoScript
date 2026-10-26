"""Private stdio worker: old application packages never load new ORT/NumPy DLLs."""
import atexit
import os
from pathlib import Path
import pickle
import subprocess
import threading
from types import SimpleNamespace

from module.ocr.rapidocr_adapter import RapidTextSystem, _Detector, _Recognizer


class _WorkerClient:
    def __init__(self, python, kwargs):
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        root = Path(__file__).resolve().parents[2]
        self._lock = threading.RLock()
        self._closed = False
        self.process = subprocess.Popen(
            [str(python), "-B", str(Path(__file__).with_name("rapid_worker.py")), str(os.getpid())],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True)
        try:
            self.call("init", kwargs)
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def call(self, operation, payload):
        with self._lock:
            if self._closed or self.process.poll() is not None:
                raise RuntimeError("Isolated rapid-small worker is not running")
            timed_out = threading.Event()
            def expire():
                timed_out.set()
                try:
                    self.process.terminate()
                except OSError:
                    pass
            timer = threading.Timer(60, expire)
            timer.daemon = True
            timer.start()
            try:
                pickle.dump((operation, payload), self.process.stdin, protocol=4)
                self.process.stdin.flush()
                ok, result = pickle.load(self.process.stdout)
                if not ok:
                    raise RuntimeError(f"Isolated rapid-small failed: {result}")
                return result
            except (EOFError, OSError, pickle.UnpicklingError) as exc:
                cause = "timed out" if timed_out.is_set() else "disconnected"
                self.close()
                raise RuntimeError(f"Isolated rapid-small worker {cause}") from exc
            finally:
                timer.cancel()

    def recognize_txt(self, images):
        return SimpleNamespace(**self.call("recognize", images))

    def __call__(self, image, **kwargs):
        return SimpleNamespace(**self.call("detect", (image, kwargs)))

    def update_params(self, **kwargs):
        self.call("params", kwargs)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            atexit.unregister(self.close)


class IsolatedRapidTextSystem(RapidTextSystem):
    """Same hooks/crops as the verified adapter, only inference runs separately."""
    def __init__(self, python, **kwargs):
        self.input_color = kwargs.get("input_color", "RGB")
        self.threads = kwargs.get("threads", 1)
        self.use_angle_cls = False
        self._lock = threading.RLock()
        self._rapid = _WorkerClient(python, kwargs)
        self.text_detector = _Detector(self, kwargs.get("box_thresh", .6), kwargs.get("unclip_ratio", 1.6))
        self.text_recognizer = _Recognizer(self)

    def close(self):
        self._rapid.close()
