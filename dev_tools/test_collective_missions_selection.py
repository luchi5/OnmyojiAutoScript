"""Actual selector/OCR dispatch and Device guard, without a game or OCR model.

Run: toolkit/python.exe -B dev_tools/test_collective_missions_selection.py
"""
import ast
from collections import deque
from enum import Enum
from pathlib import Path
import re
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/CollectiveMissions/script_task.py'


class Clock:
    now = 0


class Timer:
    def __init__(self, seconds):
        self.limit = seconds

    def start(self):
        self.started = Clock.now
        return self

    def reached(self):
        return Clock.now - self.started >= self.limit


class GameTooManyClickError(Exception):
    pass


class AccountLoggedInElsewhere(Exception):
    pass


def load_methods(path, owner, names, env):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), env)
    return {name: env[name] for name in names}


ENV = dict(Enum=Enum, logger=Mock(), Timer=Timer, re=re, RuleOcr=object)
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
enum_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MC')
exec(compile(ast.fix_missing_locations(ast.Module(body=[enum_node], type_ignores=[])), str(SOURCE), 'exec'), ENV)
MC = ENV['MC']
ACTUAL = load_methods(SOURCE, 'ScriptTask',
                      ('_mission_from_setting', '_preferred_mission', 'select_mission',
                       '_collective_click_available', 'detect_one'), ENV)
GUARD = load_methods(ROOT / 'module/device/device.py', 'Device', ('click_record_check',),
                     dict(logger=Mock(), GameTooManyClickError=GameTooManyClickError))['click_record_check']


class Selector:
    """Each frame costs 0.1s; a successful native click changes the next frame."""
    _mission_from_setting = staticmethod(ACTUAL['_mission_from_setting'])
    _preferred_mission = ACTUAL['_preferred_mission']
    select_mission = ACTUAL['select_mission']
    _collective_click_available = ACTUAL['_collective_click_available']

    def __init__(self, missions, rule='觉醒三 > 觉醒二 > 觉醒一', available=True,
                 progress=True, clickable=True, history=()):
        self.config = S(collective_missions=S(missions_config=S(missions_rule=rule)))
        self.missions = missions
        self.position = 0
        self.progress = progress
        self.clickable = clickable
        self.next_click = 0
        self.clicks = []
        self.readings = []
        self.device = S(image=None, click_record=deque(history, maxlen=15), click_record_clear=Mock())
        self.I_CM_SWITCH = S(name='CM_CM_SWITCH',
                             match_brightness=Mock(return_value=available),
                             match_mean_color=Mock(return_value=available))
        self.O_CM_1 = object()
        self.O_CM_2 = object()

    def screenshot(self):
        Clock.now += 0.1

    def detect_one(self, _first, _second):
        value = self.missions[self.position % len(self.missions)]
        self.readings.append(value)
        return value

    def appear_then_click(self, marker, interval):
        if not self.clickable or Clock.now < self.next_click:
            return False
        self.device.click_record.append(marker.name)
        GUARD(self.device)
        self.clicks.append((marker.name, interval))
        self.next_click = Clock.now + interval
        if self.progress:
            self.position += 1
        return True


class OcrSelector(Selector):
    """Use the real OCR dispatcher and selector with scripted card text only."""
    def __init__(self, cards, **kwargs):
        super().__init__(cards, **kwargs)
        self.O_CM_1 = S(ocr=lambda _: self.missions[self.position % len(self.missions)][0])
        self.O_CM_2 = S(ocr=lambda _: self.missions[self.position % len(self.missions)][1])

    def detect_one(self, first, second):
        mission = ACTUAL['detect_one'](self, first, second)
        self.readings.append(mission)
        return mission


class SelectionTests(unittest.TestCase):
    def setUp(self):
        Clock.now = 0

    def test_all_xy_enum_names_and_feed_alias_are_accepted(self):
        for text in ('觉醒一', '觉醒二', '觉醒三', '御灵一', '御灵二', '御灵三', '御魂一', '御魂二'):
            self.assertEqual(Selector._mission_from_setting(text).value, text)
        self.assertIs(Selector._mission_from_setting('养成'), MC.FEED)
        self.assertIs(Selector._mission_from_setting(' 养成 '), MC.FEED)
        self.assertIs(Selector._mission_from_setting('远远不够'), MC.FEED)
        self.assertIs(Selector._mission_from_setting(MC.AW2), MC.AW2)
        self.assertIs(Selector._mission_from_setting(S(value='养成')), MC.FEED)

    def test_blank_legacy_target_uses_first_selectable_priority_without_config_write(self):
        task = Selector([MC.AW3], rule='契灵 > 结伴同行 > 觉醒三 > 觉醒二')
        self.assertTrue(task.select_mission(''))
        self.assertEqual(task.clicks, [])
        self.assertEqual(len(task.readings), 2)
        self.assertEqual(task.config.collective_missions.missions_config.missions_rule,
                         '契灵 > 结伴同行 > 觉醒三 > 觉醒二')

    def test_invalid_target_and_unselectable_rule_use_existing_schema_default_three(self):
        for value in ('御魂三', 'unknown', None):
            task = Selector([MC.AW3], rule='契灵 > 未知')
            self.assertTrue(task.select_mission(value))
            self.assertEqual(task.clicks, [])

    def test_xy_feed_preference_selects_feed_without_refresh(self):
        task = Selector([MC.FEED])
        self.assertTrue(task.select_mission('养成'))
        self.assertEqual(task.clicks, [])

    def test_task_switch_is_confirmed_twice_and_keeps_native_two_second_interval(self):
        task = Selector([MC.AW1, MC.AW2, MC.AW3])
        self.assertTrue(task.select_mission('觉醒三'))
        self.assertEqual(task.clicks, [('CM_CM_SWITCH', 2)] * 2)
        self.assertEqual(task.readings[-2:], [MC.AW3, MC.AW3])
        task.device.click_record_clear.assert_not_called()

    def test_single_matching_ocr_frame_is_not_confirmation(self):
        task = Selector([MC.AW3, MC.AW1], clickable=False)
        sequence = iter([MC.AW3, MC.AW1])
        task.detect_one = lambda *_: next(sequence, MC.AW1)
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(task.clicks, [])

    def test_repeated_card_stops_after_three_clicks_without_triggering_actual_device_guard(self):
        task = Selector([MC.AW1], progress=False)
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(len(task.clicks), 3)
        self.assertLess(Clock.now, 25)
        task.device.click_record_clear.assert_not_called()

    def test_missing_target_cycle_is_bounded_before_actual_device_guard(self):
        task = Selector([MC.AW1, MC.AW2, MC.GR1])
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(len(task.clicks), 8)
        task.device.click_record_clear.assert_not_called()

    def test_previous_refresh_history_stops_new_attempt_without_erasing_it(self):
        history = ['CM_CM_SWITCH'] * 7
        task = Selector([MC.AW1, MC.AW2, MC.GR1], history=history)
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(len(task.clicks), 1)
        self.assertEqual(list(task.device.click_record), history + ['CM_CM_SWITCH'])
        task.device.click_record_clear.assert_not_called()

    def test_two_button_guard_budget_remains_protected(self):
        task = Selector([MC.AW1, MC.AW2], history=['CM_CM_REWARDS'] * 6 + ['CM_CM_SWITCH'] * 5)
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(task.clicks, [])
        task.device.click_record_clear.assert_not_called()

    def test_unknown_card_is_never_blindly_refreshed(self):
        task = Selector([MC.UNKNOWN])
        self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(task.clicks, [])
        self.assertEqual(len(task.readings), 3)

    def test_unavailable_refresh_and_interval_only_are_bounded(self):
        for kwargs in (dict(available=False), dict(clickable=False)):
            task = Selector([MC.AW1], **kwargs)
            self.assertFalse(task.select_mission('觉醒三'))
            self.assertEqual(task.clicks, [])
            self.assertLessEqual(Clock.now, 25.2)
            Clock.now = 0

    def test_owner_takeover_propagates(self):
        task = Selector([MC.AW1])
        task.screenshot = Mock(side_effect=AccountLoggedInElsewhere('owner login'))
        with self.assertRaises(AccountLoggedInElsewhere):
            task.select_mission('觉醒三')

    def test_real_dispatch_refreshes_six_star_card_to_confirmed_feed(self):
        task = OcrSelector([('远远不够·', '御魂三'),
                            ('远远不够·', '觉醒一'),
                            ('远远不够·', '养成')])
        self.assertTrue(task.select_mission('养成'))
        self.assertEqual(task.readings[0], MC.SO3)
        self.assertIn(MC.AW1, task.readings)
        self.assertEqual(task.readings[-2:], [MC.FEED, MC.FEED])
        self.assertEqual(task.clicks, [('CM_CM_SWITCH', 2)] * 2)
        task.device.click_record_clear.assert_not_called()

    def test_real_dispatch_refreshes_six_star_card_to_material_target(self):
        task = OcrSelector([('远远不够', '御魂三'), ('远远不够', '觉醒三')])
        self.assertTrue(task.select_mission('觉醒三'))
        self.assertEqual(task.readings[0], MC.SO3)
        self.assertEqual(task.readings[-2:], [MC.AW3, MC.AW3])
        self.assertEqual(task.clicks, [('CM_CM_SWITCH', 2)])

    def test_six_star_card_is_refresh_only_even_when_explicitly_configured(self):
        self.assertIsNone(Selector._mission_from_setting('御魂三'))
        self.assertIsNone(Selector._mission_from_setting(MC.SO3))
        task = OcrSelector([('远远不够', '御魂三'), ('远远不够', '养成')],
                           rule='御魂三 > 远远不够')
        self.assertTrue(task.select_mission('御魂三'))
        self.assertEqual(task.readings[-2:], [MC.FEED, MC.FEED])
        self.assertEqual(task.clicks, [('CM_CM_SWITCH', 2)])

    def test_unchanging_six_star_card_remains_bounded_before_device_guard(self):
        task = OcrSelector([('远远不够', '御魂三')], progress=False)
        self.assertFalse(task.select_mission('养成'))
        self.assertEqual(len(task.clicks), 3)
        self.assertLess(Clock.now, 25)
        task.device.click_record_clear.assert_not_called()

    def test_six_star_card_respects_disabled_refresh_and_existing_click_history(self):
        for kwargs, expected_clicks in ((dict(available=False), 0),
                                        (dict(clickable=False), 0),
                                        (dict(history=['CM_CM_SWITCH'] * 8), 0),
                                        (dict(history=['CM_CM_SWITCH'] * 7), 1)):
            with self.subTest(kwargs=kwargs):
                Clock.now = 0
                task = OcrSelector([('远远不够', '御魂三')], **kwargs)
                self.assertFalse(task.select_mission('养成'))
                self.assertEqual(len(task.clicks), expected_clicks)
                self.assertLessEqual(Clock.now, 25.2)
                task.device.click_record_clear.assert_not_called()

    def test_real_dispatch_unrecognized_text_is_never_refreshed(self):
        for card in (('远远不够', '乱码'), ('远远不够', ''),
                     ('乱码', '御魂三'), ('其它任务', '御魂三')):
            with self.subTest(card=card):
                Clock.now = 0
                task = OcrSelector([card])
                self.assertFalse(task.select_mission('养成'))
                self.assertEqual(task.clicks, [])
                self.assertEqual(task.readings, [MC.UNKNOWN] * 3)


class OcrDispatchTests(unittest.TestCase):
    def detect(self, title, kind):
        runtime = S(screenshot=Mock(), device=S(image=object()))
        first = S(ocr=lambda _: title)
        second = S(ocr=lambda _: kind)
        return ACTUAL['detect_one'](runtime, first, second)

    def test_only_explicit_feed_subtype_is_feed(self):
        self.assertIs(self.detect('远远不够·', '养成'), MC.FEED)
        self.assertIs(self.detect('远远不够·', '御魂三'), MC.SO3)
        self.assertIs(self.detect('远远不够·', ''), MC.UNKNOWN)
        self.assertIs(self.detect('远远不够·', '乱码'), MC.UNKNOWN)
        self.assertIs(self.detect('其它任务', '养成'), MC.UNKNOWN)

    def test_six_star_refresh_class_requires_both_title_and_kind(self):
        self.assertIs(self.detect(' 远远不够· ', ' 御魂三 '), MC.SO3)
        self.assertIs(self.detect('', '御魂三'), MC.UNKNOWN)
        self.assertIs(self.detect('其它任务', '御魂三'), MC.UNKNOWN)
        self.assertIs(self.detect('远远不够', '御魂三乱码'), MC.UNKNOWN)

    def test_supported_material_and_soul_subtypes_remain_distinct(self):
        for text, expected in (('觉醒一', MC.AW1), ('觉醒二', MC.AW2), ('觉醒三', MC.AW3),
                               ('御灵一', MC.GR1), ('御灵二', MC.GR2), ('御灵三', MC.GR3),
                               ('御魂一', MC.SO1), ('御魂二', MC.SO2)):
            self.assertIs(self.detect('远远不够·', text), expected)

    def test_other_cards_remain_supported(self):
        self.assertIs(self.detect('契灵探查', ''), MC.BL)
        self.assertIs(self.detect('结伴同行', ''), MC.FRIEND)


if __name__ == '__main__':
    unittest.main()
