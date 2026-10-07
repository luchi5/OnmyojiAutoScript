"""Offline weekly-task opening regressions using AST methods and synthetic frames.

No logger/config/device imports, live accounts, files or processes are accessed.
Run: toolkit/python.exe -B tests/test_weekly_guild_opening.py
"""
import ast
import copy
from datetime import datetime, time as clock_time, timedelta
import importlib.util
import itertools
from pathlib import Path
import sys
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'isolated_weekly_guild_calendar', ROOT / 'tasks/Component/guild_retry_schedule.py')
CALENDAR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CALENDAR
SPEC.loader.exec_module(CALENDAR)


class TaskEnd(Exception):
    pass


class Clock(datetime):
    current = datetime(2026, 10, 7, 20)

    @classmethod
    def now(cls):
        return cls.current


class FakeTimer:
    def __init__(self, seconds):
        pass

    def start(self):
        return self

    def reached(self):
        return True

    def reset(self):
        pass


def task_class(task_name):
    source = ROOT / 'tasks' / task_name / 'script_task.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
    names = {'run', '_opening_plan', '_schedule_opening_check'}
    if task_name == 'GuildBanquet':
        names.update({'get_key_from_value', 'check_runtime', 'plan_next_run'})
    else:
        names.add('goto_demon_retreat')
    selected = copy.deepcopy(original)
    selected.bases = []
    selected.body = [n for n in selected.body if isinstance(n, ast.FunctionDef) and n.name in names]
    logger = Mock()
    ticks = itertools.count(100, 11)
    environment = {
        'datetime': Clock, 'timedelta': timedelta, 'logger': logger,
        'plan_opening_check': CALENDAR.plan_opening_check,
        'TaskEnd': TaskEnd, 'DemonRetreat': object, 'Timer': FakeTimer,
        'time': S(time=lambda: next(ticks)), 'sleep': Mock(),
        'page_main': 'main', 'page_guild': 'guild', 'page_shikigami_records': 'records',
        'WEEKDAYDICT': {0: '星期一', 1: '星期二', 2: '星期三', 3: '星期四',
                        4: '星期五', 5: '星期六', 6: '星期日'},
    }
    module = ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[]))
    exec(compile(module, str(source), 'exec'), environment)
    return environment['ScriptTask'], environment


def fake_task(task_name, now):
    cls, env = task_class(task_name)
    Clock.current = datetime.fromisoformat(now)
    task = cls()
    scheduler = S(failure_interval=timedelta(minutes=5), server_update=clock_time(20), delay_date=1)
    if task_name == 'GuildBanquet':
        configured = S(day_1=S(value='星期三'), run_time_1=clock_time(20),
                       day_2=S(value='星期六'), run_time_2=clock_time(20),
                       auto_switch_shikigami=True)
        task.config = S(guild_banquet=S(guild_banquet_time=configured, scheduler=scheduler))
        task.I_FLAG = 'banquet_flag'
        task.check_full_experience = Mock()
        task.set_config = Mock(side_effect=AssertionError('must retain configured opening times'))
    else:
        task.config = S(demon_retreat=S(demon_retreat_time=S(custom_run_time=clock_time(10)),
                                      scheduler=scheduler,
                                      switch_soul_config=S(enable=False, enable_switch_by_name=False)))
        for item in ('HUNT_CHECK', 'DEMON_GATHER', 'QUIT_BACK', 'SHRINE', 'HUNT',
                     'REWARD_ALL', 'DEMON_BACK_CHECK', 'RANK_LSIT', 'PRAY'):
            setattr(task, 'I_' + item, item.lower())
        task.is_in_prepare = Mock(return_value=False)
        task.goto_main = Mock()
        task.demon_retreat = Mock(return_value=True)
    task.device = Mock()
    task.screenshot = Mock()
    task.goto_page = Mock()
    task.appear = Mock(return_value=False)
    task.appear_then_click = Mock(return_value=False)
    task.set_next_run = Mock()
    task.custom_next_run = Mock(side_effect=AssertionError('must use an explicit server=False target'))
    return task, env


class WeeklyOpeningTests(unittest.TestCase):
    def assert_target(self, task, name, target):
        task.set_next_run.assert_called_once_with(
            task=name, server=False, target=datetime.fromisoformat(target))
        task.custom_next_run.assert_not_called()

    def test_banquet_before_start_does_not_enter_game(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T19:55:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-07T20:00:00')
        task.goto_page.assert_not_called()
        task.appear.assert_not_called()

    def test_banquet_on_non_event_day_uses_earliest_slot(self):
        task, _ = fake_task('GuildBanquet', '2026-10-08T10:00:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-10T20:00:00')
        task.goto_page.assert_not_called()

    def test_banquet_not_open_uses_configured_interval_without_server_override(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-07T20:15:00')
        task.set_config.assert_not_called()

    def test_banquet_last_check_is_clamped_to_deadline(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:59:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-07T21:00:00')

    def test_banquet_deadline_does_not_make_a_late_game_check(self):
        task, env = fake_task('GuildBanquet', '2026-10-07T21:00:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-10T20:00:00')
        task.goto_page.assert_not_called()
        env['logger'].warning.assert_called_once()
        self.assertIn('without confirmed completion', env['logger'].warning.call_args.args[0])

    def test_banquet_two_times_on_same_day_still_chooses_today(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T21:10:00')
        configured = task.config.guild_banquet.guild_banquet_time
        configured.day_2 = S(value='星期三')
        configured.run_time_2 = clock_time(22)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-07T22:00:00')

    def test_banquet_already_entered_completes_after_deadline_and_keeps_user_times(self):
        task, env = fake_task('GuildBanquet', '2026-10-07T20:59:00')
        task.appear.side_effect = [True, True, False]
        task.screenshot.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 7, 21, 2))
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-10T20:00:00')
        task.check_full_experience.assert_called_once()
        task.set_config.assert_not_called()
        self.assertEqual(task.config.guild_banquet.guild_banquet_time.run_time_1, clock_time(20))
        self.assertTrue(any(call.args[0].startswith('Guild banquet completed;')
                            for call in env['logger'].info.call_args_list))

    def test_banquet_timeout_inside_window_is_not_confirmed_as_complete(self):
        task, env = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        task.appear.return_value = True
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-07T20:15:00')
        self.assertFalse(any(call.args[0].startswith('Guild banquet completed;')
                             for call in env['logger'].info.call_args_list))

    def test_banquet_timeout_after_window_is_logged_as_unconfirmed(self):
        task, env = fake_task('GuildBanquet', '2026-10-07T20:59:00')
        task.appear.return_value = True
        task.screenshot.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 7, 21, 2))
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'GuildBanquet', '2026-10-10T20:00:00')
        self.assertIn('without confirmed completion', env['logger'].warning.call_args.args[0])

    def test_retreat_saturday_before_start_retains_today(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T09:55:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-10T10:00:00')
        task.goto_page.assert_not_called()

    def test_retreat_sunday_schedules_next_saturday(self):
        task, _ = fake_task('DemonRetreat', '2026-10-11T10:00:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
        task.goto_page.assert_not_called()

    def test_retreat_at_deadline_does_not_enter_game(self):
        task, env = fake_task('DemonRetreat', '2026-10-10T11:00:00')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
        task.goto_page.assert_not_called()
        self.assertIn('without confirmed completion', env['logger'].warning.call_args.args[0])

    def test_retreat_unavailable_entry_retries_from_current_time(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:10:00')
        task.goto_demon_retreat = Mock(return_value=False)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-10T10:15:00')
        task.demon_retreat.assert_not_called()

    def test_retreat_failed_entry_near_end_is_clamped(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        task.goto_demon_retreat = Mock(return_value=False)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-10T11:00:00')

    def test_retreat_entry_that_runs_past_deadline_advances_without_success(self):
        task, env = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        def not_open(opening_deadline=None):
            Clock.current = datetime(2026, 10, 10, 11, 2)
            return False
        task.goto_demon_retreat = Mock(side_effect=not_open)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
        self.assertIn('without confirmed completion', env['logger'].warning.call_args.args[0])

    def test_retreat_entered_battle_can_finish_past_deadline(self):
        task, env = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        task.goto_demon_retreat = Mock(return_value=True)
        def finished():
            Clock.current = datetime(2026, 10, 10, 11, 2)
            return True
        task.demon_retreat.side_effect = finished
        task.appear.side_effect = lambda item: item == task.I_RANK_LSIT
        task.appear_then_click.side_effect = lambda item, **kw: item == task.I_DEMON_BACK_CHECK
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
        task.demon_retreat.assert_called_once()
        self.assertTrue(any(call.args[0].startswith('Demon retreat completed;')
                            for call in env['logger'].info.call_args_list))

    def test_retreat_battle_failure_inside_window_retries(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:10:00')
        task.goto_demon_retreat = Mock(return_value=True)
        task.demon_retreat.return_value = False
        task.appear.side_effect = lambda item: item == task.I_RANK_LSIT
        task.appear_then_click.side_effect = lambda item, **kw: item == task.I_DEMON_BACK_CHECK
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-10T10:15:00')

    def test_retreat_navigation_stops_at_deadline_before_new_click(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        task.screenshot.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 10, 11))
        self.assertFalse(task.goto_demon_retreat())
        task.appear_then_click.assert_not_called()

    def test_retreat_navigation_accepts_entered_gathering_after_deadline(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        task.screenshot.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 10, 11, 2))
        task.appear.side_effect = lambda item: item == task.I_DEMON_GATHER
        self.assertTrue(task.goto_demon_retreat())
        task.appear_then_click.assert_called_once_with(task.I_QUIT_BACK, interval=1)

    def test_retreat_real_entry_keeps_original_deadline_after_switch_soul_crosses_it(self):
        task, env = fake_task('DemonRetreat', '2026-10-10T10:59:00')
        switch = task.config.demon_retreat.switch_soul_config
        switch.enable = True
        switch.switch_group_team = '1,1'
        task.run_switch_soul = Mock(side_effect=lambda _team:
                                   setattr(Clock, 'current', datetime(2026, 10, 10, 11, 2)))
        task._opening_plan = Mock(wraps=task._opening_plan)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
        task.demon_retreat.assert_not_called()
        # Only run's cleanup may click Back; entry must not click Shrine or Hunt.
        self.assertTrue(all(call.args[0] == task.I_DEMON_BACK_CHECK
                            for call in task.appear_then_click.call_args_list))
        task.screenshot.assert_called_once()
        self.assertEqual(task._opening_plan.call_count, 2)
        self.assertTrue(any('did not open before the check deadline' in call.args[0]
                            for call in env['logger'].warning.call_args_list))

    def test_retreat_real_entry_allows_entered_gathering_or_preparation_after_switch_deadline(self):
        for in_prepare in (False, True):
            with self.subTest(in_prepare=in_prepare):
                task, env = fake_task('DemonRetreat', '2026-10-10T10:59:00')
                switch = task.config.demon_retreat.switch_soul_config
                switch.enable = True
                switch.switch_group_team = '1,1'
                task.run_switch_soul = Mock(side_effect=lambda _team:
                                           setattr(Clock, 'current', datetime(2026, 10, 10, 11, 2)))
                task.is_in_prepare.return_value = in_prepare
                task.appear.side_effect = lambda item: (
                    item == task.I_RANK_LSIT
                    or (not in_prepare and item == task.I_DEMON_GATHER)
                )
                task.appear_then_click.side_effect = lambda item, **kw: item == task.I_DEMON_BACK_CHECK
                with self.assertRaises(TaskEnd):
                    task.run()
                task.demon_retreat.assert_called_once()
                self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')
                self.assertTrue(any(call.args[0].startswith('Demon retreat completed;')
                                    for call in env['logger'].info.call_args_list))

    def test_retreat_direct_entry_after_deadline_does_not_use_next_week_deadline(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T11:02:00')
        self.assertFalse(task.goto_demon_retreat())
        task.appear_then_click.assert_not_called()
        task.screenshot.assert_called_once()

    def test_retreat_already_claimable_reward_schedules_next_week_explicitly(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:10:00')
        task.appear_then_click.side_effect = lambda item, **kw: item == task.I_REWARD_ALL
        with self.assertRaises(TaskEnd):
            task.goto_demon_retreat()
        self.assert_target(task, 'DemonRetreat', '2026-10-17T10:00:00')


if __name__ == '__main__':
    unittest.main()
