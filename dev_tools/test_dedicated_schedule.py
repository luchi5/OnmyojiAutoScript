"""Dedicated clock UI and real scheduler regression tests, without a device."""
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace as S
import unittest

from module.config.config_model import ConfigModel
from tasks.Hunt.config import Hunt
from tasks.AbyssShadows.config import AbyssShadows
from tasks.Dokan.config import Dokan
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from tasks.MysteryShop.config import MysteryShop
from tasks.MysteryShop.schedule import next_shop_run
from tasks.RyouToppa.config import RyouToppa
from tasks.Component.config_scheduler import Scheduler
from dev_tools.test_dokan_opening_window import Harness
from dev_tools.test_bondling_battle_entry import extract_methods

ROOT = Path(__file__).resolve().parents[1]
CLASSES = [('Hunt', 'hunt', Hunt), ('AbyssShadows', 'abyss_shadows', AbyssShadows),
           ('Dokan', 'dokan', Dokan), ('GuildBanquet', 'guild_banquet', GuildBanquet),
           ('DemonRetreat', 'demon_retreat', DemonRetreat),
           ('MysteryShop', 'mystery_shop', MysteryShop)]


class DedicatedScheduleTests(unittest.TestCase):
    def test_settings_hide_conflicting_controls_but_keep_manual_next_run(self):
        for name, key, cls in CLASSES:
            with self.subTest(task=name):
                args = ConfigModel.script_task(S(**{key: cls()}), name)
                self.assertEqual({f['name'] for f in args['scheduler']},
                                 {'enable', 'next_run', 'priority'})

    def test_old_force_time_intervals_and_jitter_cannot_override_target(self):
        for _, _, cls in CLASSES:
            with self.subTest(task=cls.__name__):
                h = Harness('2026-10-22 20:00:00')
                h.cfg.scheduler = cls(scheduler={
                    'server_update': '23:00:00', 'delay_date': 7,
                    'float_time': '02:00:00', 'success_interval': '00 00:01:00',
                    'failure_interval': '07 00:00:00'}).scheduler
                target = datetime(2026, 10, 23, 19, 30)
                h.config.task_delay(task='Dokan', target=target, success=True, server=True)
                self.assertEqual(h.cfg.scheduler.next_run, target)

    def test_short_retry_not_replaced_with_next_day(self):
        h = Harness('2026-10-19 20:00:00')
        h.cfg.scheduler = Dokan(scheduler={'server_update': '23:00:00'}).scheduler
        target = h.now + timedelta(minutes=3)
        h.config.task_delay(task='Dokan', target=target, server=True)
        self.assertEqual(h.cfg.scheduler.next_run, target)

    def test_ordinary_scheduler_still_honors_force_time(self):
        h = Harness('2026-10-19 20:00:00')
        h.cfg.scheduler = Scheduler(server_update='23:00:00')
        with self.assertRaisesRegex(AssertionError, 'server override'):
            h.config.task_delay(task='Dokan', success=True, server=True)

    def test_abyss_retry_migrates_once_and_explicit_setting_wins(self):
        old = {'scheduler': {'failure_interval': '00 00:05:00'}}
        task = AbyssShadows(**old)
        self.assertEqual(task.abyss_shadows_time.opening_retry_interval, timedelta(minutes=5))
        saved = task.model_dump()
        saved['scheduler']['failure_interval'] = '01 00:00:00'
        self.assertEqual(AbyssShadows(**saved).abyss_shadows_time.opening_retry_interval,
                         timedelta(minutes=5))
        self.assertEqual(AbyssShadows(scheduler={'failure_interval': '01 00:00:00'})
                         .abyss_shadows_time.opening_retry_interval, timedelta(minutes=3))

    def test_hunt_thursday_friday_and_sunday_monday_clocks(self):
        for moment, kirin, expected in [('2026-10-22 20:00:00', False, datetime(2026,10,23,20)),
                                         ('2026-10-25 20:00:00', True, datetime(2026,10,26,19))]:
            now = datetime.fromisoformat(moment)
            class Frozen(datetime):
                @classmethod
                def now(cls): return now
            calls = []
            obj = S(con_time=S(kirin_time=time(19), netherworld_time=time(20)),
                    tomorrow_kirin_day=kirin, set_next_run=lambda *a, **kw: calls.append(kw))
            obj.custom_next_run = extract_methods(ROOT/'tasks/base_task.py', 'BaseTask',
                ('custom_next_run',), {'datetime': Frozen, 'timedelta': timedelta, 'Time': time})['custom_next_run'].__get__(obj)
            plan = extract_methods(ROOT/'tasks/Hunt/script_task.py', 'ScriptTask',
                ('plan_tomorrow_hunt',), {'logger': S(info=lambda *a: None)})['plan_tomorrow_hunt']
            plan(obj)
            self.assertEqual(calls[0]['target'], expected)
            self.assertFalse(calls[0]['server'])

    def test_shop_weekdays_and_legacy_default(self):
        self.assertEqual(next_shop_run(datetime(2026,10,19,20), time(19)), datetime(2026,10,21,19))
        self.assertEqual(next_shop_run(datetime(2026,10,21,18), time(19)), datetime(2026,10,21,19))
        self.assertEqual(next_shop_run(datetime(2026,10,21,20), time(19), True), datetime(2026,10,24,19))
        task = MysteryShop(scheduler={'server_update':'19:00:00'}, shop_config={'time_of_mystery':'00:00:00'})
        self.assertEqual(task.shop_config.time_of_mystery, time(19))
        explicit = MysteryShop(scheduler={'server_update':'23:00:00'}, shop_config={'time_of_mystery':'18:00:00'})
        self.assertEqual(explicit.shop_config.time_of_mystery, time(18))

    def test_raid_keeps_repeat_intervals_only(self):
        args = ConfigModel.script_task(S(ryou_toppa=RyouToppa()), 'RyouToppa')
        self.assertEqual({f['name'] for f in args['scheduler']},
                         {'enable','next_run','priority','success_interval','failure_interval'})


if __name__ == '__main__':
    unittest.main()
