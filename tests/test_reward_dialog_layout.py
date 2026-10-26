"""Replay the tall contract-shop reward dialog with its raised/rightward title."""
import copy
import json
from pathlib import Path
import unittest

import cv2
import numpy as np

from tasks.GlobalGame.assets import GlobalGameAssets


ROOT = Path(__file__).resolve().parents[1]


class RewardDialogLayoutTests(unittest.TestCase):
    def screenshot(self):
        data = (ROOT / 'tests/fixtures/reward_dialog/contract_header.png').read_bytes()
        crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[90:220, 450:890] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return image

    def test_actual_contract_reward_is_found_at_original_threshold(self):
        rule = copy.deepcopy(GlobalGameAssets.I_UI_REWARD)
        self.assertTrue(rule.match(self.screenshot()))
        self.assertEqual(rule.roi_front[:2], [523, 124])

    def test_old_roi_reproduces_missed_reward_even_at_lower_runtime_threshold(self):
        rule = copy.deepcopy(GlobalGameAssets.I_UI_REWARD)
        rule.roi_back = (464, 142, 350, 145)
        self.assertFalse(rule.match(self.screenshot(), threshold=0.6))

    def test_legacy_reward_title_position_remains_supported(self):
        original = self.screenshot()
        image = np.zeros_like(original)
        rule = copy.deepcopy(GlobalGameAssets.I_UI_REWARD)
        height, width = rule.image.shape[:2]
        image[185:185 + height, 481:481 + width] = original[124:124 + height, 523:523 + width]
        self.assertTrue(rule.match(image))
        self.assertEqual(rule.roi_front[:2], [481, 185])

    def test_blank_frame_is_not_a_reward(self):
        rule = copy.deepcopy(GlobalGameAssets.I_UI_REWARD)
        self.assertFalse(rule.match(np.zeros((720, 1280, 3), dtype=np.uint8), threshold=0.6))

    def test_editor_metadata_also_finds_contract_reward(self):
        entries = json.loads((ROOT / 'tasks/GlobalGame/ui/image.json').read_text(encoding='utf-8'))
        entry = next(item for item in entries if item['itemName'] == 'ui_reward')
        rule = copy.deepcopy(GlobalGameAssets.I_UI_REWARD)
        rule.roi_back = tuple(map(int, entry['roiBack'].split(',')))
        self.assertTrue(rule.match(self.screenshot()))


if __name__ == '__main__':
    unittest.main()
