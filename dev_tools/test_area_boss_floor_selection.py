"""Bounded star-selector regressions, without loading OCR, OAS or Device."""
from copy import deepcopy
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeLogger, extract_methods


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/AreaBoss/script_task.py'


class RequestHumanTakeover(Exception):
    pass


class AccountLoggedInElsewhere(RequestHumanTakeover):
    pass


class Rule:
    def __init__(self, name, roi_front, roi_back=None):
        self.name = name
        self.roi_front = list(roi_front)
        self.roi_back = roi_back or roi_front


class TitleReader:
    roi = (114, 36, 248, 69)

    def __init__(self, harness):
        self.harness = harness

    def detect_text(self, _image):
        if self.harness.mode == 'empty_ocr':
            return ''
        if self.harness.mode == 'wrong_ocr':
            return '1级'
        return {'one': '壹星', 'ten': '拾星', 'two': '贰星'}[self.harness.current]


class FloorHarness:
    def __init__(self, target='one', current='two', opened=False, viewport='first', mode='normal'):
        self.target, self.current, self.opened = target, current, opened
        self.viewport, self.mode = viewport, mode
        self.clock = FakeClock(step=0.3)
        self.logger = FakeLogger()
        self.clicks, self.swipes, self.probes = [], [], []
        self.screenshots, self.scrolls = 0, 0
        self.close_after = None
        self.device = SimpleNamespace(image=None)
        self.O_AB_BOSS_NAME = TitleReader(self)
        self.I_AB_JI_FLOOR_ONE = Rule('one', (390, 150, 60, 290))
        self.I_AB_JI_FLOOR_TEN = Rule('ten', (390, 370, 60, 40))
        self.I_AB_JI_FLOOR_LIST_CHECK = Rule('old_fifth_star', (390, 150, 60, 290))
        self.C_AB_JI_FLOOR_SELECTED = Rule('toggle', (380, 120, 70, 30))
        self.S_AB_FLOOR_DOWN = Rule('ab_floor_down', (390, 260, 10, 10), (450, 500, 10, 10))
        self.originals = deepcopy((self.I_AB_JI_FLOOR_ONE.__dict__,
                                   self.I_AB_JI_FLOOR_TEN.__dict__, self.S_AB_FLOOR_DOWN.__dict__))
        namespace = {
            'RuleImage': Rule, 'logger': self.logger,
            're': re,
            'time': SimpleNamespace(monotonic=lambda: self.clock.now,
                                    sleep=lambda seconds: setattr(self.clock, 'now', self.clock.now + seconds)),
        }
        methods = extract_methods(SOURCE, 'ScriptTask', (
            'switch_to_floor_1', 'switch_to_floor_10', '_switch_to_floor'), namespace)
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def run(self):
        return self.switch_to_floor_1() if self.target == 'one' else self.switch_to_floor_10()

    def screenshot(self):
        self.clock.advance()
        self.screenshots += 1
        if self.screenshots > 100:
            raise AssertionError('Unbounded selector loop')
        if self.mode == 'takeover':
            raise AccountLoggedInElsewhere('Owner took over')
        if self.close_after and self.clock.now >= self.close_after:
            self.current, self.opened = self.target, False

    def appear(self, rule):
        self.probes.append((rule.name, rule.roi_back))
        if rule.name == 'old_fifth_star':
            raise AssertionError('Must not depend on unlocked fifth-star styling')
        if rule.roi_back == (365, 108, 140, 45):
            if self.mode in ('template_miss', 'empty_ocr', 'wrong_ocr'):
                return False
            return rule.name == self.current
        if not self.opened:
            return False
        if self.viewport == 'first':
            visible = rule.name == 'one'
        elif self.viewport == 'last':
            visible = rule.name == 'ten'
        else:
            visible = False
        if visible:
            rule.roi_front = [395, 162 if rule.name == 'one' else 380, 47, 25]
        return visible

    def click(self, rule, interval=None):
        self.clicks.append((rule.name, interval, tuple(rule.roi_front)))
        if rule.name == 'toggle':
            if self.mode != 'unopenable':
                self.opened, self.viewport = not self.opened, 'first'
        elif self.mode == 'unresponsive':
            pass
        elif self.mode == 'wrong_title':
            self.opened = False
        elif self.mode == 'slow_close':
            if len([click for click in self.clicks if click[0] != 'toggle']) == 2:
                self.close_after = self.clock.now + 1.2
        else:
            self.current, self.opened = rule.name, False
        return True

    def swipe(self, rule, interval=None):
        self.swipes.append((rule.name, tuple(rule.roi_front), tuple(rule.roi_back), interval))
        self.scrolls += 1
        if self.mode == 'missing_target':
            self.viewport = 'middle'
        elif self.scrolls % 2:
            self.viewport = 'middle'
        else:
            self.viewport = 'first' if rule.name == 'ab_floor_down' else 'last'


class FloorSelectionTests(unittest.TestCase):
    def test_real_error_state_open_list_selects_one_without_toggle(self):
        harness = FloorHarness(opened=True)
        self.assertTrue(harness.run())
        self.assertEqual(harness.clicks, [('one', 1.5, (395, 162, 47, 25))])
        self.assertFalse(harness.swipes)
        self.assertFalse(harness.opened)

    def test_closed_wrong_star_opens_once_then_confirms_one(self):
        harness = FloorHarness()
        self.assertTrue(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['toggle', 'one'])
        self.assertEqual(harness.clicks[0][1], 3)

    def test_correct_closed_star_needs_no_actions(self):
        harness = FloorHarness(current='one')
        self.assertTrue(harness.run())
        self.assertFalse(harness.clicks)
        self.assertFalse(harness.swipes)
        self.assertEqual(harness.screenshots, 2)

    def test_correct_open_star_is_closed_before_success(self):
        harness = FloorHarness(current='one', opened=True)
        self.assertTrue(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['one'])
        self.assertFalse(harness.opened)

    def test_ten_scrolls_up_through_middle_without_reopening(self):
        harness = FloorHarness(target='ten', opened=True)
        self.assertTrue(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['ten'])
        self.assertEqual(len(harness.swipes), 2)
        for name, start, end, interval in harness.swipes:
            self.assertEqual(name, 'ab_floor_up')
            self.assertGreater(start[1], end[1])
            self.assertEqual(interval, 1)

    def test_one_scrolls_down_from_last_without_toggle(self):
        harness = FloorHarness(opened=True, viewport='last')
        self.assertTrue(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['one'])
        self.assertEqual(len(harness.swipes), 2)
        self.assertLess(harness.swipes[0][1][1], harness.swipes[0][2][1])

    def test_unopenable_is_bounded_to_two_clicks(self):
        harness = FloorHarness(mode='unopenable')
        self.assertFalse(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['toggle', 'toggle'])
        self.assertFalse(harness.swipes)

    def test_missing_target_is_bounded_to_six_swipes(self):
        harness = FloorHarness(target='ten', opened=True, mode='missing_target')
        self.assertFalse(harness.run())
        self.assertEqual(len(harness.swipes), 6)
        self.assertFalse(harness.clicks)

    def test_unresponsive_selection_is_bounded_to_two_clicks(self):
        harness = FloorHarness(opened=True, mode='unresponsive')
        self.assertFalse(harness.run())
        self.assertEqual([click[0] for click in harness.clicks], ['one', 'one'])
        self.assertTrue(harness.opened)

    def test_menu_closed_with_wrong_title_is_not_success(self):
        harness = FloorHarness(opened=True, mode='wrong_title')
        self.assertFalse(harness.run())
        self.assertEqual(harness.current, 'two')
        self.assertEqual([click[0] for click in harness.clicks], ['one'])

    def test_second_selection_has_time_to_close(self):
        harness = FloorHarness(opened=True, mode='slow_close')
        self.assertTrue(harness.run())
        self.assertEqual(len(harness.clicks), 2)
        self.assertFalse(harness.opened)

    def test_shared_asset_positions_and_swipe_are_unchanged(self):
        harness = FloorHarness(target='ten', opened=True)
        self.assertTrue(harness.run())
        current = (harness.I_AB_JI_FLOOR_ONE.__dict__, harness.I_AB_JI_FLOOR_TEN.__dict__,
                   harness.S_AB_FLOOR_DOWN.__dict__)
        self.assertEqual(current, harness.originals)
        self.assertEqual(harness.O_AB_BOSS_NAME.roi, (114, 36, 248, 69))

    def test_header_template_difference_uses_exact_star_ocr(self):
        harness = FloorHarness(opened=True, mode='template_miss')
        self.assertTrue(harness.run())
        self.assertEqual(harness.current, 'one')
        self.assertFalse(harness.opened)

    def test_empty_header_ocr_does_not_claim_success(self):
        harness = FloorHarness(opened=True, mode='empty_ocr')
        self.assertFalse(harness.run())

    def test_normal_level_one_text_is_not_extreme_one_star(self):
        harness = FloorHarness(opened=True, mode='wrong_ocr')
        self.assertFalse(harness.run())

    def test_account_takeover_propagates(self):
        with self.assertRaises(AccountLoggedInElsewhere):
            FloorHarness(mode='takeover').run()


if __name__ == '__main__':
    unittest.main()
