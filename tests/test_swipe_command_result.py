import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SwipeRule:
    name = 'search'

    def coord(self):
        return 100, 150, 400, 150


class SwipeResultTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'tasks/base_task.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'swipe')
        self.timer = Mock(limit=3)
        ns = {'RuleSwipe': SwipeRule, 'Timer': Mock(return_value=self.timer)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'swipe', 'exec'), ns)
        self.swipe = ns['swipe']
        self.h = SimpleNamespace(interval_timer={'search': self.timer}, device=Mock())

    def test_interval_skip_is_not_a_command(self):
        self.timer.reached.return_value = False
        self.assertFalse(self.swipe(self.h, SwipeRule(), interval=3))
        self.h.device.swipe.assert_not_called()

    def test_command_without_motion_feedback_counts(self):
        self.timer.reached.return_value = True
        self.h.device.swipe.return_value = None
        self.assertTrue(self.swipe(self.h, SwipeRule(), interval=3))
        self.h.device.swipe.assert_called_once()
        self.timer.reset.assert_called_once()

    def test_command_error_propagates(self):
        self.h.device.swipe.side_effect = RuntimeError('transport failed')
        with self.assertRaises(RuntimeError):
            self.swipe(self.h, SwipeRule())

    def test_six_sent_commands_trigger_existing_recovery(self):
        tree = ast.parse((ROOT / 'tasks/WantedQuests/explore.py').read_text(encoding='utf-8-sig'))
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and isinstance(n.test, ast.Call) and isinstance(n.test.func, ast.Attribute)
                      and n.test.func.attr == 'swipe')
        # Run the production recovery block in a bounded wrapper.
        wrapper = ast.parse('def run(self):\n search_fail_cnt = 0\n for _ in range(20):\n  pass').body[0]
        wrapper.body[1].body = [branch]
        ns = {'logger': Mock(), 'GameStuckError': RuntimeError}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     'recovery', 'exec'), ns)
        h = SimpleNamespace(device=self.h.device, interval_timer=self.h.interval_timer,
                            S_SWIPE_BACKGROUND_RIGHT=SwipeRule(), _match_end=Mock(),
                            quit_explore=Mock(), ui_click=Mock(return_value=True),
                            I_UI_BACK_YELLOW=object(), I_CHECK_EXPLORATION=object())
        self.timer.reached.side_effect = [False, True] * 6
        h.swipe = lambda rule, interval: self.swipe(h, rule, interval)
        ns['run'](h)
        self.assertEqual(h.device.swipe.call_count, 6)
        h.quit_explore.assert_called_once()
        h.ui_click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
