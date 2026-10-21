import unittest
from pathlib import Path

import cv2
import numpy as np

from tasks.KekkaiActivation.activation_button import ActivationButton


class ActivationButtonTests(unittest.TestCase):
    def frame(self, state):
        path = Path(__file__).parent / 'fixtures' / f'card_button_{state}.png'
        return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)

    def test_selected_yellow_button(self):
        self.assertTrue(ActivationButton().match(self.frame('yellow')))

    def test_unselected_gray_button(self):
        self.assertFalse(ActivationButton().match(self.frame('gray')))

    def test_small_position_changes(self):
        for dx, dy in [(-10, -10), (10, 10)]:
            for state in ('yellow', 'gray'):
                frame = cv2.warpAffine(
                    self.frame(state), np.float32([[1, 0, dx], [0, 1, dy]]),
                    (1280, 720),
                )
                self.assertEqual(ActivationButton().match(frame), state == 'yellow')

    def test_missing_button(self):
        self.assertFalse(ActivationButton().match(np.zeros((720, 1280, 3), np.uint8)))


if __name__ == '__main__':
    unittest.main()
