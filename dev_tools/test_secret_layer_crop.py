"""Exercise Secret's layer selection without connecting to a device."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger

ROOT = Path(__file__).resolve().parents[1]


def select_layer(read_jade, gold=30000):
    jade = NS(roi=[327, 230, 23, 24])
    jade.ocr = lambda image: read_jade(jade.roi, image)
    h = NS(device=NS(image=None), screenshot=Mock(), appear=lambda _: False,
           I_CHAT_CLOSE_BUTTON='chat', O_SE_JADE=jade,
           O_SE_NO_PASS=NS(ocr=lambda _: (438, 189, 80, 33)),
           O_SE_GOLD=NS(ocr=lambda _: gold), C_SE_CLICK_LAYER=NS(roi_front=None),
           click=Mock())
    method = extract_methods(ROOT/'tasks/Secret/script_task.py', 'ScriptTask',
                             ('find_battle',), {'RuleOcr': object, 'logger': FakeLogger()})['find_battle']
    return method(h), h


class SecretLayerCropTests(unittest.TestCase):
    def test_full_reward_crop_selects_third_layer(self):
        def read(roi, image):
            self.assertEqual(roi, [320, 226, 34, 28])
            return 21
        layer, h = select_layer(read)
        self.assertEqual(layer, 3)
        h.click.assert_called_once()

    def test_non_multiple_of_seven_does_not_select_wrong_layer(self):
        for number in (2, 20, 22, 71):
            layer, h = select_layer(lambda *_: number)
            self.assertIsNone(layer)
            h.click.assert_not_called()

    def test_all_valid_reward_counts(self):
        for layer in range(1, 11):
            actual, _ = select_layer(lambda *_: layer * 7)
            self.assertEqual(actual, layer)


if __name__ == '__main__':
    unittest.main()
