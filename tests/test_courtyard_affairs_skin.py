"""Daily-only skin routing, without a device, OCR engine, or game input."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import RequestHumanTakeover
from tasks.CourtyardAffairs.skin_assets import (
    CourtyardCompletionRule, CourtyardSkinRule, SkinAwareCourtyardAffairsMixin)


class ImageRule:
    def __init__(self, name, roi=(1, 2, 3, 4)):
        self.name = name
        self.roi_front = list(roi)
        self.roi_back = (0, 0, 1280, 720)
        self.threshold = .85
        self.method = 'Template matching'
        self.file = f'{name}.png'

    def match(self, image, threshold=None):
        return self.name in image


class LocatedImageRule(ImageRule):
    def match(self, image, threshold=None):
        if super().match(image, threshold):
            self.roi_front[:2] = [101, 202]
            return True
        return False


def skin_rule(action=True, daily=True):
    return CourtyardSkinRule(
        ImageRule('default-page'), ImageRule('blue-page'),
        ImageRule('default-complete', (1100, 590, 70, 80)) if action else None,
        ImageRule('blue-complete', (1120, 599, 77, 75)) if action else None,
        ImageRule('blue-daily') if daily else None)


class SkinFrames(SkinAwareCourtyardAffairsMixin):
    def __init__(self, frames, entry):
        self.frames = list(frames)
        self.current = set(entry)
        self.device = SimpleNamespace(image=self.current)
        self.clicks = []
        names = ('I_NOTE', 'I_PAGE', 'I_NO_TASKS', 'I_HARVEST_SOUL_2',
                 'I_HARVEST_SOUL_3', 'I_UI_AWARD', 'I_CONFIRM', 'I_DAILY',
                 'I_SUCCESS_CLAIMED', 'I_SKIP', 'I_LOGIN_RED_CLOSE', 'I_COMPLETE_TASKS',
                 'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_DAILY_INACTIVE',
                 'I_BLUE_COMPLETE_TASKS', 'I_BLUE_SUCCESS_CLAIMED')
        for name in names:
            setattr(self, name, ImageRule(name))
        self.ui_click_multi_scale = Mock(side_effect=lambda click, stop, **kwargs: stop.match(self.current))

    def screenshot(self):
        self.current = set(self.frames.pop(0)) if self.frames else set()
        self.device.image = self.current

    def appear(self, rule):
        return rule.match(self.current)

    def appear_then_click(self, rule, interval=None, action=None):
        if self.appear(rule):
            self.clicks.append(((action if action is not None else rule).name, interval))
            return True
        return False

    def ui_reward_appear_click(self):
        return 'reward' in self.current


class CourtyardSkinTests(unittest.TestCase):
    def test_blue_page_is_an_entry_stop_without_default_title(self):
        rule = skin_rule(action=False, daily=False)
        self.assertTrue(rule.match({'blue-page'}))

    def test_default_page_is_still_an_entry_stop(self):
        rule = skin_rule(action=False, daily=False)
        self.assertTrue(rule.match({'default-page'}))

    def test_page_title_is_required_before_clicking(self):
        self.assertFalse(skin_rule().match({'blue-daily', 'blue-complete'}))
        self.assertFalse(skin_rule().match({'default-complete'}))

    def test_blue_special_tab_cannot_use_one_click(self):
        self.assertFalse(skin_rule().match({'blue-page', 'blue-special', 'blue-complete'}))

    def test_blue_daily_without_button_is_not_a_click_target(self):
        self.assertFalse(skin_rule().match({'blue-page', 'blue-daily'}))

    def test_blue_daily_uses_blue_button_coordinates(self):
        rule = skin_rule()
        self.assertTrue(rule.match({'blue-page', 'blue-daily', 'blue-complete'}))
        self.assertEqual(rule.roi_front, [1120, 599, 77, 75])

    def test_default_button_coordinates_are_preserved(self):
        rule = skin_rule()
        self.assertTrue(rule.match({'default-page', 'default-complete'}))
        self.assertEqual(rule.roi_front, [1100, 590, 70, 80])

    def test_default_match_cannot_bypass_blue_special_guard(self):
        self.assertFalse(skin_rule().match({'blue-page', 'default-page',
                                          'blue-complete', 'default-complete'}))

    def test_rules_copy_coordinates_instead_of_mutating_default_assets(self):
        rule = skin_rule()
        before = list(rule.default_action.roi_front)
        rule.match({'blue-page', 'blue-daily', 'blue-complete'})
        self.assertEqual(rule.default_action.roi_front, before)

    def test_matching_does_not_mutate_original_front_lists(self):
        default_page = LocatedImageRule('default-page')
        blue_page = LocatedImageRule('blue-page')
        default_action = LocatedImageRule('default-complete')
        blue_action = LocatedImageRule('blue-complete')
        daily = LocatedImageRule('blue-daily')
        original = [default_page, blue_page, default_action, blue_action, daily]
        for rule in (CourtyardSkinRule(*original),
                     CourtyardCompletionRule(LocatedImageRule('empty'), default_page, blue_page, daily)):
            rule.match({'blue-page', 'blue-complete', 'blue-daily', 'empty'})
            self.assertTrue(all(asset.roi_front == [1, 2, 3, 4] for asset in original))

    def empty_rule(self):
        return CourtyardCompletionRule(ImageRule('empty'), ImageRule('default-page'),
                                       ImageRule('blue-page'), ImageRule('blue-daily'))

    def test_special_empty_tasks_cannot_complete_daily(self):
        self.assertFalse(self.empty_rule().match({'blue-page', 'empty'}))

    def test_empty_popup_without_prior_daily_confirmation_is_rejected(self):
        self.assertFalse(self.empty_rule().match({'empty'}))

    def test_daily_confirmation_survives_reward_overlay(self):
        rule = self.empty_rule()
        self.assertFalse(rule.match({'blue-page', 'blue-daily'}))
        self.assertFalse(rule.match({'reward'}))
        self.assertTrue(rule.match({'empty'}))

    def test_changing_to_special_clears_old_daily_confirmation(self):
        rule = self.empty_rule()
        rule.match({'blue-page', 'blue-daily'})
        self.assertFalse(rule.match({'blue-page', 'empty'}))
        self.assertFalse(rule.match({'empty'}))

    def test_default_completion_marker_is_retained(self):
        rule = self.empty_rule()
        self.assertFalse(rule.match({'default-page'}))
        self.assertTrue(rule.match({'empty'}))

    def timer(self, reached=False):
        timer = Mock()
        timer.start.return_value = timer
        timer.reached.return_value = reached
        return timer

    def test_blue_workflow_confirms_empty_screen_after_claim(self):
        task = SkinFrames([
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_COMPLETE_TASKS'},
            {'reward'}, {'I_NO_TASKS'}], {'I_BLUE_PAGE'})
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer()):
            self.assertTrue(task.harvest_courtyard_affairs())
        self.assertEqual(task.clicks, [('I_COMPLETE_TASKS', 2.3)])

    def test_blue_unknown_page_timeout_has_no_click_or_success(self):
        task = SkinFrames([{'I_BLUE_COMPLETE_TASKS'}], {'I_BLUE_PAGE'})
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer(True)):
            self.assertFalse(task.harvest_courtyard_affairs())
        self.assertFalse(task.clicks)

    def test_daily_tab_selection_does_not_claim_special_affairs(self):
        task = SkinFrames([
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_INACTIVE', 'I_BLUE_COMPLETE_TASKS'},
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_COMPLETE_TASKS'},
            {'I_NO_TASKS'}], {'I_BLUE_PAGE'})
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer()):
            self.assertTrue(task.harvest_courtyard_affairs())
        self.assertEqual(task.clicks, [('I_DAILY', 1), ('I_COMPLETE_TASKS', 2.3)])

    def test_no_entry_does_not_run_claim_loop(self):
        task = SkinFrames([], {'I_BLUE_COMPLETE_TASKS'})
        self.assertFalse(task.harvest_courtyard_affairs())
        self.assertFalse(task.clicks)

    def test_human_takeover_guard_is_not_swallowed(self):
        task = SkinFrames([], {'I_BLUE_PAGE'})
        task.screenshot = Mock(side_effect=RequestHumanTakeover('owner returned'))
        with patch('tasks.Component.CourtyardAffairs.courtyard_affairs.Timer', return_value=self.timer()):
            with self.assertRaises(RequestHumanTakeover):
                task.harvest_courtyard_affairs()


if __name__ == '__main__':
    unittest.main()
