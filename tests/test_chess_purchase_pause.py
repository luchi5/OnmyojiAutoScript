"""Full-hand pauses survive round/phase changes without touching a device."""
from collections import deque
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from module.device.device import Device
from tasks.Chess.assets import ChessAssets
from tests import test_chess_season_update as season


class ChessPurchasePauseTests(unittest.TestCase):
    def task(self):
        task = season.ChessSeasonUpdateTests().purchase_task([])
        task._recognize_shop_slot = Mock(return_value={'source': 'frame', 'score': .996})
        task._chess_click_until_disappear = Mock(return_value=False)
        task._hand_card_detections.return_value = [{}] * 10
        task._free_one_hand_slot_for_purchase = Mock(return_value={})
        task._read_chess_mode = Mock(return_value='备')
        return task

    def block(self, task):
        self.assertFalse(task._buy_shop_slot(5, ChessAssets.C_SHIKIGAMI_5, 'komatsumaru'))
        self.assertTrue(task._hand_purchase_blocked)

    def economy(self, task, level=7, gold=50):
        task._schedule_economy_cycle()
        task._read_level = Mock(return_value=level)
        task._read_shop_gold = Mock(return_value=gold)
        task._lineup_final_level = Mock(return_value=8)
        task._economy_sequence_level = level
        task._economy_sequence_index = 1  # Refresh would normally be next.
        task._click_economy_button_and_confirm_gold = Mock(return_value='success')
        task._click_shop_refresh_and_confirm_slots = Mock(return_value='success')

    def test_reentry_in_other_phases_and_rounds_never_retries_while_full(self):
        task = self.task()
        self.block(task)
        calls = task._hand_card_detections.call_count
        for round_no, mode in ((27, '备'), (27, '战'), (28, '备'), (28, '战')):
            task.chess_state.begin_round(round_no)
            task._read_chess_mode.return_value = mode
            task._schedule_economy_cycle()
            self.assertIsNone(task.purchase_lineup_cards_once())
            self.assertFalse(task._buy_shop_slot(5, ChessAssets.C_SHIKIGAMI_5, 'komatsumaru'))
        task._chess_click_until_disappear.assert_called_once()
        self.assertEqual(task._hand_card_detections.call_count, calls)
        self.assertEqual(task._free_one_hand_slot_for_purchase.call_count, 2)

    def test_cycle_completion_does_not_reset_pause_but_new_game_does(self):
        task = self.task()
        self.block(task)
        task._finish_economy_cycle('budget limit')
        task._schedule_economy_cycle()
        task.chess_state.begin_round(28)
        self.assertTrue(task._hand_purchase_blocked)
        task.chess_state.begin_game()
        self.assertFalse(task._hand_purchase_blocked)
        self.assertFalse(task._hand_resume_purchase_pending)

    def test_failed_cleanup_and_target_change_also_keep_pause(self):
        for result in (None, {}):
            with self.subTest(result=result):
                task = self.task()
                task._free_one_hand_slot_for_purchase.return_value = result
                if result is not None:
                    card = {'source': 'frame', 'score': .99}
                    task._recognize_shop_slot.side_effect = [card, card, None]
                self.block(task)
                self.assertIsNone(task.purchase_lineup_cards_once())
                task._chess_click_until_disappear.assert_called_once()

    def test_no_scan_without_pause_and_no_false_resume_on_missing_or_unstable_cards(self):
        task = self.task()
        self.assertFalse(task._resume_shop_after_hand_processing())
        task._hand_card_detections.assert_not_called()
        for counts in ((10,), (0,), (9, 10), (9, 8), (9, 0)):
            with self.subTest(counts=counts):
                task._hand_purchase_blocked = True
                task._hand_card_detections.side_effect = [[{}] * n for n in counts]
                with patch('tasks.Chess.runtime.economy.time.sleep'):
                    self.assertFalse(task._resume_shop_after_hand_processing())
                self.assertTrue(task._hand_purchase_blocked)

    def test_two_matching_fresh_frames_resume_without_any_purchase_click(self):
        task = self.task()
        task._hand_purchase_blocked = True
        task._hand_card_detections.return_value = [{}] * 9
        with patch('tasks.Chess.runtime.economy.time.sleep'):
            self.assertTrue(task._resume_shop_after_hand_processing())
        self.assertFalse(task._hand_purchase_blocked)
        self.assertTrue(task._hand_resume_purchase_pending)
        self.assertEqual(task.screenshot.call_count, 2)
        task.click.assert_not_called()

    def test_phase_transition_or_grigri_never_releases_pause(self):
        for mode, grigri in (('鬼', False), (None, False), ('备', True)):
            with self.subTest(mode=mode, grigri=grigri):
                task = self.task()
                task._hand_purchase_blocked = True
                task._read_chess_mode.return_value = mode
                task.appear.return_value = grigri
                self.assertFalse(task._resume_shop_after_hand_processing())
                task._hand_card_detections.assert_not_called()

    def test_preparation_processes_hand_before_capacity_confirmation(self):
        task = self.task()
        task._hand_purchase_blocked = True
        task._is_preparation_mode = Mock(return_value=True)
        task._is_shop_open = Mock(return_value=False)
        events = []
        task.deploy_shikigami_from_hand = Mock(side_effect=lambda: events.append('deploy'))
        task.equip_souls_from_hand = Mock(side_effect=lambda names: events.append('equip'))
        task._hand_card_detections.side_effect = lambda: events.append('count') or [{}] * 9
        with patch('tasks.Chess.runtime.economy.time.sleep'):
            self.assertTrue(task._handle_preparation())
        self.assertEqual(events, ['deploy', 'equip', 'count', 'count'])
        self.assertFalse(task._hand_purchase_blocked)

    def test_battle_cleanup_can_release_pause_only_after_confirming_space(self):
        for count, blocked in ((10, True), (9, False)):
            with self.subTest(count=count):
                task = self.task()
                task._hand_purchase_blocked = True
                task._read_chess_mode.return_value = '战'
                task.cleanup_non_lineup_hand_cards = Mock(return_value=[])
                task._hand_card_detections.return_value = [{}] * count
                with patch('tasks.Chess.runtime.economy.time.sleep'):
                    self.assertTrue(task._handle_battle_sell_stage())
                self.assertEqual(task._hand_purchase_blocked, blocked)
                task._chess_click_until_disappear.assert_not_called()

    def test_paused_shop_cannot_refresh_through_low_level_entry(self):
        task = self.task()
        for blocked, pending in ((True, False), (False, True)):
            task._hand_purchase_blocked = blocked
            task._hand_resume_purchase_pending = pending
            self.assertEqual(task._click_shop_refresh_and_confirm_slots(True), 'no_progress')
        task.click.assert_not_called()

    def test_paused_refresh_sequence_can_buy_experience_under_original_budget(self):
        task = self.task()
        task._hand_purchase_blocked = True
        self.economy(task)
        self.assertEqual(task._run_economy_atomic_batch(), 'pending')
        task._click_economy_button_and_confirm_gold.assert_called_once_with(
            task.I_EXPERIENCE, task.EXPERIENCE_COST, 'buy experience', allow_hidden=False)
        task._click_shop_refresh_and_confirm_slots.assert_not_called()
        self.assertTrue(task._hand_purchase_blocked)

    def test_paused_shop_respects_reserve_final_level_and_upgrade_progress(self):
        for level, gold, levels, expected, upgrades in (
                (7, 23, None, 'complete', 0),
                (8, 100, None, 'blocked', 0),
                (7, 50, [7, 8], 'blocked', 1)):
            with self.subTest(level=level, gold=gold, levels=levels):
                task = self.task()
                task._hand_purchase_blocked = True
                self.economy(task, level, gold)
                if levels is not None:
                    task._read_level.side_effect = levels
                self.assertEqual(task._run_economy_atomic_batch(), expected)
                self.assertEqual(task._click_economy_button_and_confirm_gold.call_count, upgrades)
                task._click_shop_refresh_and_confirm_slots.assert_not_called()
                self.assertTrue(task._hand_purchase_blocked)

    def test_battle_entry_at_final_level_neither_buys_nor_refreshes(self):
        task = self.task()
        self.block(task)
        self.economy(task, level=8)
        task._read_chess_mode.return_value = '战'
        task._read_remaining_time = Mock(return_value=60)
        task._run_battle_economy_until_budget_limit()
        task._chess_click_until_disappear.assert_called_once()
        task._click_economy_button_and_confirm_gold.assert_not_called()
        task._click_shop_refresh_and_confirm_slots.assert_not_called()

    def test_resume_scans_current_shop_before_any_refresh(self):
        task = self.task()
        self.economy(task, level=8)
        task._hand_resume_purchase_pending = True
        task._ensure_shop_open = Mock(return_value=True)
        events = []
        task._shop_slots = Mock(side_effect=lambda: events.append('current_shop') or [])
        task._click_shop_refresh_and_confirm_slots.side_effect = lambda **kw: events.append('refresh') or 'success'
        with patch('tasks.Chess.runtime.economy.time.sleep'):
            self.assertEqual(task._run_economy_atomic_batch(), 'pending')
        self.assertEqual(events, ['current_shop', 'refresh', 'current_shop'])
        self.assertFalse(task._hand_resume_purchase_pending)

    def test_account19_full_hand_stays_blocked_across_repeated_round_entries(self):
        files = list(Path('log/error/1792677775102').glob('*.png'))
        if not files:
            self.skipTest('Private incident screenshot is not distributed')
        import cv2
        import numpy as np
        from tests.test_chess_port import ChessPortTests

        task = ChessPortTests().make_task()
        task.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(files[0].read_bytes(), np.uint8),
                                                     cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.assertEqual(len(task._hand_card_detections()), 10)
        task.screenshot = Mock()
        task.appear = Mock(return_value=False)
        task._is_purchase_allowed = Mock(return_value=True)
        task._can_afford_shop_shikigami = Mock(return_value=True)
        task._recognize_shop_slot = Mock(return_value={'source': 'frame', 'score': .996})
        task._free_one_hand_slot_for_purchase = Mock(return_value={})
        task.device.click_record = deque(maxlen=15)
        task.device.click_record_remove = Mock()

        def failed_attempt(rule, **kwargs):
            for _ in range(kwargs['max_clicks']):
                Device.click_record_add(task.device, rule)
                Device.click_record_check(task.device)
            return False
        task._chess_click_until_disappear = Mock(side_effect=failed_attempt)
        self.block(task)
        for round_no in range(27, 37):
            task.chess_state.begin_round(round_no)
            for _ in ('备', '战'):
                self.assertIsNone(task.purchase_lineup_cards_once())
        task._chess_click_until_disappear.assert_called_once()
        self.assertEqual(len(task.device.click_record), 2)
        task.device.click_record_remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
