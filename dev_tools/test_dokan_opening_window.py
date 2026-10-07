"""Offline Dokan scheduling and unknown-page regressions; no OCR or Device."""
from datetime import datetime, time, timedelta
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import (
    FakeClock, FakeLogger, FakeTimer, extract_methods,
)
from tasks.Component.guild_retry_schedule import plan_opening_check, retry_delay


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/Dokan/script_task.py'
BASE_TASK = ROOT / 'tasks/base_task.py'
CONFIG = ROOT / 'module/config/config.py'


class TaskEnd(Exception):
    pass


class AccountLoggedInElsewhere(Exception):
    pass


class MemoryLock:
    def __init__(self):
        self.held = False

    def acquire(self):
        assert not self.held
        self.held = True

    def release(self):
        assert self.held
        self.held = False


class Harness:
    """Execute actual task, BaseTask setter and Config scheduler from their AST."""
    def __init__(self, moment, server_time=time(19), weekdays_only=True,
                 remaining=2, daily_count=2, interval=timedelta(minutes=3)):
        self.now = datetime.fromisoformat(moment)
        owner = self

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls):
                return owner.now

        self.clock = FakeClock(step=20)
        self.logger = FakeLogger()
        self.schedule_calls = []
        self.next_run_calls = []
        self.screenshot_count = 0
        self.goto_main_calls = 0
        self.scene = 'unknown'
        self.screenshot_error = None
        self.start_time = self.now - timedelta(minutes=10)
        self.cfg = SimpleNamespace(
            scheduler=SimpleNamespace(
                server_update=server_time, failure_interval=interval,
                success_interval=timedelta(days=1), float_time=time(0),
                delay_date=1, next_run=None,
            ),
            dokan_config=SimpleNamespace(
                monday_to_thursday=weekdays_only, try_start_dokan=False,
                dokan_attack_priority=0,
            ),
            attack_count_config=SimpleNamespace(
                remain_attack_count=remaining, daily_attack_count=daily_count,
                init_attack_count=lambda callback: None,
            ),
        )
        self.config = SimpleNamespace(
            dokan=self.cfg, model=SimpleNamespace(dokan=self.cfg),
            reload=lambda: None, save=lambda: None, lock_config=MemoryLock(),
        )

        def forbidden_server_override(*args):
            raise AssertionError('Explicit opening check must not use the server override')

        scheduler_namespace = {
            'datetime': FrozenDateTime, 'time': time, 'timedelta': timedelta,
            'random': SimpleNamespace(randint=lambda first, last: 0),
            'convert_to_underscore': lambda name: 'dokan',
            'nearest_future': lambda targets: min(targets),
            'parse_tomorrow_server': forbidden_server_override,
            'dict_to_kv': lambda values, allow_none=False: str(values),
            'logger': self.logger,
        }
        task_delay = extract_methods(CONFIG, 'Config', ('task_delay',),
                                     scheduler_namespace)['task_delay']
        self.config.task_delay = task_delay.__get__(self.config)
        setter = extract_methods(BASE_TASK, 'BaseTask', ('set_next_run',),
                                 {'datetime': FrozenDateTime})['set_next_run']

        def set_next_run(**kwargs):
            self.schedule_calls.append(kwargs)
            return setter(self, **kwargs)

        self.set_next_run = set_next_run
        scene_names = set(re.findall(r'DokanScene\.([A-Z_]+)',
                                     SOURCE.read_text(encoding='utf-8-sig')))
        self.scenes = SimpleNamespace(**{name: name for name in scene_names})
        task_namespace = {
            'datetime': FrozenDateTime, 'timedelta': timedelta,
            'plan_opening_check': plan_opening_check, 'logger': self.logger,
            'retry_delay': retry_delay,
            'DokanScene': self.scenes, 'TaskEnd': TaskEnd,
            'Timer': lambda limit: FakeTimer(self.clock, limit),
            'sleep': lambda seconds: setattr(self.clock, 'now', self.clock.now + seconds),
        }
        methods = extract_methods(SOURCE, 'ScriptTask', ('next_run', 'run'), task_namespace)
        actual_next_run = methods['next_run']

        def next_run(*args, **kwargs):
            self.next_run_calls.append((args, kwargs))
            return actual_next_run(self, *args, **kwargs)

        self.next_run = next_run
        self.run = methods['run'].__get__(self)

    @property
    def target(self):
        return self.cfg.scheduler.next_run

    def screenshot(self):
        if self.screenshot_error:
            raise self.screenshot_error
        self.clock.advance()
        self.screenshot_count += 1
        if self.screenshot_count > 10:
            raise AssertionError('Unknown-page exit did not remain bounded')

    def get_current_scene(self, _refresh):
        if self.scene == 'finding':
            return True, self.scenes.RYOU_DOKAN_SCENE_FINDING_DOKAN
        return False, 'unknown'

    def goto_dokan_scene(self):
        pass

    def goto_main(self):
        self.goto_main_calls += 1

    def update_remain_attack_count(self):
        return self.cfg.attack_count_config.remain_attack_count


class DokanOpeningWindowTests(unittest.TestCase):
    def assert_explicit_target(self, harness, target):
        self.assertEqual(harness.target, datetime.fromisoformat(target))
        self.assertEqual(len(harness.schedule_calls), 1)
        call = harness.schedule_calls[0]
        self.assertEqual(call['task'], 'Dokan')
        self.assertTrue(call['finish'])
        self.assertFalse(call['server'])
        self.assertIsNone(call['success'])
        self.assertEqual(call['target'], harness.target)
        self.assertFalse(harness.config.lock_config.held)

    def test_non_default_server_time_preserves_short_target(self):
        harness = Harness('2026-10-06 19:10:00')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-06 19:13:00')

    def test_opening_instant_retries(self):
        harness = Harness('2026-10-06 19:00:00')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-06 19:03:00')

    def test_retry_at_end_does_not_exceed_deadline(self):
        for moment in ('19:57:00', '19:59:00'):
            with self.subTest(moment=moment):
                harness = Harness('2026-10-06 ' + moment)
                harness.next_run()
                self.assert_explicit_target(harness, '2026-10-06 20:00:00')

    def test_exact_deadline_schedules_next_day_without_claiming_success(self):
        harness = Harness('2026-10-06 20:00:00')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')
        self.assertTrue(any('not marked completed' in str(message)
                            for message in harness.logger.messages))

    def test_after_deadline_schedules_next_day(self):
        harness = Harness('2026-10-06 20:00:01')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')

    def test_before_opening_uses_today_start(self):
        harness = Harness('2026-10-06 18:55:00')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-06 19:00:00')

    def test_current_default_nine_oclock_remains_compatible(self):
        harness = Harness('2026-10-06 12:00:00', server_time=time(9))
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-07 09:00:00')

    def test_completed_next_day_uses_fixed_start_without_attempt_drift(self):
        harness = Harness('2026-10-06 19:31:48', remaining=0)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')

    def test_completed_single_daily_challenge_does_not_wait_for_second(self):
        harness = Harness('2026-10-06 19:31:48', remaining=1, daily_count=1)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')

    def test_skip_today_overrides_remaining_second_challenge(self):
        harness = Harness('2026-10-06 19:10:00', remaining=1)
        harness.next_run(skip_today=True, is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')

    def test_thursday_completion_skips_weekend(self):
        harness = Harness('2026-10-08 19:10:00', remaining=0)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-12 19:00:00')

    def test_thursday_expired_window_skips_weekend(self):
        harness = Harness('2026-10-08 20:00:00')
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-12 19:00:00')

    def test_weekend_skip_uses_next_allowed_day(self):
        harness = Harness('2026-10-09 19:10:00')
        harness.run()
        self.assert_explicit_target(harness, '2026-10-12 19:00:00')
        self.assertEqual(harness.screenshot_count, 0)

    def test_unrestricted_weekend_remains_available(self):
        harness = Harness('2026-10-09 19:10:00', weekdays_only=False, remaining=0)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-10 19:00:00')

    def test_confirmed_second_challenge_retries_within_window(self):
        harness = Harness('2026-10-06 19:31:48', remaining=1)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-06 19:34:48')

    def test_confirmed_second_challenge_survives_opening_window_deadline(self):
        harness = Harness('2026-10-06 20:01:00', remaining=1)
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-06 20:04:00')

    def test_unopened_second_challenge_keeps_opening_window_bound(self):
        harness = Harness('2026-10-06 20:01:00', remaining=1)
        harness.next_run(is_dokan_activated=False)
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')

    def test_second_challenge_invalid_interval_cannot_busy_loop(self):
        for interval in (None, timedelta(0), timedelta(seconds=-1)):
            with self.subTest(interval=interval):
                harness = Harness('2026-10-06 20:01:00', remaining=1, interval=interval)
                harness.next_run(is_dokan_activated=True)
                self.assert_explicit_target(harness, '2026-10-06 20:04:00')

    def test_second_challenge_serialized_interval_is_supported(self):
        harness = Harness('2026-10-06 20:01:00', remaining=1, interval='00 00:05:00')
        harness.next_run(is_dokan_activated=True)
        self.assert_explicit_target(harness, '2026-10-06 20:06:00')

    def test_configured_five_minute_retry_is_preserved(self):
        harness = Harness('2026-10-06 19:10:00', interval=timedelta(minutes=5))
        harness.next_run()
        self.assert_explicit_target(harness, '2026-10-06 19:15:00')

    def test_actual_unknown_page_exit_retries_without_completed_flag(self):
        harness = Harness('2026-10-06 19:10:00')
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assert_explicit_target(harness, '2026-10-06 19:13:00')
        self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])
        self.assertGreater(harness.clock.now, 160)
        self.assertEqual(harness.goto_main_calls, 1)

    def test_actual_unknown_page_after_window_does_not_claim_completion(self):
        harness = Harness('2026-10-06 20:10:00')
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')
        self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])

    def test_actual_unopened_finding_scene_retries_with_two_challenges_left(self):
        harness = Harness('2026-10-06 19:16:19', remaining=2)
        harness.scene = 'finding'
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assert_explicit_target(harness, '2026-10-06 19:19:19')
        self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])
        self.assertEqual(harness.cfg.attack_count_config.remain_attack_count, 2)

    def test_actual_zero_remaining_finding_scene_is_completed(self):
        harness = Harness('2026-10-06 19:16:19', remaining=0)
        harness.scene = 'finding'
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assert_explicit_target(harness, '2026-10-07 19:00:00')
        self.assertTrue(harness.next_run_calls[-1][1]['is_dokan_activated'])

    def test_unknown_count_retains_previous_values_and_retries_without_completion(self):
        for remaining in (1, 2):
            with self.subTest(remaining=remaining):
                harness = Harness('2026-10-06 19:16:19', remaining=remaining)
                harness.scene = 'finding'
                harness.update_remain_attack_count = lambda: -1
                with self.assertRaises(TaskEnd):
                    harness.run()
                self.assert_explicit_target(harness, '2026-10-06 19:19:19')
                self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])
                self.assertEqual(harness.cfg.attack_count_config.remain_attack_count, remaining)
                self.assertTrue(any('could not be confirmed' in str(message)
                                    for message in harness.logger.messages))

    def test_unknown_count_last_check_is_bounded_even_with_one_previous_challenge_left(self):
        harness = Harness('2026-10-06 19:59:00', remaining=1)
        harness.scene = 'finding'
        harness.update_remain_attack_count = lambda: -1
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assert_explicit_target(harness, '2026-10-06 20:00:00')
        self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])

    def test_unknown_count_after_deadline_advances_without_claiming_completion(self):
        for remaining in (1, 2):
            with self.subTest(remaining=remaining):
                harness = Harness('2026-10-06 20:10:00', remaining=remaining)
                harness.scene = 'finding'
                harness.update_remain_attack_count = lambda: -1
                with self.assertRaises(TaskEnd):
                    harness.run()
                self.assert_explicit_target(harness, '2026-10-07 19:00:00')
                self.assertFalse(harness.next_run_calls[-1][1]['is_dokan_activated'])
                self.assertEqual(harness.cfg.attack_count_config.remain_attack_count, remaining)
                self.assertTrue(any('not marked completed' in str(message)
                                    for message in harness.logger.messages))

    def test_owner_takeover_propagates_without_rescheduling(self):
        harness = Harness('2026-10-06 19:10:00')
        harness.screenshot_error = AccountLoggedInElsewhere('owner is online')
        with self.assertRaises(AccountLoggedInElsewhere):
            harness.run()
        self.assertEqual(harness.schedule_calls, [])
        self.assertEqual(harness.goto_main_calls, 0)


if __name__ == '__main__':
    unittest.main()
