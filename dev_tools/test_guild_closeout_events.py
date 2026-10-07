"""Run actual guild task branches offline and inspect their closeout proofs.

The coordinator is replaced with a RAM spy: no account JSON, OCR, emulator,
service, or notification is accessed.
"""
from datetime import datetime
import sys
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch

from dev_tools.test_dokan_opening_window import Harness as Dokan, TaskEnd as DokanEnd
from tests.test_collective_missions_link import (
    Clock as CollectiveClock, FakeConfig, Runtime as Collective, TaskEnd as CollectiveEnd,
)
from tests.test_weekly_guild_opening import Clock, fake_task, TaskEnd as WeeklyEnd


class GuildCloseoutEvents(unittest.TestCase):
    def setUp(self):
        self.report = Mock()
        self.patch = patch.dict(sys.modules, {
            'tasks.Component.daily_closeout': S(report_closeout_outcome=self.report),
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def assert_event(self, task, outcome):
        self.report.assert_called_once()
        args = self.report.call_args.args
        self.assertEqual(args[1:], (task, outcome))

    def dokan(self, moment='2026-10-07 19:30:00', remaining=0, daily=2):
        task = Dokan(moment, remaining=remaining, daily_count=daily)
        task.cfg.attack_count_config.attack_date = task.now.date().isoformat()
        return task

    def test_only_final_second_dokan_reports_completed(self):
        task = self.dokan(remaining=1)
        task.next_run(is_dokan_activated=True)
        self.report.assert_not_called()
        task.cfg.attack_count_config.remain_attack_count = 0
        task.next_run(is_dokan_activated=True)
        self.assert_event('Dokan', 'completed')

    def test_one_configured_dokan_reports_after_first_challenge(self):
        task = self.dokan(remaining=1, daily=1)
        task.next_run(is_dokan_activated=True)
        self.assert_event('Dokan', 'completed')

    def test_stale_or_unknown_dokan_count_is_not_completion(self):
        for remaining, proof_date in ((-1, '2026-10-07'), (0, '2026-10-06')):
            with self.subTest(remaining=remaining, proof_date=proof_date):
                self.report.reset_mock()
                task = self.dokan(remaining=remaining)
                task.cfg.attack_count_config.attack_date = proof_date
                task.next_run(is_dokan_activated=True)
                self.report.assert_not_called()

    def test_dokan_skip_does_not_claim_completion(self):
        task = self.dokan()
        task.next_run(skip_today=True, is_dokan_activated=True)
        self.report.assert_not_called()

    def test_dokan_retry_wait_is_not_terminal(self):
        task = self.dokan(remaining=2)
        task.next_run()
        self.report.assert_not_called()

    def test_dokan_deadline_reports_expired(self):
        task = self.dokan('2026-10-07 20:00:00', remaining=2)
        task.next_run()
        self.assert_event('Dokan', 'expired')

    def test_finished_scene_with_unknown_counter_does_not_use_old_zero(self):
        task = self.dokan()
        task.get_current_scene = lambda _refresh: (True, task.scenes.RYOU_DOKAN_SCENE_FINISHED)
        task.update_remain_attack_count = lambda: -1
        with self.assertRaises(DokanEnd):
            task.run()
        self.report.assert_not_called()
        self.assertFalse(task.next_run_calls[-1][1]['is_dokan_activated'])
        self.assertEqual(task.target, datetime(2026, 10, 7, 19, 33))

    def test_banquet_wait_is_not_terminal(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.report.assert_not_called()

    def test_banquet_actual_end_reports_completion(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        task.appear.side_effect = [True, True, False]
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.assert_event('GuildBanquet', 'completed')

    def test_banquet_timeout_inside_window_is_not_completion(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        task.appear.return_value = True
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.report.assert_not_called()

    def test_banquet_deadline_reports_expired(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T21:00:00')
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.assert_event('GuildBanquet', 'expired')

    def test_planning_next_banquet_does_not_invent_game_completion(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:10:00')
        task.plan_next_run()
        self.report.assert_not_called()

    def retreat(self, moment='2026-10-10T10:10:00', success=True):
        task, _ = fake_task('DemonRetreat', moment)
        task.goto_demon_retreat = Mock(return_value=True)
        task.demon_retreat.return_value = success
        task.appear.side_effect = lambda item: item == task.I_RANK_LSIT
        task.appear_then_click.side_effect = lambda item, **kw: item == task.I_DEMON_BACK_CHECK
        return task

    def test_retreat_actual_battle_success_reports_completed(self):
        task = self.retreat()
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.assert_event('DemonRetreat', 'completed')

    def test_retreat_battle_failure_still_retrying_is_not_terminal(self):
        task = self.retreat(success=False)
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.report.assert_not_called()

    def test_retreat_failure_after_window_reports_expired(self):
        task = self.retreat('2026-10-10T10:59:00', success=False)
        def failed_after_deadline():
            Clock.current = datetime(2026, 10, 10, 11, 2)
            return False
        task.demon_retreat.side_effect = failed_after_deadline
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.assert_event('DemonRetreat', 'expired')

    def test_retreat_old_reward_does_not_invent_new_battle_success(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T10:10:00')
        task.appear_then_click.side_effect = lambda item, **kw: item == task.I_REWARD_ALL
        with self.assertRaises(WeeklyEnd):
            task.goto_demon_retreat()
        self.assert_event('DemonRetreat', 'skipped')
        task.demon_retreat.assert_not_called()

    def collective(self, counters=((0, 30, 30), (30, 0, 30), (30, 0, 30))):
        CollectiveClock.current = datetime(2026, 10, 7, 19, 30)
        config = FakeConfig()
        config.state.dokan_finished_date = '2026-10-07'
        return Collective(config, counters=counters)

    def test_collective_two_full_initial_frames_report_completed(self):
        task = self.collective(counters=((30, 0, 30), (30, 0, 30)))
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.assert_event('CollectiveMissions', 'completed')
        task._donate.assert_not_called()

    def test_collective_verified_submission_reports_completed(self):
        task = self.collective()
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.assert_event('CollectiveMissions', 'completed')
        task._donate.assert_called_once()

    def test_collective_one_full_frame_is_not_completion(self):
        task = self.collective(counters=((30, 0, 30), (29, 1, 30)))
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.report.assert_not_called()
        task._donate.assert_not_called()

    def test_collective_failed_selection_keeps_retry_without_terminal_event(self):
        task = self.collective(counters=((0, 30, 30),))
        task.select_mission.return_value = False
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.report.assert_not_called()
        self.assertEqual(task.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 7, 19, 33))

    def test_unconfirmed_collective_submission_is_failed_not_completed(self):
        task = self.collective(counters=((0, 30, 30), (29, 1, 30)))
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.assert_event('CollectiveMissions', 'failed')

    def test_collective_previous_attempt_without_full_counter_is_failed(self):
        task = self.collective(counters=((29, 1, 30),))
        task.config.state.attempted_date = '2026-10-07'
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.assert_event('CollectiveMissions', 'failed')
        task._donate.assert_not_called()

    def test_collective_waiting_child_task_is_not_terminal(self):
        task = self.collective(counters=())
        task.config.state.attempted_date = '2026-10-07'
        task.config.state.pending_kind = 'bondling_reward'
        task.config.state.pending_until = '2026-10-07 21:30:00'
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.report.assert_not_called()
        task.screenshot.assert_not_called()

    def test_collective_saved_full_counter_proof_can_restore_event(self):
        task = self.collective(counters=())
        task.config.state.completed_date = '2026-10-07'
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.assert_event('CollectiveMissions', 'completed')
        task.screenshot.assert_not_called()

    def test_dokan_cross_midnight_completion_does_not_prove_new_day(self):
        task = self.dokan()
        task.get_current_scene = lambda _refresh: (True, task.scenes.RYOU_DOKAN_SCENE_FINISHED)
        def crossed_midnight():
            task.now = datetime(2026, 10, 8, 0, 1)
            task.cfg.attack_count_config.attack_date = '2026-10-08'
        task.screenshot = crossed_midnight
        with self.assertRaises(DokanEnd):
            task.run()
        self.report.assert_not_called()

    def test_dokan_previous_day_run_cannot_report_new_day_expiration(self):
        task = self.dokan('2026-10-08 20:00:00', remaining=2)
        task._dokan_closeout_date = datetime(2026, 10, 7).date()
        task.next_run()
        self.report.assert_not_called()

    def test_banquet_cross_midnight_end_does_not_prove_new_day(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T20:59:00')
        task.appear.side_effect = [True, True, False]
        task.screenshot.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 8, 0, 1))
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.report.assert_not_called()

    def test_banquet_previous_run_cannot_report_new_day_expiration(self):
        task, _ = fake_task('GuildBanquet', '2026-10-07T21:00:00')
        task._banquet_closeout_date = datetime(2026, 10, 6).date()
        task._schedule_opening_check()
        self.report.assert_not_called()

    def test_retreat_cross_midnight_win_does_not_prove_new_day(self):
        task = self.retreat('2026-10-10T10:59:00')
        def won_after_midnight():
            Clock.current = datetime(2026, 10, 11, 0, 1)
            return True
        task.demon_retreat.side_effect = won_after_midnight
        with self.assertRaises(WeeklyEnd):
            task.run()
        self.report.assert_not_called()

    def test_retreat_previous_run_cannot_report_new_day_expiration(self):
        task, _ = fake_task('DemonRetreat', '2026-10-10T11:00:00')
        task._retreat_closeout_date = datetime(2026, 10, 9).date()
        task._schedule_opening_check()
        self.report.assert_not_called()

    def test_collective_cross_midnight_counter_does_not_write_new_day_proof(self):
        task = self.collective()
        task._donate.side_effect = lambda _index: setattr(
            CollectiveClock, 'current', datetime(2026, 10, 8, 0, 1))
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.report.assert_not_called()
        self.assertNotEqual(task.config.state.completed_date, '2026-10-08')

    def test_collective_cross_midnight_unconfirmed_submission_is_not_new_day_failure(self):
        task = self.collective(counters=((0, 30, 30), (29, 1, 30)))
        task._donate.side_effect = lambda _index: setattr(
            CollectiveClock, 'current', datetime(2026, 10, 8, 0, 1))
        with self.assertRaises(CollectiveEnd):
            task.run()
        self.report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
