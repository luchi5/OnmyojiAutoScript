"""Buy first; scan and recover capacity only after a failed purchase."""
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
        task._chess_click_until_disappear = Mock(return_value=False)
        task._read_chess_mode = Mock(return_value='备')
        task._free_one_hand_slot_for_purchase = Mock(side_effect=[
            {'equipped': ['shanghunniao'], 'sold_non_lineup': []}, {'type': 'lineup'}])
        return task

    def test_successful_purchase_never_scans_hand_or_cleans_up(self):
        task = self.task()
        task._chess_click_until_disappear.return_value = True
        self.assertTrue(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        task._hand_card_detections.assert_not_called()
        task._free_one_hand_slot_for_purchase.assert_not_called()

    def test_confirmed_purchase_removes_actual_control_name_only(self):
        task = self.task()
        task.device.click_record = deque(['shikigami_2'] * 8 + ['other'], maxlen=15)
        task.device.click_record_remove = lambda rule: Device.click_record_remove(task.device, rule)
        task._chess_click_until_disappear.return_value = True
        self.assertTrue(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        self.assertEqual(list(task.device.click_record), ['other'])

    def test_failed_purchase_pauses_without_emergency_sale_or_retry(self):
        for count in (0, 2, 9, 10, 11, 12):
            with self.subTest(count=count):
                task = self.task()
                task._hand_card_detections.return_value = [{}] * count
                self.assertFalse(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
                self.assertTrue(task._hand_purchase_blocked)
                task._chess_click_until_disappear.assert_called_once()
                task._free_one_hand_slot_for_purchase.assert_not_called()
                task._hand_card_detections.assert_not_called()

    def test_insufficient_gold_does_not_attempt_purchase_or_scan_hand(self):
        task = self.task()
        task._can_afford_shop_shikigami.return_value = False
        self.assertFalse(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        task._chess_click_until_disappear.assert_not_called()
        task._hand_card_detections.assert_not_called()

    def test_account17_real_11_card_frame_stops_retrying_after_initial_attempt(self):
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
        task.device.click_record = deque(['shikigami_2'] * 4, maxlen=15)
        task.device.click_record_remove = Mock()

        def real_guard_click(rule, **kwargs):
            Device.click_record_add(task.device, rule.name)
            Device.click_record_check(task.device)
            return True

        task.click = Mock(side_effect=real_guard_click)
        def failed_attempt(rule, **kwargs):
            for _ in range(kwargs['max_clicks']):
                task.click(rule)
            return False
        task._chess_click_until_disappear = Mock(side_effect=failed_attempt)
        self.assertFalse(task._buy_shop_slot(2, ChessAssets.C_SHIKIGAMI_2, 'keisei_chihime'))
        task._chess_click_until_disappear.assert_called_once()
        self.assertEqual(task.click.call_count, 2)
        self.assertEqual(len(task.device.click_record), 6)
        task.device.click_record_remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
