import unittest
from pathlib import Path

import cv2

from tests.test_multi_courtyard import MultiCourtyardTests
from tasks.Component.Costume.assets import CostumeAssets
from tasks.Component.Costume.config import MainType


class RenewedCourtyardTests(unittest.TestCase):
    def test_shifted_layout_matches_independent_frame_and_click_targets(self):
        image = cv2.cvtColor(cv2.imread('tests/fixtures/courtyard/renewed_shifted_landmarks.png'), cv2.COLOR_BGR2RGB)
        task = MultiCourtyardTests().task(['costume_main_18'])
        task.device.image = image
        expected = {'CHECK_MAIN': (842, 227), 'MAIN_GOTO_EXPLORATION': (655, 140),
                    'MAIN_GOTO_SUMMON': (1058, 179), 'MAIN_GOTO_TOWN': (717, 282)}
        for key, (x, y) in expected.items():
            with self.subTest(key=key):
                rule = getattr(task, 'I_' + key)
                self.assertTrue(task.appear(rule))
                self.assertLessEqual(abs(rule.roi_front[0] - x), 4)
                self.assertLessEqual(abs(rule.roi_front[1] - y), 8)  # Lanterns sway vertically.
        self.assertTrue(CostumeAssets.I_PET_HOUSE_18.match(image))

    def test_independent_live_frame_and_entry_landmarks(self):
        image = cv2.cvtColor(cv2.imread('tests/fixtures/courtyard/renewed_landmarks.png'), cv2.COLOR_BGR2RGB)
        task = MultiCourtyardTests().task([item.value for item in MainType])
        task.device.image = image
        self.assertTrue(task.appear(task.I_CHECK_MAIN))
        self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN_18)
        for key in ('CHECK_MAIN', 'MAIN_GOTO_EXPLORATION', 'MAIN_GOTO_SUMMON', 'MAIN_GOTO_TOWN', 'PET_HOUSE'):
            with self.subTest(key=key):
                self.assertTrue(getattr(CostumeAssets, 'I_' + key + '_18').match(image))

    def test_existing_screens_do_not_match_new_courtyard(self):
        checked = 0
        for path in Path('tests/fixtures/draft_duel').rglob('*.png'):
            image = cv2.imread(str(path))
            if image is None or image.shape[:2] != (720, 1280):
                continue
            with self.subTest(path=str(path)):
                self.assertFalse(CostumeAssets.I_CHECK_MAIN_18.match(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
                self.assertFalse(CostumeAssets.I_CHECK_MAIN_18_SHIFTED.match(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
            checked += 1
        self.assertGreater(checked, 0)
