"""Exercise the actual result loop without a running device."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest


class CoinSettlementTests(unittest.TestCase):
    def run_loop(self, failure=False, active=False):
        source = Path(__file__).resolve().parents[1] / 'tasks/DemonEncounter/script_task.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'battle_wait')
        timer = Mock()
        timer.start.return_value = timer
        timer.reached.return_value = True
        ns = {'logger': Mock(), 'Timer': Mock(return_value=timer)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
        names = ('I_UI_CONFIRM_SAMLL', 'O_DE_BOSS_ESCAPED', 'I_DE_WIN',
                 'I_WIN', 'I_REWARD', 'I_FALSE', 'I_DE_SETTLEMENT_COIN')
        h = SimpleNamespace(**{n: n for n in names}, device=Mock(),
                            is_in_real_battle=Mock(return_value=active),
                            appear_then_click=Mock(return_value=False),
                            ui_click_until_disappear=Mock(), _finish_failed_settlement=Mock())
        frames = [0]
        def screenshot():
            frames[0] += 1
            if frames[0] > 3:
                raise TimeoutError('still waiting')
        h.screenshot = screenshot
        h.appear = lambda rule: ((rule == 'I_FALSE' and failure)
                                or (rule == 'I_DE_SETTLEMENT_COIN' and frames[0] == 1))
        try:
            result = ns['battle_wait'](h, False)
        except TimeoutError:
            result = 'waiting'
        return result, h

    def test_coin_result_clicked_and_returns(self):
        result, h = self.run_loop()
        self.assertTrue(result)
        h.ui_click_until_disappear.assert_called_once_with(h.I_DE_SETTLEMENT_COIN)

    def test_failure_takes_precedence(self):
        result, h = self.run_loop(failure=True)
        self.assertFalse(result)
        h.ui_click_until_disappear.assert_called_once_with(h.I_FALSE)

    def test_active_battle_does_not_click_coin(self):
        result, h = self.run_loop(active=True)
        self.assertEqual(result, 'waiting')
        h.ui_click_until_disappear.assert_not_called()


if __name__ == '__main__':
    unittest.main()
