"""Window presentation cannot hold up account scheduling; no real UI calls."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest


class WindowTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'module/device/platform2/platform_windows.py'
        names = {'find_hwnd_by_name', 'show_window_by_name', 'minimize_by_name', 'minimize_window'}
        tree = ast.parse(source.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.api = Mock()
        self.api.ShowWindow.side_effect = AssertionError('Synchronous window operation')
        self.env = {'ctypes': SimpleNamespace(windll=SimpleNamespace(user32=self.api)),
                    'logger': Mock()}
        exec(compile(tree, str(source), 'exec'), self.env)

    def test_blank_names_never_enumerate_or_touch_windows(self):
        for name in ('', '   ', None):
            self.assertIsNone(self.env['find_hwnd_by_name'](name))
            self.env['show_window_by_name'](name)
            self.env['minimize_by_name'](name)
        self.assertEqual(self.api.mock_calls, [])

    def test_show_queues_request_without_focus_or_sync_wait(self):
        self.env['find_hwnd_by_name'] = Mock(return_value=123)
        self.env['show_window_by_name']('06-test')
        self.api.ShowWindowAsync.assert_called_once_with(123, 8)
        self.api.SetForegroundWindow.assert_not_called()

    def test_missing_window_is_noop(self):
        self.env['find_hwnd_by_name'] = Mock(return_value=None)
        self.env['show_window_by_name']('missing')
        self.assertEqual(self.api.mock_calls, [])

    def test_minimize_is_async(self):
        self.env['minimize_window'](123)
        self.api.ShowWindowAsync.assert_called_once_with(123, 6)


if __name__ == '__main__':
    unittest.main()
