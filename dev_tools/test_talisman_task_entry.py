"""Task entry cannot depend on optional report settings or a reward red dot."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch

from tasks.Component.daily_feedback_capture_assets import DailyFeedbackCaptureAssets as A
from dev_tools import test_closeout_integration as integration

ROOT = Path(__file__).resolve().parents[1]


class Timer:
    def __init__(self, *args):
        self.checks = 0

    def start(self):
        return self

    def reached(self):
        self.checks += 1
        return self.checks > 12


def load_task():
    tree = ast.parse((ROOT / 'tasks/TalismanPass/script_task.py').read_text(encoding='utf-8'))
    node = deepcopy(next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask'))
    node.bases = []
    node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'enter_task_page']
    env = dict(Timer=Timer, logger=Mock())
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<task>', 'exec'), env)
    return env['ScriptTask']


TASK = load_task()


def runtime(state='rewards'):
    task = TASK()
    task.state = state
    task.I_UI_REWARD = 'popup'
    task.I_TP_GOTO = 'goto'
    task.I_TP_EXP = 'exp'
    task.device = S(sleep=Mock())
    task.screenshot = Mock()
    task.appear = lambda marker, **kw: (
        (marker == A.I_TALISMAN_HEADER and task.state in ('rewards', 'weekly', 'today'))
        or (marker == A.I_TALISMAN_TODAY_SELECTED and task.state == 'today')
        or (marker == 'exp' and task.state in ('weekly', 'today'))
        or (marker == 'popup' and task.state == 'popup'))
    def click(marker, **kw):
        if marker is A.C_TALISMAN_TASK_TAB:
            task.state = 'weekly'
        elif marker is A.C_TALISMAN_TODAY_TAB:
            task.state = 'today'
        return True
    task.click = Mock(side_effect=click)
    return task


class TaskEntryTests(unittest.TestCase):
    def test_rewards_without_red_dot_enters_tasks_and_today(self):
        task = runtime()
        with patch('tasks.Component.daily_feedback_capture._enabled', return_value=False):
            self.assertTrue(task.enter_task_page())
        self.assertEqual(task.state, 'today')
        self.assertEqual([c.args[0] for c in task.click.call_args_list],
                         [A.C_TALISMAN_TASK_TAB, A.C_TALISMAN_TODAY_TAB])

    def test_already_today_does_not_click(self):
        task = runtime('today')
        self.assertTrue(task.enter_task_page())
        task.click.assert_not_called()

    def test_weekly_requires_verified_today_selection(self):
        task = runtime('weekly')
        self.assertTrue(task.enter_task_page())
        task.click.assert_called_once_with(A.C_TALISMAN_TODAY_TAB, interval=1.5)

    def test_failed_today_click_is_not_proof_of_daily_page(self):
        task = runtime('weekly')
        task.click.side_effect = None
        task.click.return_value = True
        self.assertFalse(task.enter_task_page())
        self.assertLessEqual(task.click.call_count, 3)

    def test_unknown_page_never_clicks(self):
        task = runtime('unknown')
        self.assertFalse(task.enter_task_page())
        task.click.assert_not_called()

    def test_reward_overlay_never_clicks_through(self):
        task = runtime('popup')
        self.assertFalse(task.enter_task_page())
        task.click.assert_not_called()

    def test_stuck_reward_tab_is_bounded(self):
        task = runtime()
        task.click.side_effect = None
        task.click.return_value = True
        self.assertFalse(task.enter_task_page())
        self.assertEqual(task.click.call_count, 3)


class RecoveredCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.case = integration.CloseoutIntegrationTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_recovered_task_entry_finishes_auto_closeout_without_retry(self):
        self.case.queue()
        task = integration.talisman(self.case.config, page=False)
        task.enter_task_page = Mock(return_value=True)
        self.case.finish(task)
        task.enter_task_page.assert_called_once_with()
        self.assertIn(('collect',), task.actions)
        from tasks.Component.daily_closeout import closeout_state
        self.assertEqual(closeout_state(self.case.config, now=integration.Clock.value)['completed_date'], '2026-10-05')


if __name__ == '__main__':
    unittest.main()
