"""Actual scheduler/reward/API methods under offline clocks and temp state."""
from datetime import datetime, timedelta
import json
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace as S
import unittest
from unittest.mock import patch

from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger
from dev_tools.test_daily_closeout import FakeConfig
from module.config.config_model import ConfigModel
from module.config.utils import convert_to_underscore, write_file
from module.exception import TaskEnd
from tasks.Component import daily_closeout as DC

ROOT = Path(__file__).resolve().parents[1]


class Clock(datetime):
    value = datetime(2026, 10, 5, 20)

    @classmethod
    def now(cls, tz=None):
        return cls.value


def scheduler(config):
    methods = extract_methods(ROOT / 'script.py', 'Script', ('_evaluate_daily_closeout', 'wait_until'),
                              {'datetime': Clock, 'timedelta': timedelta, 'logger': FakeLogger(),
                               'convert_to_underscore': convert_to_underscore,
                               '_release_token_before_wait': lambda fn: fn,
                               'time': S(sleep=lambda seconds: setattr(Clock, 'value', Clock.value + timedelta(seconds=seconds)))})
    worker = S(config=config, _account_session_restart_pending=False)
    for name, method in methods.items():
        setattr(worker, name, method.__get__(worker))
    config.start_watching = lambda: None
    config.should_reload = lambda: False
    return worker


def talisman(config, page=True, error=None):
    namespace = {name: getattr(DC, name) for name in (
        'arm_daily_closeout', 'clear_manual_talisman_request', 'closeout_enabled',
        'closeout_state', 'closeout_summary', 'hold_closeout_until',
        'is_automatic_closeout', 'is_manual_talisman', 'mark_closeout_completed',
        'next_closeout_check', 'schedule_closeout_retry')}
    namespace.update(datetime=Clock, timedelta=timedelta, logger=FakeLogger(), page_daily='today', page_main='main', TaskEnd=TaskEnd)
    methods = extract_methods(ROOT / 'tasks/TalismanPass/script_task.py', 'ScriptTask', ('run', '_finish_dynamic_run'), namespace)
    task = S(config=config, actions=[])
    for name, method in methods.items():
        setattr(task, name, method.__get__(task))
    task.goto_page = lambda page: task.actions.append(('goto', page))
    task.in_task = lambda: page
    task.get_flower = lambda value: task.actions.append(('level', value))
    task.harvest_soul = lambda: task.actions.append(('soul',))
    task._capture_daily_feedback = lambda **kwargs: False
    task._capture_collective_feedback = lambda expected_date: False
    task.set_next_run = lambda **kwargs: task.actions.append(('legacy_delay',))
    def collect():
        task.actions.append(('collect',))
        if error:
            raise error
    task.get_all = collect
    config.reload = lambda: None
    return task


class CloseoutIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.location = Path(self.temp.name)
        self.patches = [patch.object(DC, 'STATE_DIR', self.location / 'proofs'), patch.object(DC, 'datetime', Clock),
                        patch('tasks.Component.daily_feedback_capture.ensure_talisman_today', return_value=None),
                        patch('tasks.Component.daily_feedback_capture.refresh_collective_feedback', return_value=False),
                        patch.dict(sys.modules, {'tasks.Component.daily_feedback':
                                                 S(finalize_feedback=lambda *args, **kwargs: False,
                                                   consume_recapture_request=lambda *args, **kwargs: False,
                                                   finish_recapture_request=lambda *args, **kwargs: False,
                                                   maybe_notify_feedback=lambda *args, **kwargs: False)})]
        for item in self.patches:
            item.start()
        Clock.value = datetime(2026, 10, 5, 20)
        self.config = FakeConfig()
        self.config.clock = Clock.value

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def ready(self):
        for name in ('Dokan', 'CollectiveMissions'):
            DC.report_closeout_outcome(self.config, name, 'completed', now=Clock.value)

    def queue(self):
        self.ready()
        self.assertTrue(scheduler(self.config)._evaluate_daily_closeout().queued)

    def finish(self, task):
        with self.assertRaises(TaskEnd):
            task.run()

    def test_scheduler_waits_due_delegation_even_at_fallback(self):
        Clock.value = datetime(2026, 10, 5, 23)
        self.config.delegation = S(scheduler=S(enable=True, next_run=Clock.value - timedelta(minutes=1)))
        self.ready()
        decision = scheduler(self.config)._evaluate_daily_closeout()
        self.assertEqual(decision.reason, 'waiting_due_tasks')
        self.assertEqual(self.config.calls, [])

    def test_future_foster_and_continuous_chess_do_not_block(self):
        self.ready()
        self.config.kekkai_utilize = S(scheduler=S(enable=True, next_run=Clock.value + timedelta(hours=1)))
        self.config.chess = S(scheduler=S(enable=True, next_run=Clock.value - timedelta(days=1)))
        self.assertTrue(scheduler(self.config)._evaluate_daily_closeout().queued)

    def test_damaged_proof_does_not_leave_overdue_checkpoint_in_hot_loop(self):
        self.config.talisman_pass.scheduler.next_run = Clock.value - timedelta(hours=2)
        path = DC._state_path(self.config)
        path.write_text('not valid JSON', encoding='utf-8')
        with patch.object(DC, '_warn'):
            result = scheduler(self.config)._evaluate_daily_closeout()
        self.assertEqual(result.reason, 'state_unavailable')
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, Clock.value + timedelta(minutes=3))

    def test_old_fixed_time_checkpoint_is_parked_without_collecting(self):
        self.config.talisman_pass.scheduler.next_run = Clock.value - timedelta(hours=2)
        task = talisman(self.config)
        self.finish(task)
        self.assertEqual(task.actions, [])
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, datetime(2026, 10, 5, 23))

    def test_idle_wait_wakes_at_earliest_with_no_guilds(self):
        Clock.value = datetime(2026, 10, 5, 17, 59, 55)
        self.config.clock = datetime(2026, 10, 5, 18)
        for field in DC.TASK_FIELDS.values():
            getattr(self.config, field).scheduler.enable = False
        self.config.talisman_pass.scheduler.next_run = datetime(2026, 10, 5, 23)
        self.assertFalse(scheduler(self.config).wait_until(datetime(2026, 10, 5, 23)))
        self.assertEqual(Clock.value, datetime(2026, 10, 5, 18))
        self.assertEqual(self.config.calls, [('TalismanPass', False)])

    def test_auto_rewards_complete_day_and_park_tomorrow(self):
        self.queue()
        task = talisman(self.config)
        self.finish(task)
        self.assertIn(('collect',), task.actions)
        self.assertEqual(DC.closeout_state(self.config, Clock.value)['completed_date'], '2026-10-05')
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, datetime(2026, 10, 6, 23))
        self.assertFalse(scheduler(self.config)._evaluate_daily_closeout().queued)

    def test_manual_flash_keeps_day_unfinished_then_arms(self):
        target = Clock.value - timedelta(days=1)
        self.config.talisman_pass.scheduler.next_run = target
        self.assertTrue(DC.request_manual_talisman(self.config, target, now=Clock.value))
        self.assertEqual(scheduler(self.config)._evaluate_daily_closeout().reason, 'manual_requested')
        task = talisman(self.config)
        self.finish(task)
        self.assertIn(('collect',), task.actions)
        state = DC.closeout_state(self.config, Clock.value)
        self.assertEqual(state['completed_date'], '')
        self.assertEqual(state['manual_next_run'], '')
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, datetime(2026, 10, 5, 23))

    def test_manual_override_of_queued_auto_restores_auto_after_three_minutes(self):
        self.queue()
        target = Clock.value - timedelta(days=1)
        self.config.talisman_pass.scheduler.next_run = target
        DC.request_manual_talisman(self.config, target, now=Clock.value)
        self.finish(talisman(self.config))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, Clock.value + timedelta(minutes=3))
        self.assertTrue(DC.is_automatic_closeout(self.config, now=Clock.value))
        self.assertEqual(DC.closeout_state(self.config, Clock.value)['completed_date'], '')

    def test_new_manual_request_while_collecting_is_preserved(self):
        target = Clock.value - timedelta(days=1)
        self.config.talisman_pass.scheduler.next_run = target
        DC.request_manual_talisman(self.config, target, now=Clock.value)
        task = talisman(self.config)
        newer = target + timedelta(seconds=2)
        def edit():
            self.config.talisman_pass.scheduler.next_run = newer
            DC.request_manual_talisman(self.config, newer, now=Clock.value)
        self.config.reload = edit
        self.finish(task)
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, newer)
        self.assertTrue(DC.is_manual_talisman(self.config, now=Clock.value))

    def test_missing_task_page_retries_without_marking_complete(self):
        self.queue()
        task = talisman(self.config, page=False)
        self.finish(task)
        self.assertNotIn(('collect',), task.actions)
        self.assertEqual(DC.closeout_state(self.config, Clock.value)['completed_date'], '')
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, Clock.value + timedelta(minutes=3))
        self.assertTrue(DC.is_automatic_closeout(self.config, now=Clock.value))

    def test_reward_exception_never_marks_completed(self):
        self.queue()
        with self.assertRaises(RuntimeError):
            talisman(self.config, error=RuntimeError('fixture')).run()
        self.assertEqual(DC.closeout_state(self.config, Clock.value)['completed_date'], '')

    def test_missing_task_page_near_midnight_parks_next_day_without_false_completion(self):
        Clock.value = datetime(2026, 10, 5, 23, 58)
        self.config.clock = Clock.value
        self.queue()
        self.finish(talisman(self.config, page=False))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, datetime(2026, 10, 6, 23))
        state = DC.closeout_state(self.config, Clock.value)
        self.assertEqual(state['queued_date'], '2026-10-05')
        self.assertEqual(state['completed_date'], '')

    def test_reward_flow_crossing_midnight_does_not_complete_new_day(self):
        self.queue()
        task = talisman(self.config)
        self.config.reload = lambda: setattr(Clock, 'value', datetime(2026, 10, 6, 0, 1))
        self.finish(task)
        self.assertEqual(DC.closeout_state(self.config, Clock.value)['completed_date'], '')
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, datetime(2026, 10, 6, 23))

    def test_disabled_mode_preserves_original_rewards_and_schedule(self):
        self.config.talisman_pass.closeout_config.enable = False
        task = talisman(self.config)
        self.finish(task)
        self.assertIn(('collect',), task.actions)
        self.assertIn(('legacy_delay',), task.actions)
        self.assertFalse((self.location / 'proofs').exists())

    def test_actual_external_model_api_binds_manual_but_internal_save_does_not(self):
        original = Path.cwd()
        os.chdir(self.location)
        try:
            seed = json.loads(json.dumps(ConfigModel().model_dump(), default=str))
            seed['config_name'] = '01-test'
            seed['talisman_pass']['scheduler']['enable'] = True
            seed['talisman_pass']['closeout_config']['enable'] = True
            write_file(Path('config/01-test.json'), seed)
            model = ConfigModel('01-test')
            with patch('module.config.config_model.datetime', Clock):
                target = Clock.value - timedelta(days=1)
                self.assertTrue(model.script_set_arg('TalismanPass', 'scheduler', 'next_run', target.strftime('%Y-%m-%d %H:%M:%S')))
            self.assertTrue(DC.is_manual_talisman(model, now=Clock.value))
            DC.clear_manual_talisman_request(model, now=Clock.value)
            model.talisman_pass.scheduler.next_run = target + timedelta(seconds=1)
            model.save()
            self.assertFalse(DC.is_manual_talisman(model, now=Clock.value))
        finally:
            os.chdir(original)


if __name__ == '__main__':
    unittest.main()
