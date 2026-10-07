"""Offline regressions for enabled ordinary/extreme DemonEncounter soul targets.

Compile the actual production methods and weekday enum from AST. Fake config,
time, navigation and OCR prevent game, backend and notification side effects.
"""
import ast
from datetime import datetime, timedelta
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeLogger


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tasks/DemonEncounter/script_task.py'
CONFIG = ROOT / 'tasks/DemonEncounter/config.py'
WEEKDAYS = ('kiryou_utahime', 'shinkirou', 'tsuchigumo', 'gashadokuro',
            'namazu', 'oboroguruma', 'nightly_aramitama')
MONDAY = datetime(2026, 10, 5, 19, 0, 0)


class TaskEnd(Exception):
    pass


def production_class(namespace):
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8-sig'), filename=str(SCRIPT))
    source = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == 'ScriptTask')
    methods = [node for node in source.body if isinstance(node, ast.FunctionDef)
               and (node.name in ('run', 'boss_type', 'best_demon_enable',
                                  'check_challenge_done') or 'soul' in node.name)]
    required = {'run', 'resolve_soul_target', 'checkout_soul', 'boss_type',
                'best_demon_enable', 'check_challenge_done'}
    missing = required - {node.name for node in methods}
    if missing:
        raise AssertionError('Missing production methods: ' + ', '.join(sorted(missing)))
    task = ast.ClassDef(name='ProductionTask', bases=[], keywords=[], body=methods,
                        decorator_list=[])
    config_tree = ast.parse(CONFIG.read_text(encoding='utf-8-sig'), filename=str(CONFIG))
    boss_enum = next(node for node in config_tree.body
                     if isinstance(node, ast.ClassDef) and node.name == 'BossType')
    module = ast.Module(body=[boss_enum, task], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SCRIPT), 'exec'), namespace)
    return namespace['ProductionTask']


class DemonHarness:
    def __init__(self, day=2, extreme=False, normal_enabled=True,
                 extreme_enabled=False, normal_value=None, extreme_value=None,
                 challenge_count=(1, 0, 1), scheduler_enabled=True):
        self.now = MONDAY + timedelta(days=day)
        self.logger = FakeLogger()
        self.navigation, self.switches, self.delays, self.events = [], [], [], []
        self.time_allowed = True
        self.challenge_count = challenge_count
        ordinary = {'enable': normal_enabled}
        best = {'enable': extreme_enabled}
        boss = {}
        for index, name in enumerate(WEEKDAYS):
            ordinary['demon_' + name] = f'普通{index},普通队{index}'
            best_key = 'best_demon_' + name + ('u' if index == 6 else '')
            best[best_key] = f'极组{index},极队{index}'
            boss['best_demon_' + name + '_select'] = index == day and extreme
        selected = WEEKDAYS[day]
        if normal_value is not None:
            ordinary['demon_' + selected] = normal_value
        if extreme_value is not None:
            best['best_demon_' + selected + ('u' if day == 6 else '')] = extreme_value
        self.conf = SimpleNamespace(
            scheduler=SimpleNamespace(enable=scheduler_enabled),
            demon_soul_config=SimpleNamespace(**ordinary),
            best_demon_soul_config=SimpleNamespace(**best),
            best_demon_boss_config=SimpleNamespace(**boss),
        )
        self.config = SimpleNamespace(demon_encounter=self.conf, config_name='offline-02')
        self.device = SimpleNamespace(image='fake-frame')
        self.O_DE_CHALLENGE_COUNT = SimpleNamespace(ocr=lambda image: self.challenge_count)
        harness = self

        class FrozenDatetime:
            @staticmethod
            def now():
                return harness.now

        namespace = {
            'datetime': FrozenDatetime, 'timedelta': timedelta, 'IntEnum': IntEnum,
            'logger': self.logger, 'TaskEnd': TaskEnd,
            'page_shikigami_records': 'shikigami-records',
            'page_demon_encounter_realworld': 'demon-map',
        }
        task_type = production_class(namespace)
        # Use a dynamic subclass so actual production properties retain normal
        # descriptor semantics; the harness supplies only external dependencies.
        self.__class__ = type('BoundDemonHarness', (DemonHarness, task_type), {})

    def check_time(self):
        self.events.append('check-time')
        return self.time_allowed

    def goto_page(self, page):
        self.navigation.append(page)
        self.events.append(('goto', page))

    def run_switch_soul_by_name(self, group, team):
        self.switches.append((group, team))
        self.events.append(('switch', group, team))

    def execute_lantern(self):
        self.events.append('lantern')

    def execute_boss(self):
        self.events.append('boss')

    def set_next_run(self, **kwargs):
        self.delays.append(kwargs)


class DemonSoulSelectionTests(unittest.TestCase):
    def assert_pending_retry(self, task):
        self.assertEqual(len(task.delays), 1)
        retry = task.delays[0]
        self.assertEqual(retry['task'], 'DemonEncounter')
        self.assertIs(retry['finish'], False)
        self.assertIsNone(retry['success'])
        self.assertIs(retry['server'], False)
        self.assertEqual(retry['target'], task.now + timedelta(minutes=3))
        self.assertEqual(task.navigation, [])
        self.assertEqual(task.switches, [])
        self.assertNotIn('lantern', task.events)
        self.assertNotIn('boss', task.events)

    def test_account_02_extreme_boss_disabled_extreme_souls_uses_existing_ordinary_target(self):
        task = DemonHarness(extreme=True, normal_value='托管2,逢魔通用',
                            extreme_value='group,team')
        self.assertEqual(task.boss_type, 'best_demon_tsuchigumo')
        self.assertEqual(task.resolve_soul_target(), ('托管2', '逢魔通用'))
        self.assertEqual(task.delays, [])

    def test_all_weekdays_and_toggle_combinations_use_only_enabled_matching_configuration(self):
        for day in range(7):
            for extreme in (False, True):
                for ordinary_enabled in (False, True):
                    for best_enabled in (False, True):
                        with self.subTest(day=day, extreme=extreme,
                                          ordinary=ordinary_enabled, best=best_enabled):
                            task = DemonHarness(day, extreme, ordinary_enabled, best_enabled)
                            prefix = 'best_demon_' if extreme else 'demon_'
                            self.assertEqual(task.boss_type, prefix + WEEKDAYS[day])
                            if extreme and best_enabled:
                                expected = (f'极组{day}', f'极队{day}')
                            elif ordinary_enabled:
                                expected = (f'普通{day}', f'普通队{day}')
                            else:
                                expected = None
                            self.assertEqual(task.resolve_soul_target(), expected)
                            self.assertEqual(task.delays, [])

    def test_extreme_target_wins_when_both_soul_configurations_enabled(self):
        task = DemonHarness(extreme=True, extreme_enabled=True)
        self.assertEqual(task.resolve_soul_target(), ('极组2', '极队2'))

    def test_ordinary_boss_with_only_extreme_souls_enabled_skips_switching(self):
        task = DemonHarness(normal_enabled=False, extreme_enabled=True)
        self.assertIsNone(task.resolve_soul_target())
        self.assertEqual(task.delays, [])

    def test_extreme_boss_with_both_soul_switches_disabled_ignores_placeholders(self):
        task = DemonHarness(extreme=True, normal_enabled=False,
                            normal_value='group,team', extreme_value='group,team')
        self.assertIsNone(task.resolve_soul_target())
        self.assertEqual(task.delays, [])

    def test_chinese_comma_and_whitespace_normalize_user_input(self):
        task = DemonHarness(normal_value='  托管2 ， 逢魔通用  ')
        self.assertEqual(task.resolve_soul_target(), ('托管2', '逢魔通用'))

    def test_sunday_legacy_extreme_soul_field_is_resolved(self):
        task = DemonHarness(day=6, extreme=True, extreme_enabled=True)
        self.assertEqual(task.resolve_soul_target(), ('极组6', '极队6'))

    def test_sunday_ordinary_fallback_uses_ordinary_field_without_legacy_suffix(self):
        task = DemonHarness(day=6, extreme=True, extreme_enabled=False)
        self.assertEqual(task.resolve_soul_target(), ('普通6', '普通队6'))

    def test_blank_or_partial_names_are_pending_before_navigation(self):
        for value in ('', ' ', ',', '托管2,', ',逢魔通用', '  ,  '):
            with self.subTest(value=value):
                task = DemonHarness(normal_value=value)
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assert_pending_retry(task)

    def test_wrong_separator_and_extra_names_are_pending_before_navigation(self):
        for value in ('托管2', '托管2;逢魔通用', '托管2,逢魔通用,额外'):
            with self.subTest(value=value):
                task = DemonHarness(normal_value=value)
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assert_pending_retry(task)

    def test_placeholder_names_are_not_searched(self):
        for value in ('group,team', ' GROUP , TEAM ', 'group，team'):
            with self.subTest(value=value):
                task = DemonHarness(normal_value=value)
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assert_pending_retry(task)

    def test_non_string_configuration_is_pending_without_search(self):
        for value in (None, 12, ['托管2', '逢魔通用']):
            with self.subTest(value=value):
                task = DemonHarness()
                task.conf.demon_soul_config.demon_tsuchigumo = value
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assert_pending_retry(task)

    def test_invalid_enabled_extreme_target_does_not_silently_fall_back_to_ordinary(self):
        task = DemonHarness(extreme=True, extreme_enabled=True,
                            normal_value='托管2,逢魔通用', extreme_value='group,team')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_pending_retry(task)

    def test_missing_enabled_boss_target_is_pending(self):
        task = DemonHarness()
        del task.conf.demon_soul_config.demon_tsuchigumo
        with self.assertRaises(TaskEnd):
            task.run()
        self.assert_pending_retry(task)

    def test_invalid_retry_does_not_turn_a_manually_disabled_schedule_on(self):
        task = DemonHarness(normal_value='group,team', scheduler_enabled=False)
        with self.assertRaises(TaskEnd):
            task.resolve_soul_target()
        self.assert_pending_retry(task)
        self.assertIs(task.conf.scheduler.enable, False)

    def test_checkout_soul_can_resolve_the_current_target_for_existing_callers(self):
        task = DemonHarness(extreme=True, normal_value='托管2,逢魔通用')
        task.checkout_soul()
        self.assertEqual(task.switches, [('托管2', '逢魔通用')])

    def test_checkout_soul_with_both_switches_off_does_not_search(self):
        task = DemonHarness(normal_enabled=False, extreme_enabled=False)
        task.checkout_soul()
        self.assertEqual(task.switches, [])

    def test_pre_resolved_checkout_target_is_used_without_reading_disabled_placeholders(self):
        task = DemonHarness(normal_enabled=False, extreme_enabled=False,
                            normal_value='group,team', extreme_value='group,team')
        task.checkout_soul(('托管2', '逢魔通用'))
        self.assertEqual(task.switches, [('托管2', '逢魔通用')])
        self.assertEqual(task.delays, [])

    def test_valid_target_switches_before_map_and_completes_original_lantern_boss_body(self):
        task = DemonHarness(extreme=True, normal_value='托管2,逢魔通用')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(task.events, ['check-time', ('goto', 'shikigami-records'),
                                      ('switch', '托管2', '逢魔通用'), ('goto', 'demon-map'),
                                      'lantern', 'boss'])
        self.assertEqual(task.delays, [{'task': 'DemonEncounter', 'success': True, 'finish': False}])

    def test_no_target_avoids_shikigami_navigation_and_preserves_original_body(self):
        task = DemonHarness(normal_enabled=False, extreme_enabled=True)
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(task.navigation, ['demon-map'])
        self.assertEqual(task.switches, [])
        self.assertEqual(task.events[-2:], ['lantern', 'boss'])
        self.assertEqual(task.delays, [{'task': 'DemonEncounter', 'success': True, 'finish': False}])

    def test_already_challenged_skips_lantern_and_boss_with_original_success_semantics(self):
        for total in (1, 2):
            with self.subTest(total=total):
                task = DemonHarness(normal_enabled=False, challenge_count=(0, total, total))
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assertEqual(task.navigation, ['demon-map'])
                self.assertNotIn('lantern', task.events)
                self.assertNotIn('boss', task.events)
                self.assertEqual(task.delays, [{'task': 'DemonEncounter', 'success': True, 'finish': False}])

    def test_time_outside_window_does_not_resolve_invalid_config_or_navigate(self):
        task = DemonHarness(normal_value='group,team')
        task.time_allowed = False
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(task.events, ['check-time'])
        self.assertEqual(task.navigation, [])
        self.assertEqual(task.delays, [])


if __name__ == '__main__':
    unittest.main()
