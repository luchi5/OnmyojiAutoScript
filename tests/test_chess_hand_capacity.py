"""Capacity-first purchasing regressions, including account 17's actual overflow frame."""
from collections import deque
from pathlib import Path
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from module.device.device import Device
from tasks.Chess.assets import ChessAssets
from tests import test_chess_season_update as season


class ChessHandCapacityTests(unittest.TestCase):
    def task(self):
        task = season.ChessSeasonUpdateTests().purchase_task([])
        task._recognize_shop_slot = Mock(return_value={'source': 'frame', 'score': .982})
        task._chess_click_until_disappear = Mock(return_value=True)
        task._free_one_hand_slot_for_purchase = Mock(side_effect=[
            {'equipped': ['shanghunniao'], 'sold_non_lineup': []}, {'type': 'lineup'}])
        return task

    def test_two_cards_over_limit_still_full_after_both_recoveries_never_clicks(self):
        task = self.task()
        task._hand_card_detections.side_effect = [[{}] * 12, [{}] * 11, [{}] * 10]
        self.assertFalse(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        task._chess_click_until_disappear.assert_not_called()
        task.click.assert_not_called()
        self.assertEqual([call.kwargs for call in task._free_one_hand_slot_for_purchase.call_args_list],
                         [{'sell_lineup': False}, {'sell_lineup': True}])

    def test_overflow_recovery_only_buys_after_hand_drops_below_capacity(self):
        task = self.task()
        task._hand_card_detections.side_effect = [[{}] * 11, [{}] * 10, [{}] * 9]
        self.assertTrue(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        self.assertEqual(task._free_one_hand_slot_for_purchase.call_count, 2)
        task._chess_click_until_disappear.assert_called_once()
        self.assertEqual(task._chess_click_until_disappear.call_args.kwargs['max_clicks'], 2)

    def test_account17_real_11_card_frame_never_adds_to_failed_purchase_click_history(self):
        image_path = Path('log/error/1792649926326/2026-10-22_14-18-46-316468.png')
        if not image_path.exists():
            self.skipTest('Private account 17 incident screenshot is not distributed')
        task = season.port.ChessPortTests().make_task()
        task.screenshot = Mock()
        task._recognize_shop_slot = Mock(return_value={'source': 'frame', 'score': .982})
        task._is_purchase_allowed = Mock(return_value=True)
        task._can_afford_shop_shikigami = Mock(return_value=True)
        task.device.image = cv2.cvtColor(cv2.imdecode(np.fromfile(image_path, np.uint8),
                                                     cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        # Exercise actual recognition instead of substituting a synthetic count.
        self.assertEqual(len(task._frame_card_scan('card')), 6)
        self.assertEqual(len(task._soul_hand_cards()), 5)
        self.assertEqual(len(task._hand_card_detections()), 11)
        task._free_one_hand_slot_for_purchase = Mock(return_value={
            'equipped': ['shanghunniao'], 'sold_non_lineup': []})
        task.device.click_record = deque(['shikigami_2'] * 9, maxlen=15)
        task.device.click_record_remove = Mock()

        def real_guard_click(rule, **kwargs):
            Device.click_record_add(task.device, rule)
            Device.click_record_check(task.device)
            return True

        task.click = Mock(side_effect=real_guard_click)
        # The real adapter would trigger the global guard on the first extra click.
        for _ in range(5):
            self.assertFalse(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        task.click.assert_not_called()
        self.assertEqual(len(task.device.click_record), 9)
        task.device.click_record_remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
