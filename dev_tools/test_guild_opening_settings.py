"""Offline visible-field, legacy migration and actual task scheduling checks."""
from datetime import time, timedelta
import json
from pathlib import Path
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock

from pydantic import ValidationError
from module.config.config_model import ConfigModel
from tasks.Dokan.config import Dokan
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from dev_tools.test_dokan_opening_window import Harness
from tests.test_weekly_guild_opening import fake_task, TaskEnd
from tasks.CollectiveMissions.config import CollectiveMissions
from dev_tools.migrate_guild_opening_settings import migrated_settings


ROOT = Path(__file__).resolve().parents[1]
TASKS = (
    ('Dokan', 'dokan', 'dokan_config', Dokan),
    ('GuildBanquet', 'guild_banquet', 'guild_banquet_time', GuildBanquet),
    ('DemonRetreat', 'demon_retreat', 'demon_retreat_time', DemonRetreat),
)


class VisibleOpeningTests(unittest.TestCase):
    def test_legacy_accounts_keep_real_clock_and_retry(self):
        for name, _, group, cls in TASKS:
            with self.subTest(task=name):
                task = cls(scheduler={'server_update': '19:15:00',
                                      'failure_interval': '00 00:05:00'})
                settings = getattr(task, group)
                self.assertEqual(settings.opening_wait_minutes, 60)
                self.assertEqual(settings.opening_retry_interval, timedelta(minutes=5))
                if name == 'Dokan':
                    self.assertEqual(settings.dokan_run_time, time(19, 15))

    def test_mainline_sentinel_is_not_a_new_morning_opening(self):
        task = Dokan(scheduler={'server_update': '09:00:00',
                               'failure_interval': '01 00:00:00'})
        self.assertEqual(task.dokan_config.dokan_run_time, time(19))
        self.assertEqual(task.dokan_config.opening_retry_interval, timedelta(minutes=3))

    def test_explicit_settings_override_legacy_fields(self):
        task = Dokan(scheduler={'server_update': '19:15:00',
                               'failure_interval': '00 00:05:00'},
                     dokan_config={'dokan_run_time': '20:00:00',
                                   'opening_wait_minutes': 30,
                                   'opening_retry_interval': '00 00:04:00'})
        self.assertEqual(task.dokan_config.dokan_run_time, time(20))
        self.assertEqual(task.dokan_config.opening_retry_interval, timedelta(minutes=4))
        self.assertEqual(task.dokan_config.opening_wait_minutes, 30)

    def test_real_args_show_controls_and_hide_obsolete_force_controls(self):
        for name, key, group, cls in TASKS:
            with self.subTest(task=name):
                args = ConfigModel.script_task(S(**{key: cls()}), name)
                fields = {field['name']: field for field in args[group]}
                self.assertEqual(fields['opening_wait_minutes']['type'], 'integer')
                self.assertEqual(fields['opening_retry_interval']['type'], 'time_delta')
                scheduler_names = {f['name'] for f in args['scheduler']}
                self.assertNotIn('server_update', scheduler_names)
                self.assertNotIn('failure_interval', scheduler_names)
                if name == 'Dokan':
                    self.assertEqual(fields['dokan_run_time']['type'], 'time')
                elif name == 'GuildBanquet':
                    self.assertEqual(fields['run_time_1']['type'], 'time')
                    self.assertEqual(fields['run_time_2']['type'], 'time')
                else:
                    self.assertEqual(fields['custom_run_time']['type'], 'time')

    def test_bad_ui_assignment_is_rejected_without_changing_the_value(self):
        for name, _, group, cls in TASKS:
            settings = getattr(cls(), group)
            for bad in (0, -1, 1441):
                with self.subTest(task=name, wait=bad), self.assertRaises(ValidationError):
                    settings.opening_wait_minutes = bad
                self.assertEqual(settings.opening_wait_minutes, 60)
            for bad in (timedelta(0), timedelta(days=1), '00 00:00:00'):
                with self.subTest(task=name, retry=bad), self.assertRaises(ValidationError):
                    settings.opening_retry_interval = bad
                self.assertEqual(settings.opening_retry_interval, timedelta(minutes=3))

    def test_valid_edit_survives_save_and_reload(self):
        for name, _, group, cls in TASKS:
            with self.subTest(task=name):
                task = cls()
                getattr(task, group).opening_wait_minutes = 45
                getattr(task, group).opening_retry_interval = '00 00:04:00'
                restored = cls(**task.model_dump())
                self.assertEqual(getattr(restored, group).opening_wait_minutes, 45)
                self.assertEqual(getattr(restored, group).opening_retry_interval, timedelta(minutes=4))

    def test_dokan_really_uses_new_clock_window_and_interval(self):
        harness = Harness('2026-10-06 20:10:00')
        harness.cfg.dokan_config.dokan_run_time = time(20)
        harness.cfg.dokan_config.opening_retry_interval = timedelta(minutes=4)
        harness.cfg.dokan_config.opening_wait_minutes = 30
        harness.next_run()
        self.assertEqual(harness.target.isoformat(), '2026-10-06T20:14:00')
        ended = Harness('2026-10-06 20:30:00')
        ended.cfg.dokan_config.dokan_run_time = time(20)
        ended.cfg.dokan_config.opening_retry_interval = timedelta(minutes=4)
        ended.cfg.dokan_config.opening_wait_minutes = 30
        ended.next_run()
        self.assertEqual(ended.target.isoformat(), '2026-10-07T20:00:00')

    def test_weekly_tasks_really_use_new_interval_and_window(self):
        for name, moment, target, next_event in (
            ('GuildBanquet', '2026-10-07T20:28:00', '2026-10-07T20:30:00', '2026-10-10T20:00:00'),
            ('DemonRetreat', '2026-10-10T10:28:00', '2026-10-10T10:30:00', '2026-10-17T10:00:00'),
        ):
            task, _ = fake_task(name, moment)
            cfg = (task.config.guild_banquet.guild_banquet_time if name == 'GuildBanquet'
                   else task.config.demon_retreat.demon_retreat_time)
            cfg.opening_wait_minutes = 30
            cfg.opening_retry_interval = timedelta(minutes=4)
            plan = task._opening_plan()
            self.assertEqual(plan.target.isoformat(), target)
            expired = task._opening_plan(now=plan.deadline)
            self.assertEqual(expired.target.isoformat(), next_event)

    def test_only_final_confirmed_dokan_adds_collective_to_queue(self):
        harness = Harness('2026-10-06 19:31:48', remaining=0)
        harness.cfg.attack_count_config.attack_date = '2026-10-06'
        missions = S(run_after_dokan=True, monday_to_thursday=True,
                     dokan_finished_date='', attempted_date='', completed_date='',
                     pending_kind='', pending_until='')
        harness.config.collective_missions = S(scheduler=S(enable=True), missions_config=missions)
        harness.config.task_call = Mock(return_value=True)
        harness.next_run(is_dokan_activated=True)
        harness.config.task_call.assert_called_once_with('CollectiveMissions', force_call=False)
        self.assertEqual(missions.dokan_finished_date, '2026-10-06')
        harness.next_run(is_dokan_activated=True)
        self.assertEqual(harness.config.task_call.call_count, 1)

    def test_second_pending_unknown_and_skip_dokan_never_queue_collective(self):
        for remaining, activated, skip in ((1, True, False), (0, False, False), (0, True, True)):
            harness = Harness('2026-10-06 19:31:48', remaining=remaining)
            harness.cfg.attack_count_config.attack_date = '2026-10-06'
            harness.config.collective_missions = S(
                scheduler=S(enable=True),
                missions_config=S(run_after_dokan=True, dokan_finished_date='',
                                  attempted_date='', completed_date=''))
            harness.config.task_call = Mock(return_value=True)
            harness.next_run(is_dokan_activated=activated, skip_today=skip)
            harness.config.task_call.assert_not_called()

    def test_chinese_labels_and_descriptions_exist_for_visible_new_fields(self):
        translations = json.loads((ROOT / 'assets/i18n/zh-CN.json').read_text('utf-8'))
        for name in ('dokan_run_time', 'opening_wait_minutes', 'opening_retry_interval',
                     'run_after_dokan'):
            self.assertIn(name, translations)
            self.assertIn(name + '_help', translations)
            self.assertNotEqual(translations[name], name)

    def test_deployment_plan_keeps_other_tasks_counts_and_enable_flags(self):
        before = {'untouched': {'token': 'synthetic', 'counts': [1, 2, 3]},
                  'dokan': Dokan(scheduler={'server_update': '19:15:00',
                                            'failure_interval': '00 00:05:00'}).model_dump(),
                  'guild_banquet': GuildBanquet().model_dump(),
                  'demon_retreat': DemonRetreat().model_dump(),
                  'collective_missions': CollectiveMissions().model_dump()}
        before['dokan']['scheduler']['enable'] = True
        before['dokan']['attack_count_config']['remain_attack_count'] = 1
        before['collective_missions']['scheduler']['enable'] = False
        after = migrated_settings(before)
        self.assertEqual(after['untouched'], before['untouched'])
        for key in ('dokan', 'guild_banquet', 'demon_retreat'):
            self.assertEqual(after[key]['scheduler'], before[key]['scheduler'])
        self.assertEqual(after['dokan']['attack_count_config'], before['dokan']['attack_count_config'])
        self.assertFalse(after['collective_missions']['scheduler']['enable'])
        self.assertTrue(after['collective_missions']['missions_config']['run_after_dokan'])
        self.assertTrue(after['collective_missions']['missions_config']['monday_to_thursday'])
        self.assertEqual(after['collective_missions']['scheduler']['next_run'], '2099-01-01 00:00:00')
        self.assertFalse(before['collective_missions']['missions_config']['run_after_dokan'])


if __name__ == '__main__':
    unittest.main()
