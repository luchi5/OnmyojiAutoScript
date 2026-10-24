"""Exercise the real retreat result loop without importing a device."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/DemonRetreat/script_task.py'


class VictoryTests(unittest.TestCase):
    def run_result(self, themed=False, default=False, failure=False):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'battle_wait')
        namespace = {'logger': Mock(), 'Timer': lambda _: Mock()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(SOURCE), 'exec'), namespace)
        rules = {key: object() for key in ('I_WIN', 'I_DEMON_DEFAULT_WIN', 'I_FALSE', 'I_PREPARE_HIGHLIGHT')}
        results = {rules['I_WIN']: themed, rules['I_DEMON_DEFAULT_WIN']: default,
                   rules['I_FALSE']: failure}
        harness = SimpleNamespace(**rules, device=Mock(), screenshot=Mock(),
                                  appear=Mock(side_effect=lambda rule, **kw: results.get(rule, False)),
                                  appear_then_click=Mock(return_value=False),
                                  ui_click_until_disappear=Mock())
        result = namespace['battle_wait'](harness, False)
        return result, harness

    def test_default_result_under_custom_theme(self):
        result, h = self.run_result(default=True)
        self.assertTrue(result)
        h.ui_click_until_disappear.assert_called_once_with(h.I_DEMON_DEFAULT_WIN)

    def test_themed_result_still_works(self):
        result, h = self.run_result(themed=True, default=True)
        self.assertTrue(result)
        h.ui_click_until_disappear.assert_called_once_with(h.I_WIN)
        self.assertEqual(h.appear.call_count, 1)

    def test_loss_does_not_become_victory(self):
        result, h = self.run_result(failure=True)
        self.assertFalse(result)
        h.ui_click_until_disappear.assert_called_once_with(h.I_FALSE)


if __name__ == '__main__':
    unittest.main()
