"""Exercise bounded startup recovery without touching live accounts."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import cv2
import numpy as np


class StartupFrameTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'module/device/startup_frame.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.now = 0
        def sleep(seconds):
            self.now += seconds
        self.env = dict(time=SimpleNamespace(monotonic=lambda: self.now, sleep=sleep),
                        cv2=cv2, np=np, AdbError=OSError,
                        RequestHumanTakeover=RuntimeError, logger=Mock())
        exec(compile(tree, str(path), 'exec'), self.env)
        self.device = Mock(_startup_frame_pending=True)
        self.device.emulator_start.return_value = True
        self.launch = Mock()

    def capture(self, color):
        return cv2.imencode('.png', np.full((720, 1280, 3), color, np.uint8))[1].tobytes()

    def test_black_waits_full_minute(self):
        self.device.adb_shell.return_value = self.capture(0)
        self.assertFalse(self.env['wait_for_frame'](self.device))
        self.assertEqual(self.now, 60)

    def test_slow_start_recovers_without_restart(self):
        self.device.adb_shell.side_effect = [self.capture(0)] * 25 + [self.capture(50)]
        self.env['ensure_startup_frame'](self.device, self.launch)
        self.assertEqual(self.now, 50)
        self.device.emulator_start.assert_not_called()

    def test_one_restart_then_success(self):
        self.env['wait_for_frame'] = Mock(side_effect=[False, True])
        self.env['ensure_startup_frame'](self.device, self.launch)
        self.device.emulator_start.assert_called_once_with(max_attempts=1)
        self.launch.assert_called_once()
        self.assertFalse(self.device._startup_frame_pending)

    def test_second_black_stops_and_does_not_reset_budget(self):
        self.env['wait_for_frame'] = Mock(return_value=False)
        with self.assertRaises(RuntimeError):
            self.env['ensure_startup_frame'](self.device, self.launch)
        self.env['ensure_startup_frame'](self.device, self.launch)
        self.device.emulator_start.assert_called_once()

    def test_normal_running_capture_never_restarts(self):
        self.device._startup_frame_pending = False
        self.env['ensure_startup_frame'](self.device, self.launch)
        self.device.adb_shell.assert_not_called()
        self.device.emulator_start.assert_not_called()

    def test_failed_restart_does_not_launch_game(self):
        self.env['wait_for_frame'] = Mock(return_value=False)
        self.device.emulator_start.return_value = False
        with self.assertRaises(RuntimeError):
            self.env['ensure_startup_frame'](self.device, self.launch)
        self.launch.assert_not_called()
        self.assertFalse(self.device._startup_frame_pending)

    def test_broken_capture_is_bounded(self):
        self.device.adb_shell.side_effect = OSError('not ready')
        self.assertFalse(self.env['wait_for_frame'](self.device))
        self.assertEqual(self.now, 60)


if __name__ == '__main__':
    unittest.main()
