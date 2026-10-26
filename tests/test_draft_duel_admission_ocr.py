"""Digit loss must not silently reduce the price charged to the budget."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from tasks.DraftDuel.workflow import read_offer


class AdmissionDigitTests(unittest.TestCase):
    def offer(self, results):
        vision = Mock()
        vision.read.return_value = SimpleNamespace(kind="team_ready")
        vision.match.return_value = (1193, 616)
        vision.ocr.side_effect = results
        return read_offer(vision, np.zeros((720, 1280, 3), np.uint8), "member", "leader")

    def test_missing_zero_is_not_a_cheaper_admission(self):
        self.assertIsNone(self.offer([("3万", .99)] * 8).cost)

    def test_single_confident_read_is_not_enough(self):
        self.assertIsNone(self.offer([("30万", .99)] + [("", 0)] * 7).cost)

    def test_conflicting_complete_prices_abstain(self):
        self.assertIsNone(self.offer([("30万", .99)] * 4 + [("10万", .99)] * 4).cost)

    def test_unsupported_digit_result_invalidates_other_views(self):
        self.assertIsNone(self.offer([("30万", .99)] * 7 + [("3万", .99)]).cost)

    def test_discount_crop_requires_two_complete_current_price_reads(self):
        offer = self.offer([("30万10万", .99)] * 6 + [("10万", .99)] * 2)
        self.assertEqual(offer.cost, 100000)

    def test_free_requires_repeated_positive_text(self):
        self.assertEqual(self.offer([("本次免费", .99)] * 8).cost, 0)
        self.assertIsNone(self.offer([("0万", .99)] * 8).cost)


if __name__ == "__main__":
    unittest.main()
