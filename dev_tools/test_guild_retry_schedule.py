"""Pure guild calendar regressions; no backend, OCR or game controls."""
from datetime import datetime, time, timedelta, timezone
import unittest

from tasks.Component.guild_retry_schedule import plan_opening_check, retry_delay


class OpeningCalendarTests(unittest.TestCase):
    def test_before_start_stays_on_today(self):
        plan = plan_opening_check(datetime(2026, 10, 10, 9, 55), [(5, time(10))], timedelta(minutes=5))
        self.assertEqual(plan.status, 'before_start')
        self.assertEqual(plan.target, datetime(2026, 10, 10, 10))

    def test_exact_start_retries_in_current_window(self):
        plan = plan_opening_check(datetime(2026, 10, 10, 10), [(5, time(10))], timedelta(minutes=3))
        self.assertTrue(plan.in_window)
        self.assertEqual(plan.target, datetime(2026, 10, 10, 10, 3))

    def test_last_retry_is_capped_at_deadline(self):
        plan = plan_opening_check(datetime(2026, 10, 10, 10, 59), [(5, time(10))], timedelta(minutes=5))
        self.assertEqual(plan.target, datetime(2026, 10, 10, 11))
        self.assertEqual(plan.target, plan.deadline)

    def test_exact_deadline_moves_to_next_valid_week(self):
        plan = plan_opening_check(datetime(2026, 10, 10, 11), [(5, time(10))], timedelta(minutes=5))
        self.assertEqual(plan.status, 'window_expired')
        self.assertFalse(plan.in_window)
        self.assertEqual(plan.target, datetime(2026, 10, 17, 10))

    def test_workday_window_skips_weekend(self):
        slots = [(day, time(19)) for day in range(4)]
        plan = plan_opening_check(datetime(2026, 10, 8, 20), slots, timedelta(minutes=3))
        self.assertEqual(plan.target, datetime(2026, 10, 12, 19))

    def test_completion_does_not_reenter_current_window(self):
        slots = [(day, time(19)) for day in range(7)]
        plan = plan_opening_check(datetime(2026, 10, 6, 19, 15), slots, timedelta(minutes=3), completed=True)
        self.assertEqual(plan.status, 'completed')
        self.assertEqual(plan.target, datetime(2026, 10, 7, 19))

    def test_weekly_banquet_chooses_next_of_two_days(self):
        slots = [(4, time(19, 25)), (6, time(19, 25))]
        plan = plan_opening_check(datetime(2026, 10, 9, 19, 50), slots, timedelta(minutes=3), completed=True)
        self.assertEqual(plan.target, datetime(2026, 10, 11, 19, 25))

    def test_two_slots_on_same_day_remain_separate(self):
        slots = [(4, time(19)), (4, time(21))]
        plan = plan_opening_check(datetime(2026, 10, 9, 20), slots, timedelta(minutes=5))
        self.assertEqual(plan.target, datetime(2026, 10, 9, 21))

    def test_early_wrong_day_does_not_start_activity(self):
        plan = plan_opening_check(datetime(2026, 10, 6, 19), [(5, time(10))], timedelta(minutes=3))
        self.assertEqual(plan.status, 'before_start')
        self.assertEqual(plan.target, datetime(2026, 10, 10, 10))

    def test_cross_midnight_window_uses_original_event(self):
        plan = plan_opening_check(datetime(2026, 10, 11, 0, 10), [(5, time(23, 45))], timedelta(minutes=5))
        self.assertTrue(plan.in_window)
        self.assertEqual(plan.start, datetime(2026, 10, 10, 23, 45))
        self.assertEqual(plan.deadline, datetime(2026, 10, 11, 0, 45))

    def test_oversized_interval_cannot_escape_window(self):
        plan = plan_opening_check(datetime(2026, 10, 10, 10, 5), [(5, time(10))], timedelta(days=1))
        self.assertEqual(plan.target, datetime(2026, 10, 10, 11))

    def test_invalid_or_zero_intervals_use_positive_fallback(self):
        for interval in (None, 'bad', timedelta(0), timedelta(seconds=-1)):
            self.assertEqual(retry_delay(interval), timedelta(minutes=3))

    def test_saved_interval_strings_are_understood(self):
        self.assertEqual(retry_delay('00 00:05:00'), timedelta(minutes=5))

    def test_seconds_and_time_zone_are_preserved(self):
        tz = timezone(timedelta(hours=8))
        now = datetime(2026, 10, 10, 10, 1, 20, tzinfo=tz)
        plan = plan_opening_check(now, [(5, time(10))], timedelta(minutes=3))
        self.assertEqual(plan.target, datetime(2026, 10, 10, 10, 4, 20, tzinfo=tz))

    def test_invalid_slots_and_windows_are_rejected(self):
        for slots in ([], [(7, time(10))], [(5, '10:00')]):
            with self.assertRaises(ValueError):
                plan_opening_check(datetime(2026, 10, 10, 10), slots, timedelta(minutes=3))
        for window in (timedelta(0), timedelta(days=2)):
            with self.assertRaises(ValueError):
                plan_opening_check(datetime(2026, 10, 10, 10), [(5, time(10))], timedelta(minutes=3), window=window)


if __name__ == '__main__':
    unittest.main()
