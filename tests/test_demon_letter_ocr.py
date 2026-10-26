"""Read isolated numeric options without blocking the ordinary answer flow."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import numpy as np

from tasks.DemonEncounter.letter_ocr import read_letter_option


class LetterOptionTests(unittest.TestCase):
    def rule(self, text='', readings=()):
        return SimpleNamespace(roi=(424, 261, 450, 74), score=0.6, detect_text=Mock(return_value=text),
                               model=SimpleNamespace(ocr_single_line=Mock(side_effect=readings)))

    def setUp(self):
        self.image = np.full((720, 1280, 3), 200, np.uint8)

    def test_existing_text_does_not_use_numeric_fallback(self):
        rule = self.rule(text='良品')
        self.assertEqual(read_letter_option(self.image, rule), '良品')
        rule.model.ocr_single_line.assert_not_called()

    def test_isolated_digits_use_one_recognition_pass(self):
        for digit in ('6', '8', '9', '12', '100'):
            with self.subTest(digit=digit):
                rule = self.rule(readings=[(digit, .88)])
                self.assertEqual(read_letter_option(self.image, rule), digit)
                self.assertEqual(rule.model.ocr_single_line.call_count, 1)

    def test_uncertain_or_nonnumeric_fallback_stays_unknown(self):
        for readings in ([('8', .5)], [('', .99)], [('EOA10000', .99)], [('上等', .99)]):
            with self.subTest(readings=readings):
                self.assertEqual(read_letter_option(self.image, self.rule(readings=readings)), '')


if __name__ == '__main__':
    unittest.main()
