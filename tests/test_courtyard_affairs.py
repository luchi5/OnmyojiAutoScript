"""Independent courtyard scheduling and bounded UI handling, with no ADB."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from module.exception import TaskEnd
from tasks.Component.CourtyardAffairs.courtyard_affairs import CourtyardAffairsMixin
from tasks.CourtyardAffairs.script_task import ScriptTask
from tasks.Restart.login import LoginHandler


class Frames(CourtyardAffairsMixin):
    def __init__(self, frames, entry=True):
        self.frames = list(frames)
        self.current = set()
        self.clicks = []
        for name in ('I_NOTE', 'I_PAGE', 'I_NO_TASKS', 'I_HARVEST_SOUL_2',
                     'I_HARVEST_SOUL_3', 'I_UI_AWARD', 'I_CONFIRM', 'I_DAILY',
                     'I_SUCCESS_CLAIMED', 'I_SKIP', 'I_LOGIN_RED_CLOSE', 'I_COMPLETE_TASKS'):
            setattr(self, name, name)
        self.ui_click_multi_scale = Mock(return_value=entry)

    def screenshot(self):
        self.current = set(self.frames.pop(0)) if self.frames else set()

    def appear(self, name):
        return name in self.current

    def appear_then_click(self, name, interval=None):
        if name in self.current:
            self.clicks.append((name, interval))
            return True
        return False

    def ui_reward_appear_click(self):
        return 'reward' in self.current


class CourtyardAffairsTests(unittest.TestCase):
    def timer(self, reached=False):
        timer = Mock()
        timer.start.return_value = timer
        timer.reached.return_value = reached
        return timer

    def test_registered_as_disabled_separate_daily_task(self):
        model = ConfigModel()
        self.assertFalse(model.courtyard_affairs.scheduler.enable)
        self.assertIn('CourtyardAffairs', ConfigMenu().menu['Daily Task'])
        args = model.script_task('CourtyardAffairs')
        self.assertIn('scheduler', args)
        self.assertIn('courtyard_affairs_config', args)
        old_args = model.script_task('Restart')['harvest_config']
        self.assertNotIn('enable_courtyard_affairs', {row['name'] for row in old_args})
        self.assertTrue(model.restart.harvest_config.enable_courtyard_affairs)

    def test_completion_requires_confirmed_empty_tasks(self):
        task = Frames([{'I_COMPLETE_TASKS'}, {'reward'}, {'I_NO_TASKS'}])
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer()):
            self.assertTrue(task.harvest_courtyard_affairs())
        self.assertEqual(task.clicks, [('I_COMPLETE_TASKS', 2.3)])

    def test_missing_entry_does_not_click_or_report_success(self):
        task = Frames([], entry=False)
        self.assertFalse(task.harvest_courtyard_affairs())
        self.assertFalse(task.clicks)

    def test_timeout_leaves_unfinished_and_does_not_complete_click(self):
        task = Frames([{'I_COMPLETE_TASKS'}])
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer(True)):
            self.assertFalse(task.harvest_courtyard_affairs())
        self.assertFalse(task.clicks)

    def test_click_limit_stops_retry_without_false_completion(self):
        task = Frames([{'I_COMPLETE_TASKS'}] * 3)
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer()):
            self.assertFalse(task.harvest_courtyard_affairs(max_complete_clicks=2))
        self.assertEqual(len(task.clicks), 2)

    def test_failure_and_success_schedule_independently(self):
        for success in (False, True):
            task = SimpleNamespace(
                config=SimpleNamespace(courtyard_affairs=SimpleNamespace(
                    courtyard_affairs_config=SimpleNamespace(timeout_seconds=60, max_complete_clicks=3))),
                configure_courtyard_skin=Mock(),
                goto_page=Mock(), harvest_courtyard_affairs=Mock(return_value=success), set_next_run=Mock())
            with self.subTest(success=success), self.assertRaises(TaskEnd):
                ScriptTask.run(task)
            self.assertEqual(task.goto_page.call_count, 2)
            task.set_next_run.assert_called_once_with('CourtyardAffairs', success=success,
                                                     finish=success, server=success)

    def test_login_never_runs_affairs_even_with_legacy_switch_enabled(self):
        task = SimpleNamespace(
            config=SimpleNamespace(restart=SimpleNamespace(harvest_config=SimpleNamespace(
                enable_courtyard_affairs=True, enable_mail=False))),
            device=SimpleNamespace(screenshot=Mock(), check_screen_size_sample=Mock(return_value=True)),
            _burst=Mock(), ui_reward_appear_click=Mock(return_value=False),
            appear_then_click=Mock(return_value=False), appear=Mock(return_value=False),
            harvest_courtyard_affairs=Mock(), harvest_mail=Mock(),
        )
        names = ('I_UI_AWARD', 'I_HARVEST_CHAT_CLOSE', 'I_LOGIN_YELLOW_CLOSE',
                 'I_HARVEST_BACK_PET_HOUSE', 'I_UI_CONFIRM_SAMLL', 'I_HARVEST_ZIDU',
                 'I_HARVEST_JADE', 'I_HARVEST_SIGN', 'I_HARVEST_SIGN_2', 'I_HARVEST_SIGN_3',
                 'I_HARVEST_SIGN_4', 'I_HARVEST_SIGN_999', 'I_HARVEST_AP', 'I_HARVEST_SOUL',
                 'I_HARVEST_GUILD_REWARD', 'I_HARVEST_SOUL_1', 'I_LOGIN_RED_CLOSE')
        for name in names:
            setattr(task, name, name)
        timer = self.timer(True)
        timer.started.return_value = True
        with patch('tasks.Restart.login.Timer', return_value=timer):
            LoginHandler.harvest(task)
        task.harvest_courtyard_affairs.assert_not_called()
        task.harvest_mail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
