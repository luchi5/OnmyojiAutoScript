"""Offline difficulty regressions; no OCR model, backend or emulator startup."""
from copy import copy
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeLogger, extract_methods


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/AreaBoss/script_task.py'


class AreaBossFloor(str, Enum):
    DEFAULT = '不更改'
    ONE = '一星'
    TEN = '十星'
    NORMAL_LV1 = '普通-1级'
    NORMAL_LV60 = '普通-60级'


class TaskEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class GameTooManyClickError(Exception):
    pass


class AccountLoggedInElsewhere(Exception):
    pass


class Reader:
    roi = (1180, 61, 82, 32)
    area = roi
    name = 'AB_REPUTATION'

    def __init__(self, harness):
        self.harness = harness

    def after_process(self, text):
        # A realistic dangerous shared Digit repair, which the local reader
        # must bypass without altering the original reputation rule.
        return 1 if text in ('00', '000') else text

    def detect_and_ocr(self, image):
        assert self.roi == (300, 160, 110, 100)
        assert self.area == self.roi
        texts = self.harness.frame.get('texts', ['1'])
        if texts is None:
            return []
        return [SimpleNamespace(ocr_text=self.after_process(text)) for text in texts]


class NoDiagnosticFiles:
    """Keep actual unknown-mode diagnostics from touching the filesystem."""
    def __init__(self, *parts):
        pass

    def __truediv__(self, name):
        return self

    def mkdir(self, **kwargs):
        pass


class Harness:
    def __init__(self, frames, floor=AreaBossFloor.NORMAL_LV1):
        self.frames, self.frame = frames, frames[0]
        self.clock = FakeClock(step=0.4)
        self.screenshots = 0
        self.clicks, self.swipes, self.delays, self.navigation = [], [], [], []
        self.battles = 0
        self.logger = FakeLogger()
        self.device = SimpleNamespace(image=None)
        self.O_AB_REPUTATION = Reader(self)
        self.I_AB_CLOSE_RED = 'close'
        self.I_AB_DIFFICULTY_NORMAL = 'normal'
        self.I_AB_DIFFICULTY_JI = 'ultra'
        self.I_AB_FILTER_OPENED = 'filter'
        self.I_AB_LEVEL_HANDLE = SimpleNamespace(front_center=lambda: (420, 279))
        self.S_AB_LEVEL_RIGHT = SimpleNamespace(
            roi_front=(0, 0, 10, 10), roi_back=(570, 270, 10, 10), name='ab_level_right')
        self.config = SimpleNamespace(area_boss=SimpleNamespace(
            boss=SimpleNamespace(reward_floor=floor)))
        self.exception = self.navigation_exception = None
        self.ranked = False
        namespace = {
            'time': SimpleNamespace(monotonic=lambda: self.clock.now, sleep=self.sleep),
            'copy': copy, 're': re, 'datetime': datetime, 'timedelta': timedelta,
            'logger': self.logger, 'AreaBossFloor': AreaBossFloor, 'RuleImage': object,
            'TaskEnd': TaskEnd, 'GameStuckError': GameStuckError,
            'GamePageUnknownError': GamePageUnknownError,
            'GameTooManyClickError': GameTooManyClickError, 'page_main': 'courtyard',
            'Path': NoDiagnosticFiles,
            'cv2': SimpleNamespace(COLOR_RGB2BGR=0, cvtColor=lambda image, code: image,
                                   imencode=lambda fmt, image: (False, None)),
        }
        methods = ('_normal_level', '_normal_mode_visible', '_switch_to_normal_level', 'switch_to_level_1',
                   'switch_to_level_60', 'switch_difficulty', 'setup_normal', 'setup_ultra',
                   '_defer_difficulty', 'boss_fight')
        for name, method in extract_methods(SOURCE, 'ScriptTask', methods, namespace).items():
            setattr(self, name, method.__get__(self))

    def sleep(self, seconds):
        self.clock.now += seconds

    def screenshot(self):
        if self.exception:
            raise self.exception
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.clock.advance()
        if self.screenshots > 100:
            raise AssertionError('Difficulty loop did not finish')

    def appear(self, rule):
        if rule is self.I_AB_LEVEL_HANDLE:
            return self.frame.get('handle', self.frame.get('mode', 'normal') == 'normal')
        if rule in ('normal', 'ultra'):
            if self.frame.get('buttons_missing', False):
                return False
            mode = self.frame.get('template_mode', self.frame.get('mode', 'normal'))
            return mode == rule or mode == 'ambiguous'
        if rule == 'close':
            return self.frame.get('detail', True)
        return rule == 'filter'

    def click(self, rule, interval=None):
        if self.clicks and self.clock.now - self.clicks[-1][2] < interval:
            return False
        self.clicks.append((rule, interval, self.clock.now))
        return True

    def swipe(self, rule, interval=None):
        self.swipes.append((copy(rule), interval))

    def open_boss_detail(self, *args):
        return True

    def is_group_ranked(self):
        return self.ranked

    def start_fight(self):
        self.battles += 1
        return True

    def wait_until_appear(self, *args):
        pass

    def ui_click_until_disappear(self, *args, **kwargs):
        pass

    def set_next_run(self, **kwargs):
        self.delays.append(kwargs)

    def goto_page(self, page):
        self.navigation.append(page)
        if self.navigation_exception:
            raise self.navigation_exception


class NormalLevelTests(unittest.TestCase):
    def test_existing_one_needs_no_drag(self):
        h = Harness([{'texts': ['1']}])
        self.assertTrue(h.switch_to_level_1())
        self.assertEqual(h.swipes, [])
        self.assertEqual(h.screenshots, 2)

    def test_old_high_level_drags_left_then_confirms(self):
        h = Harness([{'texts': ['60']}, {'texts': ['1']}, {'texts': ['1']}])
        self.assertTrue(h.switch_to_level_1())
        self.assertEqual(len(h.swipes), 1)
        swipe, interval = h.swipes[0]
        self.assertEqual(interval, 3)
        self.assertEqual(swipe.roi_front, (418, 277, 4, 4))
        self.assertEqual(swipe.roi_back, (170, 277, 4, 4))
        self.assertEqual(h.S_AB_LEVEL_RIGHT.roi_front, (0, 0, 10, 10))

    def test_unchanged_high_level_has_bounded_retries(self):
        h = Harness([{'texts': ['60']}])
        self.assertFalse(h.switch_to_level_1())
        self.assertEqual(len(h.swipes), 3)
        self.assertLessEqual(h.clock.now, 116)

    def test_zero_repair_does_not_count_as_one(self):
        for raw in ('00', '000', '0', '01', '61', 'I', '1级', ''):
            with self.subTest(raw=raw):
                h = Harness([{'texts': [raw]}])
                self.assertIsNone(h._normal_level())
                self.assertEqual(h.O_AB_REPUTATION.after_process('00'), 1)
                self.assertEqual(h.O_AB_REPUTATION.roi, (1180, 61, 82, 32))
        h = Harness([{'texts': ['00']}])
        self.assertFalse(h.switch_to_level_1())

    def test_partial_and_multiple_ocr_results_are_unconfirmed(self):
        for texts in ([], ['1', '60']):
            h = Harness([{'texts': texts}])
            self.assertIsNone(h._normal_level())

    def test_each_complete_level_remains_supported(self):
        for level in range(1, 61):
            h = Harness([{'texts': [str(level)]}])
            self.assertEqual(h._normal_level(), level)

    def test_one_frame_one_does_not_finish(self):
        h = Harness([{'texts': ['1']}, {'texts': ['60']}])
        self.assertFalse(h.switch_to_level_1())

    def test_level_sixty_uses_right_endpoint(self):
        h = Harness([{'texts': ['1']}, {'texts': ['60']}, {'texts': ['60']}])
        self.assertTrue(h.switch_to_level_60())
        self.assertEqual(h.swipes[0][0].roi_back, (570, 277, 4, 4))

    def test_missing_mode_or_detail_prevents_drag(self):
        for frame in ({'mode': 'unknown'}, {'mode': 'ultra'}, {'detail': False}):
            h = Harness([frame])
            self.assertFalse(h.switch_to_level_1())
            self.assertEqual(h.swipes, [])

    def test_current_normal_mode_needs_no_toggle(self):
        h = Harness([{}])
        self.assertTrue(h.setup_normal())
        self.assertEqual(h.clicks, [])

    def test_normal_one_without_mode_buttons_is_confirmed_without_click_or_drag(self):
        h = Harness([{'buttons_missing': True, 'texts': ['1']}])
        self.assertTrue(h.setup_normal())
        self.assertTrue(h.switch_to_level_1())
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])
        self.assertEqual(h.screenshots, 4)

    def test_normal_sixty_without_mode_buttons_is_confirmed(self):
        h = Harness([{'buttons_missing': True, 'texts': ['60']}],
                    floor=AreaBossFloor.NORMAL_LV60)
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])

    def test_missing_mode_templates_can_use_positive_slider_and_exact_level(self):
        h = Harness([{'mode': 'normal', 'template_mode': 'unknown', 'texts': ['1']}])
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])

    def test_normal_sixty_without_buttons_drags_left_before_battle(self):
        h = Harness([{'buttons_missing': True, 'texts': ['60']}] * 3
                    + [{'buttons_missing': True, 'texts': ['1']}] * 2)
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.clicks, [])
        self.assertEqual(len(h.swipes), 1)
        self.assertEqual(h.swipes[0][0].roi_back, (170, 277, 4, 4))
        self.assertEqual(h.swipes[0][1], 3)

    def test_normal_caller_reuses_mode_helper_when_raising_level_without_buttons(self):
        h = Harness([{'buttons_missing': True, 'texts': ['1']},
                     {'buttons_missing': True, 'texts': ['60']},
                     {'buttons_missing': True, 'texts': ['60']}])
        self.assertTrue(h.switch_to_level_60())
        self.assertEqual(len(h.swipes), 1)
        self.assertEqual(h.swipes[0][0].roi_back, (570, 277, 4, 4))
        self.assertEqual(h.clicks, [])

    def test_missing_switch_button_cannot_click_into_ultra(self):
        h = Harness([{'buttons_missing': True, 'texts': ['1']}])
        self.assertFalse(h.switch_difficulty(True))
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])
        self.assertLessEqual(h.clock.now, 113)

    def test_slider_with_zero_or_missing_level_cannot_confirm_normal_or_start_battle(self):
        for texts in (['00'], ['0'], None, []):
            with self.subTest(texts=texts):
                h = Harness([{'buttons_missing': True, 'handle': True, 'texts': texts}])
                self.assertFalse(h._normal_mode_visible())
                with self.assertRaises(TaskEnd):
                    h.boss_fight(object())
                self.assertEqual(h.battles, 0)
                self.assertEqual(h.clicks, [])
                self.assertEqual(h.swipes, [])
                self.assertEqual(len(h.delays), 1)

    def test_real_ultra_icon_overrides_misleading_slider_and_level(self):
        h = Harness([{'mode': 'ultra', 'template_mode': 'ultra',
                      'handle': True, 'texts': ['1']}])
        self.assertFalse(h._normal_mode_visible())
        self.assertFalse(h.switch_to_level_1())
        self.assertEqual(h.swipes, [])
        self.assertEqual(h.clicks, [])

    def test_missing_slider_and_mode_buttons_never_infer_normal_from_digits_alone(self):
        h = Harness([{'buttons_missing': True, 'handle': False, 'texts': ['1']}])
        self.assertFalse(h.setup_normal())
        self.assertFalse(h.switch_to_level_1())
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])

    def test_no_detail_page_cannot_fight_even_with_slider_and_exact_level(self):
        h = Harness([{'buttons_missing': True, 'handle': True,
                      'detail': False, 'texts': ['1']}])
        with self.assertRaises(TaskEnd):
            h.boss_fight(object())
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])

    def test_one_positive_missing_button_frame_is_not_stable_confirmation(self):
        h = Harness([{'buttons_missing': True, 'texts': ['1']},
                     {'buttons_missing': True, 'texts': ['00']}])
        self.assertFalse(h.setup_normal())
        self.assertEqual(h.clicks, [])

    def test_takeover_on_missing_button_page_still_propagates(self):
        h = Harness([{'buttons_missing': True, 'texts': ['1']}])
        h.exception = AccountLoggedInElsewhere('Owner online')
        with self.assertRaises(AccountLoggedInElsewhere):
            h.boss_fight(object())
        self.assertEqual(h.delays, [])
        self.assertEqual(h.battles, 0)

    def test_ultra_to_normal_toggles_once(self):
        h = Harness([{'mode': 'ultra'}, {'mode': 'normal'}, {'mode': 'normal'}])
        self.assertTrue(h.setup_normal())
        self.assertEqual([item[0] for item in h.clicks], ['ultra'])
        self.assertEqual(h.clicks[0][1], 3)

    def test_ambiguous_unknown_mode_does_not_click(self):
        for mode in ('unknown', 'ambiguous'):
            h = Harness([{'mode': mode}])
            self.assertFalse(h.setup_normal())
            self.assertEqual(h.clicks, [])

    def test_failed_toggle_has_bounded_clicks(self):
        h = Harness([{'mode': 'ultra'}])
        self.assertFalse(h.setup_normal())
        self.assertEqual(len(h.clicks), 3)

    def test_boss_fight_cannot_start_unconfirmed_level(self):
        h = Harness([{'texts': ['60']}])
        started = datetime.now()
        with self.assertRaises(TaskEnd):
            h.boss_fight(object())
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.delays[0]['success'], None)
        self.assertEqual(h.delays[0]['server'], False)
        self.assertEqual(h.delays[0]['task'], 'AreaBoss')
        self.assertGreaterEqual(h.delays[0]['target'], started + timedelta(minutes=30))
        self.assertEqual(h.navigation, ['courtyard'])

    def test_confirmed_normal_one_starts_battle(self):
        h = Harness([{'texts': ['1']}])
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.swipes, [])
        self.assertEqual(h.clicks, [])

    def test_takeover_is_not_swallowed_or_deferred(self):
        h = Harness([{}])
        h.exception = AccountLoggedInElsewhere('Owner online')
        with self.assertRaises(AccountLoggedInElsewhere):
            h.boss_fight(object())
        self.assertEqual(h.delays, [])
        self.assertEqual(h.battles, 0)

    def test_routine_guard_defers_without_fighting(self):
        h = Harness([{}])
        h.exception = GameTooManyClickError('Unresponsive mode')
        h.navigation_exception = GamePageUnknownError('Overlay')
        with self.assertRaises(TaskEnd):
            h.boss_fight(object())
        self.assertEqual(h.battles, 0)
        self.assertEqual(len(h.delays), 1)

    def test_no_change_and_ranked_keep_existing_behavior(self):
        h = Harness([{}], floor=AreaBossFloor.DEFAULT)
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.screenshots, 0)
        h = Harness([{}])
        h.ranked = True
        self.assertTrue(h.boss_fight(object()))
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.screenshots, 0)

    def test_ultra_failed_selection_cannot_start_battle(self):
        h = Harness([{'mode': 'ultra'}], floor=AreaBossFloor.ONE)
        h.switch_to_floor_1 = lambda: False
        with self.assertRaises(TaskEnd):
            h.boss_fight(object())
        self.assertEqual(h.battles, 0)


if __name__ == '__main__':
    unittest.main()
