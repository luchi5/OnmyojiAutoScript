"""Space emulator launches across worker processes without stopping any worker."""
import time
from pathlib import Path


def wait_for_launch_slot(path=None, interval=10, timeout=180):
    import msvcrt

    path = Path(path) if path else Path(__file__).resolve().parents[3] / 'log' / 'emulator-launch.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for emulator launch slot')
                time.sleep(0.25)
        try:
            # mtime is shared between workers and remains valid after a worker exits.
            remaining = interval - (time.time() - path.stat().st_mtime)
            if remaining > 0:
                time.sleep(min(remaining, interval))
            path.touch()
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
