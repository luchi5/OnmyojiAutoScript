"""Replay the moved honor icon against the shipped asset and editor metadata."""
import copy
import json
from pathlib import Path
import unittest

import cv2
import numpy as np

from tasks.Duel.assets import DuelAssets


ROOT = Path(__file__).resolve().parents[1]


class HonorLayoutTests(unittest.TestCase):
    def screenshot(self):
        data = (ROOT / 'tests/fixtures/duel/honor_bottom.png').read_bytes()
        crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[601:701, 175:330] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return image

    def test_actual_current_icon_is_found(self):
        rule = copy.deepcopy(DuelAssets.I_DUEL_HONOR)
        self.assertTrue(rule.match(self.screenshot()))
        self.assertEqual(rule.roi_front[:2], [190, 635])

    def test_original_roi_reproduces_the_failure(self):
        rule = copy.deepcopy(DuelAssets.I_DUEL_HONOR)
        rule.roi_back = (200, 601, 130, 100)
        self.assertFalse(rule.match(self.screenshot()))

    def test_legacy_icon_position_remains_supported(self):
        original = self.screenshot()
        shifted = np.zeros_like(original)
        shifted[:, 21:] = original[:, :-21]
        rule = copy.deepcopy(DuelAssets.I_DUEL_HONOR)
        self.assertTrue(rule.match(shifted))
        self.assertEqual(rule.roi_front[:2], [211, 635])

    def test_editor_metadata_also_matches_the_current_screenshot(self):
        data = json.loads((ROOT / 'tasks/Duel/duel/image.json').read_text(encoding='utf-8'))
        entry = next(item for item in data if item['itemName'] == 'duel_honor')
        rule = copy.deepcopy(DuelAssets.I_DUEL_HONOR)
        rule.roi_back = tuple(map(int, entry['roiBack'].split(',')))
        self.assertTrue(rule.match(self.screenshot()))


if __name__ == '__main__':
    unittest.main()
