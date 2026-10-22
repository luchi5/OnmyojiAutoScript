"""Integration checks for the 2026-10-22 xy Chess update, with no game input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tasks.Chess.runtime.hyakki_draft import (
    HyakkiTurn, ChessHyakkiDraftMixin, player_name_box, throw_point,
)
from tasks.Chess.strategy.lineup import LINEUP_REGISTRY
from tests import test_chess_port as port


class ChessXyOctoberUpdateTests(unittest.TestCase):
    def task(self):
        return port.ChessPortTests().make_task()

    def recovery_task(self, result):
        task = self.task()
        task.emergency_cleanup_hand = Mock(return_value=result)
        task._is_hand_cleanup_allowed = Mock(return_value=True)
        task.sell_one_lineup_hand_card = Mock(return_value={'type': 'lineup'})
        task._ensure_shop_open = Mock(return_value=True)
        task.screenshot = Mock()
        return task

    def test_no_progress_cleanup_immediately_sells_one_lineup_card_and_reopens_shop(self):
        task = self.recovery_task({'equipped': [], 'sold_non_lineup': []})
        self.assertEqual(task._free_one_hand_slot_for_purchase(), {'type': 'lineup'})
        task.sell_one_lineup_hand_card.assert_called_once_with()
        task._ensure_shop_open.assert_called_once_with()

    def test_progress_cleanup_does_not_also_sell_lineup(self):
        for result in ({'equipped': ['soul'], 'sold_non_lineup': []},
                       {'equipped': [], 'sold_non_lineup': [(300, 620)]}):
            task = self.recovery_task(result)
            self.assertEqual(task._free_one_hand_slot_for_purchase(), result)
            task.sell_one_lineup_hand_card.assert_not_called()

    def test_changed_stage_or_failed_cleanup_never_sells_lineup(self):
        task = self.recovery_task({'equipped': [], 'sold_non_lineup': []})
        task._is_hand_cleanup_allowed.return_value = False
        self.assertIsNone(task._free_one_hand_slot_for_purchase())
        task.sell_one_lineup_hand_card.assert_not_called()
        task._ensure_shop_open.assert_not_called()
        task = self.recovery_task(None)
        self.assertIsNone(task._free_one_hand_slot_for_purchase())
        task.sell_one_lineup_hand_card.assert_not_called()

    def test_new_lineup_and_qijiaoshan_position_three(self):
        self.assertIn('pinganjing', LINEUP_REGISTRY)
        strategy = LINEUP_REGISTRY['qijiaoshan']['strategy']['shikigami']
        self.assertEqual(strategy['keisei_chihime']['display_name'], '鲸汐千姬')
        self.assertEqual(strategy['keisei_chihime']['position'], 3)
        self.assertNotIn('座敷童子', [unit['display_name'] for unit in strategy.values()])

    def test_golden_copy_only_targets_verified_one_cost_units_at_actual_position(self):
        task = self.task()
        task._board_actual_positions = {'kaoru': 8, 'hakurou': 2, 'ichimoku_ren': 5}
        self.assertEqual(task._golden_copy_targets({'kaoru', 'ichimoku_ren'}), [('kaoru', 8)])
        self.assertEqual(task._golden_copy_targets(set()), [])

    def test_golden_copy_without_verified_target_is_preserved(self):
        task = self.task()
        task._is_preparation_mode = Mock(return_value=True)
        task._golden_copy_hand_cards = Mock(return_value=[{'position': (300, 620)}])
        task._equip_soul_card = Mock()
        self.assertEqual(task.use_golden_copies_from_hand(set()), 0)
        task._equip_soul_card.assert_not_called()

    def test_active_stage_gold_is_required_and_grayscale_preview_is_rejected(self):
        task = self.task()
        rule = task.I_STAGE_HYAKKI
        x, y, w, h = rule.roi_back
        template = rule.image
        task.device.image = np.zeros((720, 1280, 3), np.uint8)
        task.device.image[y:y + template.shape[0], x:x + template.shape[1]] = template
        self.assertTrue(task._stage_marker_visible(rule))
        gray = cv2.cvtColor(task.device.image, cv2.COLOR_RGB2GRAY)
        task.device.image = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        self.assertFalse(task._stage_marker_visible(rule))

    def test_boss_marker_state_expires_on_next_round_and_next_game(self):
        task = self.task()
        task._current_round_no = 5
        task._stage_marker_visible = Mock(return_value=True)
        self.assertTrue(task._is_boss_challenge_round())
        task._stage_marker_visible.return_value = False
        self.assertTrue(task._is_boss_challenge_round())
        task._current_round_no = 6
        self.assertFalse(task._is_boss_challenge_round())
        task._current_round_no = 5
        task.chess_state.game_number += 1
        self.assertFalse(task._is_boss_challenge_round())

    def test_unknown_grigri_options_are_rechecked_on_a_new_frame(self):
        task = self.task()
        unknown = [{'name': None}] * 3
        known = [{'name': 'known'}] * 3
        task._recognize_grigri_options = Mock(side_effect=[unknown, known])
        task.screenshot = Mock()
        with patch('tasks.Chess.runtime.round_state.time.sleep'):
            self.assertEqual(task._recognize_grigri_options_with_retry(), known)
        task.screenshot.assert_called_once_with()

    def test_hyakki_lock_reappearance_resets_wait_and_two_throw_budget(self):
        state = HyakkiTurn((1, 3), wait_seconds=6)
        self.assertFalse(state.ready(.9, 0))
        self.assertFalse(state.ready(.2, 1))
        self.assertFalse(state.ready(.2, 6))
        self.assertTrue(state.ready(.2, 7))
        self.assertFalse(state.ready(.7, 8))
        self.assertFalse(state.ready(.2, 9))
        self.assertTrue(state.ready(.2, 15))
        state.throws = 2
        self.assertFalse(state.ready(.2, 20))

    def test_hyakki_prefers_needed_soul_and_waits_for_all_locks_before_random(self):
        task = SimpleNamespace(_hyakki_all_locks_gone=Mock(return_value=False), _hyakki_skip=Mock())
        candidates = [{'name': 'needed'}, {'name': 'other'}]
        choose = ChessHyakkiDraftMixin._hyakki_target_pool
        self.assertEqual(choose(task, candidates, {'needed'}), [candidates[0]])
        self.assertEqual(choose(task, candidates, set()), [])
        task._hyakki_all_locks_gone.return_value = True
        self.assertEqual(choose(task, candidates, set()), candidates)

    def test_player_name_coordinates_rebase_roi_and_reject_multiple_yellow_names(self):
        image = np.zeros((100, 200, 3), np.uint8)
        image[25:35, 110:130] = (255, 220, 0)
        result = SimpleNamespace(ocr_text='player', box=[[10, 5], [30, 5], [30, 15], [10, 15]])
        rule = SimpleNamespace(roi=(100, 20, 80, 60), detect_and_ocr=Mock(return_value=[result]))
        self.assertEqual(player_name_box(rule, image), ('player', (110, 25, 20, 10)))
        rule.detect_and_ocr.return_value = [result, result]
        self.assertIsNone(player_name_box(rule, image))

    def test_throw_compensation_never_clicks_outside_allowed_roi(self):
        roi = (100, 100, 300, 300)
        self.assertEqual(throw_point({'center': (300, 160)}, {'center': (280, 160)}, .2, .1, roi),
                         (265, 310))
        self.assertIsNone(throw_point({'center': (110, 160)}, {'center': (100, 160)}, .2, .3, roi))
        self.assertIsNone(throw_point({'center': (280, 350)}, {'center': (280, 350)}, 0, 0, roi))


if __name__ == '__main__':
    unittest.main()
