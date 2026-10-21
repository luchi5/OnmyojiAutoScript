from datetime import datetime, timezone
import unittest

from tasks.DraftDuel.rules import GAME_TIMEZONE, next_closed_retry, opening_window


class OpeningRulesTests(unittest.TestCase):
    def at(self, day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=GAME_TIMEZONE)

    def test_real_sunday_early_morning_opens_same_day(self):
        result = opening_window(self.at(18, 3, 20))
        self.assertFalse(result.is_scheduled_open)
        self.assertEqual(result.next_opens_at, self.at(18, 8))

    def test_weekday_and_weekend_boundaries(self):
        self.assertFalse(opening_window(self.at(19, 9, 59)).is_scheduled_open)
        self.assertTrue(opening_window(self.at(19, 10)).is_scheduled_open)
        self.assertTrue(opening_window(self.at(18, 8)).is_scheduled_open)
        self.assertFalse(opening_window(self.at(18, 23)).is_scheduled_open)
        self.assertEqual(opening_window(self.at(18, 23)).next_opens_at, self.at(19, 10))
        self.assertEqual(opening_window(self.at(16, 23)).next_opens_at, self.at(17, 8))

    def test_utc_is_converted_and_naive_time_is_rejected(self):
        result = opening_window(datetime(2026, 10, 18, 0, tzinfo=timezone.utc))
        self.assertTrue(result.is_scheduled_open)
        with self.assertRaises(ValueError):
            opening_window(datetime(2026, 10, 18, 8))

    def test_closed_in_nominal_hours_does_not_admit_or_spin(self):
        self.assertEqual(next_closed_retry(self.at(18, 12)), self.at(18, 12, 15))
        self.assertEqual(next_closed_retry(self.at(18, 22, 59)), self.at(19, 10))


if __name__ == "__main__":
    unittest.main()
