"""Exercise real normal/smart Abyss loops against synthetic enemy areas.

Uses actual quota, enemy, smart-switch and battle-return methods from AST.
No project runtime, configuration file, notification or emulator is loaded.
"""
import ast
import copy
from datetime import datetime
from enum import Enum
from pathlib import Path
import sys
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/AbyssShadows/script_task.py'


class TaskEnd(Exception):
    pass


class Clock(datetime):
    current = datetime(2026, 10, 9, 19, 40)

    @classmethod
    def now(cls):
        return cls.current


class Marker(str):
    @property
    def name(self):
        return str(self)


class Enemy(Enum):
    BOSS = 1
    GENERAL = 2
    ELITE = 3


AREAS = S(DRAGON=Marker('dragon'), PEACOCK=Marker('peacock'),
          FOX=Marker('fox'), LEOPARD=Marker('leopard'))
CLICKS = S(BOSS=Marker('boss'), GENERAL_1=Marker('general1'), GENERAL_2=Marker('general2'),
           ELITE_1=Marker('elite1'), ELITE_2=Marker('elite2'), ELITE_3=Marker('elite3'))


def load_task():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
    selected = copy.deepcopy(original)
    selected.bases = []
    keep = {'run', 'fight_and_switch', 'switch_area', 'find_enemy', 'run_boss_fight',
            'run_general_fight', 'run_elite_fight', 'run_general_battle_back'}
    selected.body = [n for n in selected.body if isinstance(n, ast.FunctionDef) and n.name in keep]
    env = {'datetime': Clock, 'logger': Mock(), 'TaskEnd': TaskEnd, 'AbyssShadows': object,
           'AreaType': AREAS, 'CilckArea': CLICKS, 'EmemyType': Enemy,
           'page_shikigami_records': 'shikigami_records', 'print': Mock(),
           'time': S(time=Mock(return_value=100.0))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[])),
                 str(SOURCE), 'exec'), env)
    return env['ScriptTask']


ACTUAL = load_task()


def runtime(*, smart=False, enemies=True, stale_counts=False):
    task = ACTUAL()
    task.config = S(abyss_shadows=S(
        switch_soul_config=S(enable=False, enable_switch_by_name=False),
        abyss_shadows_combat_time=S(CombatTime_enable=smart, boss_combat_time=0,
                                    general_combat_time=0, elite_combat_time=0),
        abyss_shadows_time=S(custom_run_time_friday='fri', custom_run_time_saturday='sat',
                             custom_run_time_sunday='sun'),
    ))
    task.boss_fight_count, task.general_fight_count, task.elite_fight_count = (
        (2, 4, 6) if stale_counts else (0, 0, 0))
    task.device = Mock()
    task.screenshot = Mock()
    task.goto_page = Mock()
    task.goto_abyss_shadows = Mock()
    task.select_boss = Mock(return_value=True)
    task.wait_until_disappear = Mock()
    task.wait_until_appear = Mock(return_value=False)
    task.goto_main = Mock()
    task.custom_next_run = Mock()
    task.set_next_run = Mock()
    task.current_area = AREAS.DRAGON
    task.change_area = Mock(side_effect=lambda area: setattr(task, 'current_area', area))
    task.check_current_area = lambda: task.current_area
    task.battles = []
    for name in ('WAIT_TO_START', 'ABYSS_MAP_EXIT', 'ABYSS_MAP', 'ABYSS_NAVIGATION',
                 'EQUIPPING', 'WIN', 'EXIT', 'EXIT_ENSURE'):
        setattr(task, 'I_' + name, Marker(name))
    task.appear = Mock(side_effect=lambda marker: marker in (task.I_ABYSS_MAP, task.I_ABYSS_NAVIGATION))
    task.appear_then_click = Mock(side_effect=lambda marker, **kw: marker == task.I_WIN)
    used = set()

    def available(marker):
        target = (task.current_area, marker)
        if not enemies or target in used:
            return False
        used.add(target)
        task.battles.append(target)
        return True

    task.click_emeny_area = Mock(side_effect=available)
    return task


class AbyssCloseoutEvents(unittest.TestCase):
    def setUp(self):
        Clock.current = datetime(2026, 10, 9, 19, 40)
        self.report = Mock()
        self.patch = patch.dict(sys.modules, {
            'tasks.Component.daily_closeout': S(report_closeout_outcome=self.report),
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_to_end(self, task):
        with self.assertRaises(TaskEnd):
            task.run()

    def assert_outcome(self, outcome):
        self.report.assert_called_once()
        self.assertEqual(self.report.call_args.args[1:], ('AbyssShadows', outcome))

    def test_normal_actual_two_four_six_reports_completed(self):
        task = runtime()
        self.run_to_end(task)
        self.assert_outcome('completed')
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 2, 'GENERAL': 4, 'ELITE': 6})
        self.assertEqual(len(task.battles), 12)
        task.goto_main.assert_called_once()
        task.custom_next_run.assert_called_once_with(
            task='AbyssShadows', custom_time='sat', time_delta=1)

    def test_smart_actual_two_four_six_reports_completed(self):
        task = runtime(smart=True)
        self.run_to_end(task)
        self.assert_outcome('completed')
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 2, 'GENERAL': 4, 'ELITE': 6})
        self.assertEqual(len(task.battles), 12)

    def test_normal_exhausted_targets_are_skipped_not_completed(self):
        task = runtime(enemies=False)
        self.run_to_end(task)
        self.assert_outcome('skipped')
        self.assertIn('boss=0/2', self.report.call_args.kwargs['detail'])
        task.custom_next_run.assert_called_once_with(
            task='AbyssShadows', custom_time='sat', time_delta=1)

    def test_smart_empty_target_returns_do_not_prove_general_or_elite_quota(self):
        task = runtime(smart=True, enemies=False)
        self.run_to_end(task)
        self.assert_outcome('skipped')
        # Retain old behavior, but never use these virtual increments as proof.
        self.assertEqual((task.general_fight_count, task.elite_fight_count), (4, 6))
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 0, 'GENERAL': 0, 'ELITE': 0})
        self.assertIn('general=0/4', self.report.call_args.kwargs['detail'])

    def test_smart_partial_target_virtual_quota_is_not_completed(self):
        task = runtime(smart=True)
        actual_click = task.click_emeny_area.side_effect
        task.click_emeny_area.side_effect = lambda marker: (
            False if marker in (CLICKS.GENERAL_2, CLICKS.ELITE_2, CLICKS.ELITE_3)
            else actual_click(marker))
        self.run_to_end(task)
        self.assert_outcome('skipped')
        self.assertEqual((task.boss_fight_count, task.general_fight_count,
                          task.elite_fight_count), (2, 4, 6))
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 2, 'GENERAL': 2, 'ELITE': 2})

    def test_both_modes_reset_closeout_proof_despite_stale_task_counts(self):
        for smart in (False, True):
            with self.subTest(smart=smart):
                self.report.reset_mock()
                task = runtime(smart=smart, stale_counts=True)
                task._closeout_fight_counts = {'BOSS': 2, 'GENERAL': 4, 'ELITE': 6}
                self.run_to_end(task)
                self.assert_outcome('skipped')
                self.assertEqual(task._closeout_fight_counts, {'BOSS': 0, 'GENERAL': 0, 'ELITE': 0})
                self.assertEqual(task.battles, [])

    def test_failed_entry_keeps_retry_without_terminal_event(self):
        task = runtime()
        task.select_boss.return_value = False
        self.run_to_end(task)
        self.report.assert_not_called()
        task.set_next_run.assert_called_once_with(
            task='AbyssShadows', finish=False, server=True, success=False)
        task.custom_next_run.assert_not_called()

    def test_weekday_does_not_emit_completion_or_skipped(self):
        Clock.current = datetime(2026, 10, 7, 19, 40)
        task = runtime()
        self.run_to_end(task)
        self.report.assert_not_called()
        task.goto_abyss_shadows.assert_not_called()
        task.custom_next_run.assert_called_once_with(
            task='AbyssShadows', custom_time='fri', time_delta=2)

    def test_return_to_main_must_finish_before_completion_event(self):
        task = runtime()
        task.goto_main.side_effect = RuntimeError('navigation interrupted')
        with self.assertRaisesRegex(RuntimeError, 'navigation interrupted'):
            task.run()
        self.report.assert_not_called()
        task.custom_next_run.assert_not_called()

    def test_next_run_save_must_finish_before_completion_event(self):
        task = runtime()
        task.custom_next_run.side_effect = RuntimeError('schedule save failed')
        with self.assertRaisesRegex(RuntimeError, 'schedule save failed'):
            task.run()
        self.report.assert_not_called()

    def test_interrupted_real_battle_does_not_create_completion_event(self):
        task = runtime()
        task.wait_until_appear.side_effect = RuntimeError('battle interrupted')
        with self.assertRaisesRegex(RuntimeError, 'battle interrupted'):
            task.run()
        self.report.assert_not_called()
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 0, 'GENERAL': 0, 'ELITE': 0})

    def test_navigation_return_battle_path_records_once(self):
        task = runtime()
        task._closeout_fight_counts = {'BOSS': 0, 'GENERAL': 0, 'ELITE': 0}
        task.appear_then_click.return_value = False
        task.appear_then_click.side_effect = None
        self.assertTrue(task.run_general_battle_back('GENERAL'))
        self.assertEqual(task._closeout_fight_counts, {'BOSS': 0, 'GENERAL': 1, 'ELITE': 0})

    def test_saturday_and_sunday_keep_original_next_day_plans(self):
        for day, time_name, delay in ((10, 'sun', 1), (11, 'fri', 5)):
            with self.subTest(day=day):
                self.report.reset_mock()
                Clock.current = datetime(2026, 10, day, 19, 40)
                task = runtime()
                self.run_to_end(task)
                self.assert_outcome('completed')
                task.custom_next_run.assert_called_once_with(
                    task='AbyssShadows', custom_time=time_name, time_delta=delay)

    def test_full_quota_crossing_midnight_does_not_prove_tomorrow_complete(self):
        task = runtime()
        task.goto_main.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 10, 0, 1))
        self.run_to_end(task)
        self.report.assert_not_called()
        task.custom_next_run.assert_called_once_with(
            task='AbyssShadows', custom_time='sat', time_delta=1)

    def test_exhausted_targets_crossing_midnight_does_not_mark_tomorrow_skipped(self):
        task = runtime(enemies=False)
        task.goto_main.side_effect = lambda: setattr(Clock, 'current', datetime(2026, 10, 10, 0, 1))
        self.run_to_end(task)
        self.report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
