from datetime import datetime as D, time, timedelta
from types import SimpleNamespace as NS
import unittest
from module.config.manual_wait import next_wait_run

class ManualWaitTests(unittest.TestCase):
    def settings(self, scheduled, at=time(9), days=1, interval=timedelta(days=1)):
        return NS(scheduler=NS(next_run=D.fromisoformat(scheduled), server_update=at,
                              delay_date=days, success_interval=interval))

    def banquet(self, scheduled):
        s=self.settings(scheduled)
        s.guild_banquet_time=NS(day_1='星期五', run_time_1=time(20,25),
                               day_2='星期日', run_time_2=time(20,25))
        return s

    def test_friday_due_banquet_goes_to_sunday(self):
        s=self.banquet('2026-10-16 20:25:00')
        self.assertEqual(next_wait_run('guild_banquet',s,D(2026,10,16,21,24)),D(2026,10,18,20,25))

    def test_future_occurrence_is_skipped(self):
        s=self.banquet('2026-10-16 20:25:00')
        self.assertEqual(next_wait_run('guild_banquet',s,D(2026,10,16,19)),D(2026,10,18,20,25))

    def test_sunday_wraps_to_friday(self):
        s=self.banquet('2026-10-18 20:25:00')
        self.assertEqual(next_wait_run('guild_banquet',s,D(2026,10,18,21)),D(2026,10,23,20,25))

    def test_daily_preserves_clock(self):
        s=self.settings('2026-10-16 18:30:00',time(18,30))
        self.assertEqual(next_wait_run('daily_trifles',s,D(2026,10,16,21,24)),D(2026,10,17,18,30))

    def test_weekly_preserves_phase(self):
        s=self.settings('2026-10-15 18:00:00',time(18),7)
        self.assertEqual(next_wait_run('rich_man',s,D(2026,10,16,21)),D(2026,10,22,18))

    def test_interval_preserves_phase_after_missed_runs(self):
        s=self.settings('2026-10-16 10:00:00',interval=timedelta(hours=6))
        self.assertEqual(next_wait_run('kekkai_utilize',s,D(2026,10,16,21,24)),D(2026,10,16,22))

    def test_abyss_uses_individual_times(self):
        s=self.settings('2026-10-16 19:45:00')
        s.abyss_shadows_time=NS(custom_run_time_friday=time(19,45),
            custom_run_time_saturday=time(19,35),custom_run_time_sunday=time(19,45))
        self.assertEqual(next_wait_run('abyss_shadows',s,D(2026,10,16,21)),D(2026,10,17,19,35))

    def test_no_mutation_or_false_completion(self):
        s=self.banquet('2026-10-16 20:25:00')
        next_wait_run('guild_banquet',s,D(2026,10,16,21))
        self.assertEqual(s.scheduler.next_run,D(2026,10,16,20,25))

if __name__ == '__main__': unittest.main()
