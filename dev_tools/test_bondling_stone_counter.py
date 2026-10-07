"""Offline regressions for unreadable summoning-stone counters.

The production methods are compiled from AST with a fake clock, OCR and UI.
No emulator, OAS process, game task or notification service is initialized.
"""
from copy import copy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import (
    SCRIPT_FILE, FakeClock, FakeLogger, FakeTimer, extract_methods,
)


ZERO = (0, 30, 30)
UNKNOWN = (0, 0, 0)
SEVEN = (7, 23, 30)
BASE_TIME = datetime(2026, 10, 7, 18, 0, 0)
OCR_FILE = Path(__file__).resolve().parents[1] / 'module/ocr/sub_ocr.py'


class TaskEnd(Exception):
    pass


def frame(primary=ZERO, verified=(ZERO, ZERO), modal=True):
    return {'primary': primary, 'verified': verified, 'modal': modal}


class StoneHarness:
    def __init__(self, frames, confirmation_closes=True):
        self.frames = frames
        self.frame = dict(frames[0])
        self.clock, self.logger = FakeClock(), FakeLogger()
        self.device = SimpleNamespace(image=self.frame)
        self.screenshots = self.rechecks = self.evidence = 0
        self.clicks, self.click_attempts, self.closed, self.delays = [], [], [], []
        self.confirmation_closes = confirmation_closes
        self.summoned = False
        self.current_count = 70
        self.config = SimpleNamespace(config_name='11')
        self.O_B_STONE_NUMBER = SimpleNamespace(name='B_STONE_NUMBER', ocr=self.ocr)
        for name in ('I_STONE_SURE', 'I_STONE_CLOSE', 'I_BUY_PLUS', 'I_GI_SURE'):
            setattr(self, name, name)

        class SimulatedDatetime:
            @staticmethod
            def now():
                return BASE_TIME + timedelta(seconds=self.clock.now - 100)

        methods = extract_methods(
            SCRIPT_FILE, 'ScriptTask',
            ('_valid_plate_counter', 'check_stone_available', '_defer_stone_check', 'run_stone'),
            {'Timer': lambda limit, count=0: FakeTimer(self.clock, limit, count),
             'sleep': self.sleep, 'logger': self.logger, 'TaskEnd': TaskEnd,
             'datetime': SimulatedDatetime, 'timedelta': timedelta,
             'random': SimpleNamespace(uniform=lambda low, high: (low + high) / 2)},
        )
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def screenshot(self):
        self.clock.advance()
        self.frame = dict(self.frames[min(self.screenshots, len(self.frames) - 1)])
        self.screenshots += 1
        if self.summoned:
            self.frame['modal'] = False
        self.device.image = self.frame
        if self.screenshots > 100:
            raise AssertionError('Resource verification must have a bounded timeout')

    def sleep(self, seconds):
        self.clock.now += seconds

    def ocr(self, image):
        return image.get('primary', UNKNOWN)

    def appear(self, marker, **kwargs):
        if marker in (self.I_STONE_SURE, self.I_STONE_CLOSE):
            return self.frame.get('modal', True)
        return False

    def _recheck_plate_counter(self, target):
        self.assert_stone_target(target)
        self.rechecks += 1
        return self.frame.get('verified', (UNKNOWN, UNKNOWN))

    def assert_stone_target(self, target):
        if target is not self.O_B_STONE_NUMBER:
            raise AssertionError('A summoning counter must not use a plate OCR target')

    def _save_plate_read_evidence(self, target, *args):
        self.assert_stone_target(target)
        self.evidence += 1

    def _save_stone_read_evidence(self, *args):
        self.evidence += 1

    def ui_click_until_disappear(self, marker, **kwargs):
        self.closed.append(marker)
        self.frame['modal'] = False

    def appear_then_click(self, marker, **kwargs):
        self.click_attempts.append(marker)
        if marker == self.I_STONE_SURE and self.appear(marker):
            self.clicks.append(marker)
            self.summoned = self.confirmation_closes
            return True
        return False

    def set_next_run(self, **kwargs):
        self.delays.append((self.clock.now, kwargs))


class StoneCounterTests(unittest.TestCase):
    def assert_retry_pending(self, task):
        self.assertEqual(len(task.delays), 1)
        when, delay = task.delays[0]
        self.assertEqual(delay['task'], 'BondlingFairyland')
        self.assertIs(delay['finish'], False)
        self.assertIsNone(delay['success'])
        self.assertIs(delay['server'], False)
        expected = BASE_TIME + timedelta(seconds=when - 100, minutes=3)
        self.assertEqual(delay['target'], expected)
        self.assertEqual(task.current_count, 70)
        self.assertEqual(task.closed, [task.I_STONE_CLOSE])

    def test_valid_seven_stones_is_fast_and_does_not_recheck(self):
        task = StoneHarness([frame(SEVEN)])
        self.assertIs(task.check_stone_available(), True)
        self.assertEqual((task.screenshots, task.rechecks, task.evidence), (1, 0, 0))
        self.assertEqual(task.click_attempts, [])

    def test_partial_slash_thirty_is_unknown_in_the_actual_parser(self):
        parser = extract_methods(OCR_FILE, 'DigitCounter', ('ocr_str_digit_counter',),
                                 {'logger': FakeLogger(), 're': __import__('re')})
        counter = type('Counter', (), {'name': 'B_STONE_NUMBER'})
        parse = parser['ocr_str_digit_counter'].__get__(None, counter)
        self.assertEqual(parse('/30'), UNKNOWN)
        task = StoneHarness([frame(parse('/30'), (UNKNOWN, UNKNOWN))])
        self.assertIsNone(task.check_stone_available())
        self.assertEqual(task.click_attempts, [])

    def test_unknown_does_not_confirm_zero_and_has_bounded_eight_second_wait(self):
        task = StoneHarness([frame(UNKNOWN, (UNKNOWN, UNKNOWN))])
        self.assertIsNone(task.check_stone_available())
        self.assertGreaterEqual(task.clock.now - 100, 8)
        self.assertLess(task.clock.now - 100, 11)
        self.assertEqual(task.click_attempts, [])
        self.assertEqual(task.evidence, 1)

    def test_stone_recheck_uses_wide_padding_and_double_scale_without_changing_asset(self):
        calls = []

        class FakeOcr:
            name = 'B_STONE_NUMBER'
            roi = [1135, 16, 78, 32]
            score = min_score = 0.8

            def ocr(self, image):
                calls.append(('ocr', self.name, list(self.roi), self.min_score))
                return SEVEN

            def crop(self, image, roi):
                calls.append(('crop', list(roi)))
                return SimpleNamespace(shape=(roi[3], roi[2], 3))

        def resize(cropped, dimensions, fx, fy, interpolation):
            calls.append(('resize', dimensions, fx, fy, interpolation))
            return SimpleNamespace(shape=(int(cropped.shape[0] * fy),
                                          int(cropped.shape[1] * fx), 3))

        target = FakeOcr()
        methods = extract_methods(
            SCRIPT_FILE, 'ScriptTask', ('_recheck_plate_counter',),
            {'copy': copy, 'cv2': SimpleNamespace(resize=resize, INTER_CUBIC='cubic')},
        )
        task = SimpleNamespace(device=SimpleNamespace(image='frame'))
        self.assertEqual(methods['_recheck_plate_counter'](task, target), (SEVEN, SEVEN))
        self.assertEqual(calls, [
            ('ocr', 'B_STONE_NUMBER_VERIFY', [1122, 10, 102, 44], 0.85),
            ('crop', [1122, 10, 102, 44]),
            ('resize', None, 2, 2, 'cubic'),
            ('ocr', 'B_STONE_NUMBER_VERIFY_2X', [0, 0, 204, 88], 0.85),
        ])
        self.assertEqual(target.roi, [1135, 16, 78, 32])
        self.assertEqual(target.min_score, 0.8)

    def test_single_zero_then_seven_stones_does_not_finish(self):
        task = StoneHarness([frame(), frame(SEVEN)])
        self.assertIs(task.check_stone_available(), True)
        self.assertEqual(task.screenshots, 2)

    def test_wide_or_scaled_positive_overrules_primary_zero_or_unknown(self):
        for primary in (ZERO, UNKNOWN):
            for verified in ((SEVEN, ZERO), (ZERO, SEVEN)):
                with self.subTest(primary=primary, verified=verified):
                    task = StoneHarness([frame(primary, verified)])
                    self.assertIs(task.check_stone_available(), True)
                    self.assertEqual((task.screenshots, task.rechecks), (1, 1))

    def test_empty_requires_three_fresh_frames_and_three_agreeing_readers(self):
        task = StoneHarness([frame()])
        self.assertIs(task.check_stone_available(), False)
        self.assertEqual((task.screenshots, task.rechecks), (3, 3))
        self.assertGreaterEqual(task.clock.now - 100, 2)
        self.assertEqual(task.click_attempts, [])

    def test_unknown_reader_prevents_zero_confirmation(self):
        task = StoneHarness([frame(ZERO, (ZERO, UNKNOWN))])
        self.assertIsNone(task.check_stone_available())

    def test_invalid_counter_shapes_and_values_are_never_available_or_empty(self):
        invalid = (UNKNOWN, None, (0, 0), (31, -1, 30), (-1, 31, 30),
                   (0, 20, 30), (0, 0, -1), ('7', 23, 30), (True, 29, 30))
        for value in invalid:
            with self.subTest(value=value):
                task = StoneHarness([frame(value, (value, value))])
                self.assertIsNone(task.check_stone_available())
                self.assertEqual(task.click_attempts, [])

    def test_denominator_disagreement_is_unknown(self):
        other = (0, 29, 29)
        task = StoneHarness([frame(ZERO, (ZERO, other))])
        self.assertIsNone(task.check_stone_available())

    def test_consistent_denominator_changes_reset_the_three_frame_streak(self):
        other = (0, 29, 29)
        task = StoneHarness([frame(), frame(other, (other, other)), frame(), frame(), frame()])
        self.assertIs(task.check_stone_available(), False)
        self.assertEqual(task.screenshots, 5)

    def test_missing_summon_popup_never_confirms_zero(self):
        task = StoneHarness([frame(modal=False)])
        self.assertIsNone(task.check_stone_available())
        self.assertEqual(task.rechecks, 0)
        self.assertEqual(task.click_attempts, [])

    def test_zero_streak_interrupted_by_missing_popup_is_not_reused(self):
        task = StoneHarness([frame(), frame(), frame(modal=False)])
        self.assertIsNone(task.check_stone_available())
        self.assertEqual(task.rechecks, 2)

    def test_disabling_stones_closes_popup_without_any_resource_read_or_consumption(self):
        task = StoneHarness([frame(SEVEN)])
        self.assertIs(task.run_stone(False), False)
        self.assertEqual((task.screenshots, task.rechecks), (0, 0))
        self.assertEqual(task.closed, [task.I_STONE_CLOSE])
        self.assertEqual(task.click_attempts, [])
        self.assertEqual(task.delays, [])

    def test_run_stone_without_popup_preserves_original_non_consuming_false(self):
        task = StoneHarness([frame(SEVEN, modal=False)])
        self.assertIs(task.run_stone(True), False)
        self.assertEqual((task.screenshots, task.rechecks), (0, 0))
        self.assertEqual(task.click_attempts, [])
        self.assertEqual(task.delays, [])

    def test_confirmed_zero_closes_without_summoning_and_returns_false(self):
        task = StoneHarness([frame()])
        self.assertIs(task.run_stone(True), False)
        self.assertEqual(task.screenshots, 3)
        self.assertEqual(task.closed, [task.I_STONE_CLOSE])
        self.assertEqual(task.click_attempts, [])
        self.assertEqual(task.delays, [])
        warnings = [args for level, args in task.logger.messages if level == 'warning']
        self.assertTrue(any('鸣契石' in str(args) for args in warnings))

    def test_unknown_stones_exit_only_by_three_minute_pending_retry(self):
        task = StoneHarness([frame(UNKNOWN, (UNKNOWN, UNKNOWN))])
        with self.assertRaises(TaskEnd):
            task.run_stone(True)
        self.assert_retry_pending(task)
        self.assertEqual(task.click_attempts, [])
        warnings = [str(args) for level, args in task.logger.messages if level == 'warning']
        self.assertFalse(any('已经没有鸣契石' in message for message in warnings))

    def test_unknown_stone_retry_propagates_through_switch_ball_without_success_closeout(self):
        task = StoneHarness([frame(UNKNOWN, (UNKNOWN, UNKNOWN))])
        task.config.bondling_fairyland = SimpleNamespace(
            bondling_config=SimpleNamespace(
                bondling_stone_class='镇墓兽', bondling_mode='capture',
                bondling_stone_enable=True, bondling_search_enable=False, user_status='alone'),
            battle_config=SimpleNamespace(),
        )
        task.in_search_ui = lambda **kwargs: True
        task.ball_click = lambda index: False
        page_calls = []
        task.goto_page = page_calls.append
        method = extract_methods(
            SCRIPT_FILE, 'ScriptTask', ('switch_ball',),
            {'logger': task.logger, 'BondlingMode': SimpleNamespace(MODE1='search'),
             'BondlingClass': SimpleNamespace(get_index=lambda value: 1),
             'UserStatus': SimpleNamespace(ALONE='alone'),
             'BondlingNumberMax': type('BondlingNumberMax', (Exception,), {}),
             'TaskEnd': TaskEnd, 'page_main': 'main',
             'page_bondling_fairyland': 'bondling'},
        )['switch_ball']
        with self.assertRaises(TaskEnd):
            method(task)
        self.assert_retry_pending(task)
        self.assertEqual(task.click_attempts, [])
        self.assertEqual(page_calls, [])
        self.assertFalse(any('BondlingFairyland task finished' in str(args)
                             for _, args in task.logger.messages))

    def test_seven_available_stones_preserve_normal_successful_summoning(self):
        task = StoneHarness([frame(SEVEN)])
        self.assertIs(task.run_stone(True), True)
        self.assertEqual(task.clicks, [task.I_STONE_SURE])
        self.assertEqual(task.closed, [])
        self.assertEqual(task.delays, [])

    def test_unconfirmed_summoning_does_not_return_false_or_mark_task_complete(self):
        task = StoneHarness([frame(SEVEN)], confirmation_closes=False)
        with self.assertRaises(TaskEnd):
            task.run_stone(True)
        self.assert_retry_pending(task)
        self.assertGreater(len(task.clicks), 0)
        self.assertLessEqual(len(task.clicks), 6)
        self.assertLess(task.clock.now - 100, 35)


if __name__ == '__main__':
    unittest.main()
