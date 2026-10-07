"""Offline dynamic closeout tests; all persistent state is in temporary paths."""
from copy import deepcopy
from datetime import datetime, time, timedelta
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as S
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.Component import daily_closeout as DC
from tasks.TalismanPass.config import DailyCloseoutConfig, TalismanPass


class FakeConfig:
    def __init__(self, name='01-测试', enabled=True, linked=True):
        self.config_name = name
        self.running_task = ''
        self.talisman_pass = TalismanPass()
        self.talisman_pass.scheduler.enable = True
        self.talisman_pass.closeout_config.enable = enabled
        self.calls = []
        self.saved = 0
        self.clock = datetime(2026, 10, 5, 20)
        self.task_call_result = True
        self.task_call_error = False
        for field in DC.TASK_FIELDS.values():
            setattr(self, field, S(scheduler=S(enable=True, next_run=self.clock)))
        self.collective_missions.missions_config = S(run_after_dokan=linked)

    def task_call(self, task, force_call=True):
        self.calls.append((task, force_call))
        if self.task_call_error:
            raise RuntimeError('fake task queue failure')
        if self.task_call_result:
            self.talisman_pass.scheduler.next_run = self.clock
        return self.task_call_result

    def save(self):
        self.saved += 1


def event_process(state_dir, task, outcome):
    DC.STATE_DIR = Path(state_dir)
    config = FakeConfig()
    result = DC.report_closeout_outcome(config, task, outcome, now=config.clock)
    if result.reason == 'state_unavailable':
        raise RuntimeError('subprocess failed to persist proof')


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oas-closeout-test-')
        self.location = Path(self.temp.name)
        self.directory_patch = patch.object(DC, 'STATE_DIR', self.location)
        self.directory_patch.start()
        self.warn_patch = patch.object(DC, '_warn')
        self.warn_patch.start()
        self.config = FakeConfig()
        self.now = self.config.clock

    def tearDown(self):
        self.warn_patch.stop()
        self.directory_patch.stop()
        self.temp.cleanup()

    def report(self, task, outcome='completed', now=None):
        return DC.report_closeout_outcome(self.config, task, outcome, now=now or self.now)

    def ready(self):
        self.report('Dokan')
        self.report('CollectiveMissions')

    def test_weekday_dependencies_and_only_enabled(self):
        expected = {
            0: ('Dokan', 'CollectiveMissions'), 1: ('Dokan', 'CollectiveMissions'),
            2: ('Dokan', 'CollectiveMissions'), 3: ('Dokan', 'CollectiveMissions'),
            4: ('AbyssShadows', 'GuildBanquet'), 5: ('AbyssShadows', 'DemonRetreat'),
            6: ('AbyssShadows', 'GuildBanquet'),
        }
        for day in range(7):
            at = self.now + timedelta(days=day)
            self.assertEqual(DC.closeout_dependencies(self.config, at), expected[day])
        self.config.dokan.scheduler.enable = False
        self.assertEqual(DC.closeout_dependencies(self.config, self.now), ())
        self.config.dokan.scheduler.enable = True
        self.config.collective_missions.scheduler.enable = False
        self.assertEqual(DC.closeout_dependencies(self.config, self.now), ('Dokan',))

    def test_independent_collective_not_dependency(self):
        self.config.collective_missions.missions_config.run_after_dokan = False
        self.assertEqual(DC.closeout_dependencies(self.config, self.now), ('Dokan',))

    def test_reports_do_not_queue(self):
        self.ready()
        self.assertEqual(self.config.calls, [])
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertTrue(result.queued)
        self.assertEqual(self.config.calls, [('TalismanPass', False)])
        self.assertTrue(DC.is_automatic_closeout(self.config, now=self.now))

    def test_dokan_complete_still_waits_collective(self):
        self.report('Dokan')
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertFalse(result.queued)
        self.assertEqual(result.waiting, ('CollectiveMissions',))

    def test_schedule_next_day_or_taskend_not_completion(self):
        self.config.dokan.scheduler.next_run = self.now + timedelta(days=1)
        self.config.collective_missions.scheduler.next_run = datetime(2099, 1, 1)
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(result.waiting, ('Dokan', 'CollectiveMissions'))
        self.assertFalse(result.queued)

    def test_expired_dokan_blocks_linked_collective_honestly(self):
        self.report('Dokan', 'expired')
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertTrue(result.queued)
        self.assertEqual(result.completed, ())
        self.assertIn(('Dokan', 'expired'), result.incomplete)
        self.assertIn(('CollectiveMissions', 'blocked_by_dokan'), result.incomplete)

    def test_failed_collective_allows_terminal_report(self):
        self.report('Dokan')
        self.report('CollectiveMissions', 'failed')
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertTrue(result.queued)
        self.assertEqual(result.completed, ('Dokan',))
        self.assertEqual(result.incomplete, (('CollectiveMissions', 'failed'),))

    def test_due_daily_blocks_even_at_fallback(self):
        at = self.now.replace(hour=23)
        result = DC.evaluate_daily_closeout(self.config, now=at, due_tasks=('WantedQuests', 'KekkaiUtilize'))
        self.assertFalse(result.queued)
        self.assertEqual(result.reason, 'waiting_due_tasks')
        result = DC.evaluate_daily_closeout(self.config, now=at, due_tasks=())
        self.assertTrue(result.queued)
        self.assertEqual(result.reason, 'fallback')
        self.assertEqual(result.waiting, ('Dokan', 'CollectiveMissions'))
        summary = DC.closeout_summary(self.config, at)
        self.assertEqual([item['outcome'] for item in summary['incomplete']], ['unconfirmed', 'unconfirmed'])

    def test_success_waits_other_due_daily(self):
        self.ready()
        result = DC.evaluate_daily_closeout(self.config, now=self.now, due_tasks=('AreaBoss', 'TalismanPass'))
        self.assertFalse(result.queued)
        self.assertEqual(result.due_tasks, ('AreaBoss',))

    def test_no_guild_earliest_time_not_midnight(self):
        self.config.dokan.scheduler.enable = False
        at = self.now.replace(hour=1)
        result = DC.evaluate_daily_closeout(self.config, now=at)
        self.assertEqual(result.reason, 'before_earliest_time')
        self.assertFalse(result.queued)
        self.config.clock = at.replace(hour=18)
        self.assertTrue(DC.evaluate_daily_closeout(self.config, now=self.config.clock).queued)

    def test_disabled_closeout_and_disabled_talisman(self):
        self.config.talisman_pass.closeout_config.enable = False
        self.ready()
        self.assertEqual(DC.evaluate_daily_closeout(self.config, now=self.now).reason, 'disabled')
        self.config.talisman_pass.closeout_config.enable = True
        self.config.talisman_pass.scheduler.enable = False
        self.assertEqual(DC.evaluate_daily_closeout(self.config, now=self.now).reason, 'disabled')
        self.assertFalse(DC.arm_daily_closeout(self.config, self.now))

    def test_restart_does_not_duplicate_queue_and_complete_guard(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        restarted = FakeConfig()
        restarted.talisman_pass.scheduler.next_run = self.config.talisman_pass.scheduler.next_run
        self.assertEqual(DC.evaluate_daily_closeout(restarted, now=self.now).reason, 'already_queued')
        self.assertEqual(restarted.calls, [])
        self.assertTrue(DC.mark_closeout_completed(restarted, self.now))
        self.assertFalse(DC.mark_closeout_completed(restarted, self.now))
        self.assertEqual(DC.evaluate_daily_closeout(restarted, now=self.now).reason, 'already_completed')

    def test_main_config_stale_overwrite_cannot_erase_state(self):
        self.ready()
        saved = deepcopy(DC.closeout_state(self.config, self.now))
        legacy = FakeConfig()
        del legacy.talisman_pass.closeout_config
        legacy.save()
        self.assertEqual(DC.closeout_state(self.config, self.now), saved)

    def test_midnight_discards_previous_day_proofs(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        DC.mark_closeout_completed(self.config, self.now)
        tomorrow = self.now + timedelta(days=1)
        result = DC.evaluate_daily_closeout(self.config, now=tomorrow)
        self.assertFalse(result.queued)
        self.assertEqual(result.waiting, ('Dokan', 'CollectiveMissions'))
        self.assertEqual(DC.closeout_state(self.config, tomorrow)['outcomes'], {})

    def test_account_state_separate_including_decimal_name(self):
        self.report('Dokan')
        other = FakeConfig('demo-account-007')
        self.assertEqual(DC.closeout_state(other, self.now)['outcomes'], {})
        DC.report_closeout_outcome(other, 'Dokan', 'expired', now=self.now)
        self.assertTrue((self.location / 'demo-account-007.json').exists())
        self.assertEqual(DC.closeout_state(self.config, self.now)['outcomes']['Dokan']['outcome'], 'completed')

    def test_unsafe_account_path_is_rejected(self):
        for name in ('../escape', 'C:\\escape', '..', 'last.', 'name\x00'):
            config = FakeConfig(name)
            self.assertTrue(DC.closeout_state(config, self.now).get('unavailable'))
            self.assertEqual(DC.report_closeout_outcome(config, 'Dokan', 'completed', now=self.now).reason,
                             'state_unavailable')

    def test_corrupt_state_never_erased_or_queued(self):
        path = self.location / (self.config.config_name + '.json')
        path.write_text('{broken', encoding='utf-8')
        result = DC.evaluate_daily_closeout(self.config, now=self.now.replace(hour=23))
        self.assertEqual(result.reason, 'state_unavailable')
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
        self.assertEqual(self.config.calls, [])

    def test_finished_proof_not_overwritten_by_later_skip_or_failure(self):
        self.report('Dokan')
        self.report('Dokan', 'skipped')
        self.report('Dokan', 'failed')
        self.assertEqual(DC.closeout_state(self.config, self.now)['outcomes']['Dokan']['outcome'], 'completed')

    def test_later_real_success_replaces_failed_proof(self):
        self.report('Dokan', 'failed')
        self.report('Dokan')
        self.assertEqual(DC.closeout_state(self.config, self.now)['outcomes']['Dokan']['outcome'], 'completed')

    def test_queue_failure_rolls_back_intent(self):
        self.ready()
        self.config.task_call_error = True
        self.assertEqual(DC.evaluate_daily_closeout(self.config, now=self.now).reason, 'state_unavailable')
        self.assertEqual(DC.closeout_state(self.config, self.now)['queued_date'], '')
        self.config.task_call_error = False
        self.assertTrue(DC.evaluate_daily_closeout(self.config, now=self.now).queued)

    def test_queue_disabled_result_rolls_back_intent(self):
        self.ready()
        self.config.task_call_result = False
        self.assertEqual(DC.evaluate_daily_closeout(self.config, now=self.now).reason, 'disabled')
        self.assertEqual(DC.closeout_state(self.config, self.now)['queued_date'], '')

    def test_crash_after_intent_recovers_existing_queue_once(self):
        self.ready()
        path = self.location / (self.config.config_name + '.json')
        state = DC.closeout_state(self.config, self.now)
        state.update(queued_date=self.now.date().isoformat(), queue_reason='dependencies_terminal',
                     queue_stage='intent', queued_at=self.now.isoformat(),
                     queued_next_run=self.now.isoformat(),
                     queue_previous_next_run=self.config.talisman_pass.scheduler.next_run.isoformat())
        path.write_text(json.dumps(state), encoding='utf-8')
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertTrue(result.queued)
        self.assertEqual(result.reason, 'queue_recovered')
        self.assertTrue(DC.is_automatic_closeout(self.config, now=self.now))
        DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(len(self.config.calls), 1)

    def test_manual_override_is_distinct_and_never_overwritten(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        manual = self.now - timedelta(minutes=3)
        self.config.talisman_pass.scheduler.next_run = manual
        self.assertFalse(DC.is_automatic_closeout(self.config, now=self.now))
        self.assertFalse(DC.should_hold_talisman(self.config, now=self.now, manual=True))
        self.assertTrue(DC.should_hold_talisman(self.config, now=self.now))
        DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, manual)
        self.assertEqual(len(self.config.calls), 1)

    def test_arm_fixed_schedule_and_completed_parks_tomorrow(self):
        self.assertTrue(DC.arm_daily_closeout(self.config, self.now))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, self.now.replace(hour=23))
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertFalse(DC.arm_daily_closeout(self.config, self.now))
        DC.mark_closeout_completed(self.config, self.now)
        self.assertTrue(DC.arm_daily_closeout(self.config, self.now))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run,
                         self.now.replace(hour=23) + timedelta(days=1))

    def test_two_process_events_merge_under_account_lock(self):
        context = multiprocessing.get_context('spawn')
        jobs = [context.Process(target=event_process, args=(str(self.location), task, 'completed'))
                for task in ('Dokan', 'CollectiveMissions')]
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(20)
            self.assertEqual(job.exitcode, 0)
        self.assertEqual(set(DC.closeout_state(self.config, self.now)['outcomes']),
                         {'Dokan', 'CollectiveMissions'})

    def test_automatic_retry_updates_timestamp_and_keeps_identity(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertTrue(DC.schedule_closeout_retry(self.config, now=self.now))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, self.now + timedelta(minutes=3))
        self.assertTrue(DC.is_automatic_closeout(self.config, now=self.now))
        self.assertEqual(len(self.config.calls), 1)

    def test_retry_crash_recovers_requested_future_target(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        path = self.location / (self.config.config_name + '.json')
        state = DC.closeout_state(self.config, self.now)
        target = self.now + timedelta(minutes=3)
        state.update(queue_stage='retry_intent', queued_next_run=target.isoformat(),
                     queue_previous_next_run=self.config.talisman_pass.scheduler.next_run.isoformat())
        path.write_text(json.dumps(state), encoding='utf-8')
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(result.reason, 'retry_recovered')
        self.assertFalse(result.queued)
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, target)
        self.assertTrue(DC.is_automatic_closeout(self.config, now=self.now))
        self.assertEqual(len(self.config.calls), 1)

    def test_manual_unqueued_or_completed_has_no_automatic_retry(self):
        self.assertFalse(DC.schedule_closeout_retry(self.config, now=self.now))
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        DC.mark_closeout_completed(self.config, self.now)
        self.assertFalse(DC.schedule_closeout_retry(self.config, now=self.now))

    def test_retry_never_carries_old_proof_over_midnight(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertFalse(DC.schedule_closeout_retry(self.config, now=self.now.replace(hour=23, minute=59)))
        self.assertEqual(DC.closeout_state(self.config, self.now)['queue_stage'], 'queued')

    def test_disabled_reporting_does_not_create_any_state(self):
        self.config.talisman_pass.closeout_config.enable = False
        self.assertEqual(self.report('Dokan').reason, 'disabled')
        self.assertEqual(list(self.location.iterdir()), [])
        self.config.talisman_pass.closeout_config.enable = True
        self.config.talisman_pass.scheduler.enable = False
        self.assertEqual(self.report('Dokan').reason, 'disabled')
        self.assertEqual(list(self.location.iterdir()), [])

    def test_manual_marker_takes_precedence_over_eligible_auto_queue(self):
        self.ready()
        manual = self.now - timedelta(minutes=1)
        self.config.talisman_pass.scheduler.next_run = manual
        self.assertTrue(DC.request_manual_talisman(self.config, manual, now=self.now))
        self.assertTrue(DC.is_manual_talisman(self.config, now=self.now))
        self.assertFalse(DC.is_automatic_closeout(self.config, now=self.now))
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(result.reason, 'manual_requested')
        self.assertEqual(self.config.calls, [])
        self.assertFalse(DC.arm_daily_closeout(self.config, self.now))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, manual)
        self.assertTrue(DC.clear_manual_talisman_request(self.config, now=self.now))
        self.assertFalse(DC.is_manual_talisman(self.config, now=self.now))
        self.assertTrue(DC.evaluate_daily_closeout(self.config, now=self.now).queued)

    def test_manual_marker_needs_matching_timestamp_and_current_date(self):
        manual = self.now - timedelta(minutes=1)
        self.assertTrue(DC.request_manual_talisman(self.config, manual, now=self.now))
        self.assertFalse(DC.is_manual_talisman(self.config, now=self.now))
        self.assertTrue(DC.is_manual_talisman(self.config, scheduled_at=manual, now=self.now))
        self.config.talisman_pass.scheduler.next_run = manual
        self.assertFalse(DC.is_manual_talisman(self.config, now=self.now + timedelta(days=1)))
        self.assertFalse(DC.request_manual_talisman(self.config, self.now + timedelta(minutes=1), now=self.now))

    def test_manual_marker_does_not_clear_newer_unconsumed_request(self):
        consumed = self.now - timedelta(minutes=1)
        newer = self.now - timedelta(minutes=2)
        DC.request_manual_talisman(self.config, consumed, now=self.now)
        DC.request_manual_talisman(self.config, newer, now=self.now)
        self.assertFalse(DC.clear_manual_talisman_request(self.config, scheduled_at=consumed, now=self.now))
        self.assertTrue(DC.is_manual_talisman(self.config, scheduled_at=newer, now=self.now))
        self.assertTrue(DC.clear_manual_talisman_request(self.config, scheduled_at=newer, now=self.now))

    def test_manual_priority_over_pending_automatic_retry(self):
        self.ready()
        DC.evaluate_daily_closeout(self.config, now=self.now)
        manual = self.now - timedelta(minutes=1)
        self.config.talisman_pass.scheduler.next_run = manual
        DC.request_manual_talisman(self.config, manual, now=self.now)
        self.assertFalse(DC.schedule_closeout_retry(self.config, now=self.now))
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, manual)
        DC.clear_manual_talisman_request(self.config, now=self.now)
        self.assertTrue(DC.schedule_closeout_retry(self.config, now=self.now))
        self.assertTrue(DC.is_automatic_closeout(self.config, now=self.now))

    def test_intent_recovery_does_not_overwrite_external_schedule_change(self):
        self.ready()
        path = self.location / (self.config.config_name + '.json')
        state = DC.closeout_state(self.config, self.now)
        state.update(queued_date=self.now.date().isoformat(), queue_reason='dependencies_terminal',
                     queue_stage='intent', queued_at=self.now.isoformat(),
                     queued_next_run=self.now.isoformat(),
                     queue_previous_next_run=self.config.talisman_pass.scheduler.next_run.isoformat())
        path.write_text(json.dumps(state), encoding='utf-8')
        external = self.now + timedelta(hours=1)
        self.config.talisman_pass.scheduler.next_run = external
        result = DC.evaluate_daily_closeout(self.config, now=self.now)
        self.assertEqual(result.reason, 'schedule_changed')
        self.assertEqual(self.config.calls, [])
        self.assertEqual(self.config.talisman_pass.scheduler.next_run, external)

    def test_default_config_opt_in_and_three_visible_fields(self):
        options = DailyCloseoutConfig()
        self.assertFalse(options.enable)
        self.assertEqual(options.fallback_time, time(23))
        self.assertEqual(options.earliest_time, time(18))
        self.assertEqual(set(options.model_dump()), {'enable', 'fallback_time', 'earliest_time'})
        self.assertEqual(options.model_json_schema()['properties']['enable']['title'], 'daily_closeout_enable')


if __name__ == '__main__':
    unittest.main()
