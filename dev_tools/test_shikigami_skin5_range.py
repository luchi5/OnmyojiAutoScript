from copy import deepcopy
from pathlib import Path
import unittest
import cv2
import numpy as np
from tasks.Component.CostumeShikigami.assets import CostumeShikigamiAssets

ROOT = Path(__file__).resolve().parents[1]


class Skin5RangeTests(unittest.TestCase):
    def frame(self):
        path = ROOT / "dev_tools/fixtures/shikigami_skin5_shifted.png"
        return cv2.cvtColor(cv2.imdecode(np.fromfile(str(path), np.uint8), 1), cv2.COLOR_BGR2RGB)

    def test_real_shifted_search_icon(self):
        rule = deepcopy(CostumeShikigamiAssets.I_SOU_CHECK_IN_5)
        self.assertTrue(rule.match(self.frame()))
        self.assertLess(abs(rule.roi_front[0] - 320), 3)

    def test_original_position_remains_supported(self):
        rule = deepcopy(CostumeShikigamiAssets.I_SOU_CHECK_IN_5)
        frame = np.zeros((720, 1280, 3), np.uint8)
        h, w = rule.image.shape[:2]
        frame[74:74+h, 268:268+w] = rule.image
        self.assertTrue(rule.match(frame))

    def test_other_header_controls_do_not_replace_missing_search_icon(self):
        frame = self.frame()
        frame[60:135, 310:375] = 0
        self.assertFalse(deepcopy(CostumeShikigamiAssets.I_SOU_CHECK_IN_5).match(frame))


if __name__ == "__main__":
    unittest.main()
