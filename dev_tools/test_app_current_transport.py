import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

source = Path(__file__).resolve().parents[1] / 'module/device/app_control.py'
cls = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n, ast.ClassDef))
fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'app_is_running')
ns = {'logger': Mock()}
exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), ns)

class TransportTests(unittest.TestCase):
    def harness(self, method, foreground='game'):
        return SimpleNamespace(config=SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method=method))),
                               package='game', app_current_adb=Mock(return_value=foreground),
                               app_current_uiautomator2=Mock(side_effect=RuntimeError('ATX unavailable')))

    def test_minitouch_and_scrcpy_do_not_contact_atx(self):
        for method in ('minitouch', 'scrcpy', 'adb'):
            h = self.harness(method, ' game\r\n')
            self.assertTrue(ns['app_is_running'](h))
            h.app_current_adb.assert_called_once()
            h.app_current_uiautomator2.assert_not_called()

    def test_launcher_is_not_running_game(self):
        self.assertFalse(ns['app_is_running'](self.harness('minitouch', 'launcher')))

    def test_real_adb_failure_is_not_hidden(self):
        h = self.harness('minitouch')
        h.app_current_adb.side_effect = RuntimeError('device offline')
        with self.assertRaises(RuntimeError): ns['app_is_running'](h)

    def test_explicit_uiautomator_keeps_existing_transport(self):
        h = self.harness('uiautomator2')
        h.app_current_uiautomator2.side_effect = None
        h.app_current_uiautomator2.return_value = 'game'
        self.assertTrue(ns['app_is_running'](h))
        h.app_current_adb.assert_not_called()

if __name__ == '__main__': unittest.main()
