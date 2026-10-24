"""Ordinary OCR/drag failures recover autonomously without bypassing budgets."""
from dataclasses import replace
from itertools import count
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tasks.DraftDuel.config import DraftDuelConfig
from tasks.DraftDuel.preflight import AccountConfig, PairConfig
from tasks.DraftDuel.workflow import DraftWorkflow, StartOffer
from tasks.DraftDuel.session import SessionError
from tasks.DraftDuel.deployment import Deployment, DeploymentController


class AdmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('tasks.DraftDuel.workflow.time.monotonic', return_value=120).start()

    def setup_workflow(self, budget=300000):
        settings = DraftDuelConfig(user_status='leader', partner_config='two',
                                   teammate_name='member', free_only=False, coin_budget=budget)
        other = DraftDuelConfig(user_status='member', partner_config='one',
                               teammate_name='leader', free_only=False, coin_budget=budget)
        pair = PairConfig((AccountConfig('one', 'a', 'a', settings),
                           AccountConfig('two', 'b', 'b', other)))
        workflow = object.__new__(DraftWorkflow)
        workflow.pair, workflow.name = pair, 'one'
        workflow.settings, workflow.own_name = settings, 'leader'
        workflow.peer_guard, workflow.event, workflow.vision = Mock(), Mock(), Mock()
        devices = {'one': Mock(), 'two': Mock()}
        timestamps = count(111, .25)
        for device in devices.values():
            device.capture.side_effect = lambda: (None, next(timestamps))
        return workflow, devices

    def test_single_unreadable_lobby_recovers_without_user(self):
        workflow, devices = self.setup_workflow()
        good = (True, (1193, 616))
        with patch('tasks.DraftDuel.workflow.read_admission_page', side_effect=[(False, None), good, good, good, good]), \
             patch('tasks.DraftDuel.workflow.time.sleep'):
            offers, start = workflow.recover_admission(devices)
        self.assertTrue(offers['one'].ready)
        self.assertEqual(start, (1193, 616))
        self.assertEqual(devices['one'].capture.call_count, 3)

    def test_changed_page_rechecks_both_accounts(self):
        workflow, devices = self.setup_workflow()
        good = (True, (1193, 616))
        with patch('tasks.DraftDuel.workflow.read_admission_page', side_effect=[good, good, (False, None), good, good, good]), \
             patch('tasks.DraftDuel.workflow.time.sleep'):
            offers, _ = workflow.recover_admission(devices)
        self.assertTrue(offers['two'].ready)
        self.assertEqual(devices['two'].capture.call_count, 2)

    def test_old_budget_setting_does_not_block_ready_lobby(self):
        workflow, devices = self.setup_workflow(0)
        with patch('tasks.DraftDuel.workflow.read_admission_page', return_value=(True, (1, 1))):
            _, point = workflow.recover_admission(devices)
        self.assertEqual(point, (1, 1))

    def test_repeated_unknown_price_is_bounded_and_never_taps(self):
        workflow, devices = self.setup_workflow()
        with patch('tasks.DraftDuel.workflow.read_admission_page', return_value=(False, None)), \
             patch('tasks.DraftDuel.workflow.time.sleep'):
            with self.assertRaises(SessionError):
                workflow.recover_admission(devices)
        self.assertEqual(devices['one'].capture.call_count, 3)
        for device in devices.values():
            device.tap.assert_not_called()

    def test_stale_or_future_capture_is_reread_before_admission(self):
        for invalid_at in (100, 130):
            with self.subTest(captured=invalid_at):
                workflow, devices = self.setup_workflow()
                devices['one'].capture.side_effect = [(None, invalid_at), (None, 115), (None, 116)]
                with patch('tasks.DraftDuel.workflow.read_admission_page', return_value=(True, (1, 1))), \
                     patch('tasks.DraftDuel.workflow.time.sleep'):
                    workflow.recover_admission(devices)
                self.assertEqual(devices['two'].capture.call_count, 2)

    def test_reused_final_capture_never_authorizes_start(self):
        workflow, devices = self.setup_workflow()
        devices['one'].capture.side_effect = None
        devices['one'].capture.return_value = (None, 115)
        with patch('tasks.DraftDuel.workflow.read_admission_page', return_value=(True, (1, 1))), \
             patch('tasks.DraftDuel.workflow.time.sleep'):
            with self.assertRaises(SessionError):
                workflow.recover_admission(devices)
        self.assertEqual(devices['one'].capture.call_count, 6)


class DragRecoveryTests(unittest.TestCase):
    def make_runner(self):
        clock = Mock(return_value=100.5)
        io = Mock()
        runner = DeploymentController(io, Mock(), ('a', 'b', 'c'), clock=clock)
        wrong = Deployment(100, 'right', 1, 20, 'b', (750, 330), {'a': (400, 588)})
        runner.observe = Mock(return_value=wrong)
        return runner, io, clock, wrong

    def test_missed_drag_retries_on_a_fresh_same_stage_then_confirms(self):
        runner, io, clock, wrong = self.make_runner()
        self.assertEqual(runner.step(True), 'drag_not_verified_no_confirmation')
        self.assertEqual(runner.step(True), 'waiting_for_fresh_drag_retry')
        clock.return_value = 102.5
        fresh = replace(wrong, captured=102)
        io.drag.side_effect = lambda *args: setattr(clock, 'return_value', 103)
        runner.observe.side_effect = [fresh, fresh, replace(fresh, captured=102.75, current='a')]
        self.assertEqual(runner.step(True), 'confirmation_sent')
        self.assertEqual(io.drag.call_count, 2)
        io.confirm.assert_called_once()

    def test_cached_pre_drag_portrait_cannot_confirm_on_this_or_next_step(self):
        import time
        io = Mock()
        runner = DeploymentController(io, Mock(), ('a', 'b', 'c'), clock=time.monotonic)
        # Real monotonic capture time is recent, but still precedes the input.
        before = Deployment(time.monotonic(), 'right', 1, 20, 'b',
                            (750, 330), {'a': (400, 588)})
        cached = replace(before, current='a')
        runner.observe = Mock(side_effect=[before, before, cached])
        self.assertEqual(runner.step(True), 'drag_not_verified_no_confirmation')
        self.assertLessEqual(cached.captured, runner.last_drag_at[('right', 1)])
        self.assertTrue(cached.usable(time.monotonic()))
        # Returning the same cached desired portrait on another loop must not
        # bypass the post-drag timestamp guard by taking the no-drag branch.
        runner.observe = Mock(return_value=cached)
        self.assertEqual(runner.step(True), 'drag_not_verified_no_confirmation')
        io.drag.assert_called_once()
        io.confirm.assert_not_called()

    def test_two_failed_drags_never_turn_into_unbounded_clicking(self):
        runner, io, clock, wrong = self.make_runner()
        runner.step(True)
        clock.return_value = 102.5
        runner.observe.return_value = replace(wrong, captured=102)
        runner.step(True)
        clock.return_value = 104.5
        runner.observe.return_value = replace(wrong, captured=104)
        self.assertEqual(runner.step(True), 'waiting_for_stage_recovery')
        self.assertEqual(io.drag.call_count, 2)
        io.confirm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
