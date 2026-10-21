"""Execute production transport methods with fake devices; never touch live ADB."""
import ast
import asyncio
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class Takeover(Exception):
    pass


class Unavailable(Takeover):
    pass


class TransportError(Exception):
    pass


def load_methods(path, cls, names, ns):
    tree = ast.parse((ROOT / path).read_text('utf-8'))
    target = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    methods = [n for n in target.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for method in methods:
        method.decorator_list = []
    node = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), path, 'exec'), ns)
    return ns['Subject']()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock()
        self.clock.monotonic.return_value = 100
        self.ns = dict(time=self.clock, logger=Mock(), cv2=cv2, np=np,
                       NemuIpcUnavailable=Unavailable, RequestHumanTakeover=Takeover,
                       NemuIpcError=TransportError, AdbError=TransportError, asyncio=asyncio)
        self.frame = np.full((720, 1280, 3), 120, np.uint8)
        self.png = cv2.imencode('.png', self.frame)[1].tobytes()
        self.device = load_methods('module/device/screenshot.py', 'Screenshot',
                                   {'screenshot_nemu_with_fallback'}, self.ns)
        self.device.screenshot_nemu_ipc = Mock(return_value=self.frame)
        self.device.adb_shell = Mock(return_value=self.png)

    def test_fallback_preserves_rgb_channels(self):
        rgb = np.zeros((720, 1280, 3), np.uint8)
        rgb[:] = (231, 75, 19)
        self.device.screenshot_nemu_ipc.side_effect = Unavailable()
        self.device.adb_shell.return_value = cv2.imencode('.png', rgb[:, :, ::-1])[1].tobytes()
        actual = self.device.screenshot_nemu_with_fallback()
        np.testing.assert_array_equal(actual, rgb)

    def test_real_rank_pages_recover_both_decision_buttons(self):
        from PIL import Image
        from tasks.GameUi.assets import GameUiAssets
        from tasks.Chess.assets import ChessAssets
        for filename in ('chess_rank2_bgr.png', 'chess_rank6_bgr.png'):
            with self.subTest(filename=filename):
                wrong_rgb = np.array(Image.open(ROOT / 'dev_tools/fixtures' / filename).convert('RGB'))
                self.device._nemu_retry_at = 0
                self.device.screenshot_nemu_ipc.side_effect = Unavailable()
                # The incident array was BGR saved as RGB; encoding it with
                # OpenCV recreates the correctly colored ADB PNG input.
                self.device.adb_shell.return_value = cv2.imencode('.png', wrong_rgb)[1].tobytes()
                actual = self.device.screenshot_nemu_with_fallback()
                for rule in (GameUiAssets.I_CHESS_RANK_GOTO_LOBBY, ChessAssets.I_RESTART_AGAIN):
                    self.assertFalse(rule.match(wrong_rgb))
                    self.assertTrue(rule.match(actual), rule.name)

    def test_healthy_ipc_never_calls_adb(self):
        self.assertIs(self.device.screenshot_nemu_with_fallback(), self.frame)
        self.device.adb_shell.assert_not_called()

    def test_fallback_cooldown_then_recovers_ipc(self):
        self.device.screenshot_nemu_ipc.side_effect = [Unavailable(), self.frame]
        np.testing.assert_array_equal(self.device.screenshot_nemu_with_fallback(), self.frame)
        self.device.screenshot_nemu_with_fallback()
        self.assertEqual(self.device.screenshot_nemu_ipc.call_count, 1)
        self.clock.monotonic.return_value = 161
        self.assertIs(self.device.screenshot_nemu_with_fallback(), self.frame)
        self.assertEqual(self.device.screenshot_nemu_ipc.call_count, 2)

    def test_invalid_ipc_frames_use_adb(self):
        for frame in (None, np.zeros_like(self.frame), np.ones((10, 10, 3), np.uint8)):
            with self.subTest(frame=None if frame is None else frame.shape):
                self.device._nemu_retry_at = 0
                self.device.screenshot_nemu_ipc.return_value = frame
                np.testing.assert_array_equal(self.device.screenshot_nemu_with_fallback(), self.frame)

    def test_adb_failure_is_bounded(self):
        self.device.screenshot_nemu_ipc.side_effect = Unavailable()
        self.device.adb_shell.side_effect = TransportError('closed')
        with self.assertRaises(Takeover):
            self.device.screenshot_nemu_with_fallback()
        self.assertEqual(self.device.adb_shell.call_count, 15)
        self.assertEqual(self.clock.sleep.call_count, 14)

    def test_black_or_corrupt_adb_frames_fail_closed(self):
        for data in (b'bad PNG', cv2.imencode('.png', np.zeros_like(self.frame))[1].tobytes()):
            self.device.adb_shell.reset_mock()
            self.device.adb_shell.return_value = data
            self.device._nemu_retry_at = 200
            with self.assertRaises(Takeover):
                self.device.screenshot_nemu_with_fallback()
            self.assertEqual(self.device.adb_shell.call_count, 15)

    def test_slow_game_launch_recovers_after_initial_black_frames(self):
        self.device._nemu_retry_at = 200
        black = cv2.imencode('.png', np.zeros_like(self.frame))[1].tobytes()
        self.device.adb_shell.side_effect = [black] * 4 + [self.png]
        np.testing.assert_array_equal(self.device.screenshot_nemu_with_fallback(), self.frame)
        self.assertEqual(self.device.adb_shell.call_count, 5)

    def test_wait_stops_at_deadline(self):
        self.device._nemu_retry_at = 200
        self.clock.monotonic.side_effect = [100, 100, 146]
        self.device.adb_shell.side_effect = TransportError('timeout')
        with self.assertRaises(Takeover):
            self.device.screenshot_nemu_with_fallback()
        self.assertEqual(self.device.adb_shell.call_count, 1)

    def test_takeover_and_programming_errors_are_not_hidden(self):
        for error in (Takeover('account guard'), ValueError('bug')):
            self.device.screenshot_nemu_ipc.side_effect = error
            with self.assertRaises(type(error)):
                self.device.screenshot_nemu_with_fallback()
        self.device.adb_shell.assert_not_called()

    def connector(self, outcomes):
        ipc = SimpleNamespace(__enter__=Mock(side_effect=outcomes))
        self.ns['NemuIpcImpl'] = Mock(return_value=ipc)
        device = load_methods('module/device/method/nemu_ipc.py', 'NemuIpc',
                              {'_connect_nemu_ipc'}, self.ns)
        return device, ipc

    def test_initial_connection_recovers_after_closed_and_timeout(self):
        device, ipc = self.connector([TransportError(), asyncio.TimeoutError(), 'connected'])
        self.assertEqual(device._connect_nemu_ipc('folder', 2), 'connected')
        self.assertEqual(ipc.__enter__.call_count, 3)
        self.assertEqual([c.args[0] for c in self.clock.sleep.call_args_list], [2, 4])

    def test_initial_connection_failure_is_bounded(self):
        device, ipc = self.connector([TransportError()] * 4)
        with self.assertRaises(Unavailable):
            device._connect_nemu_ipc('folder', 2)
        self.assertEqual(ipc.__enter__.call_count, 4)

    def test_runtime_screenshot_error_allows_fallback_but_control_does_not(self):
        tree = ast.parse((ROOT / 'module/device/method/nemu_ipc.py').read_text('utf-8'))
        retry = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'retry')
        ns = dict(self.ns, wraps=wraps, RETRY_TRIES=3, retry_sleep=Mock(),
                  NemuIpcIncompatible=type('Incompatible', (Exception,), {}))
        exec(compile(ast.Module(body=[retry], type_ignores=[]), '<retry>', 'exec'), ns)
        for name, error_type in [('screenshot', Unavailable), ('down', Takeover)]:
            def failing(self):
                raise TransportError('closed')
            failing.__name__ = name
            with self.assertRaises(error_type) as raised:
                ns['retry'](failing)(SimpleNamespace(reconnect=Mock()))
            self.assertIs(type(raised.exception), error_type)


if __name__ == '__main__':
    unittest.main()
