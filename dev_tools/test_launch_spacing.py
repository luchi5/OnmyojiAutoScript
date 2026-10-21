"""Real process lock tests using temporary files, without launching emulators."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'module/device/platform2/launch_spacing.py'
spec = importlib.util.spec_from_file_location('spacing', SOURCE)
spacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spacing)


@unittest.skipUnless(sys.platform == 'win32', 'Windows process locks')
class SpacingTests(unittest.TestCase):
    def test_independent_processes_are_spaced(self):
        code = ('import runpy,sys,time; f=runpy.run_path(sys.argv[1])["wait_for_launch_slot"]; '
                'f(sys.argv[2], interval=0.5); print(time.time())')
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'launch.lock')
            workers = [subprocess.Popen([sys.executable, '-B', '-c', code, str(SOURCE), path],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                       for _ in range(2)]
            stamps = []
            for worker in workers:
                out, err = worker.communicate(timeout=10)
                self.assertEqual(worker.returncode, 0, err)
                stamps.append(float(out))
            self.assertGreaterEqual(abs(stamps[0] - stamps[1]), 0.45)

    def test_busy_lock_times_out_and_can_be_reused(self):
        import msvcrt
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'launch.lock'
            path.write_bytes(b'0')
            with path.open('r+b') as handle:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    with self.assertRaises(TimeoutError):
                        spacing.wait_for_launch_slot(path, interval=0, timeout=0)
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            spacing.wait_for_launch_slot(path, interval=0)


if __name__ == '__main__':
    unittest.main()
