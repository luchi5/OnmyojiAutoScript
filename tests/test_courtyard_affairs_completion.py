"""Real daily-panel fixtures and completion/reward boundary tests; no ADB."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import RequestHumanTakeover
from tasks.CourtyardAffairs.completion_state import (
    BlueCompletionDetector, CompletionAwareCourtyardMixin, CourtyardEmptyTasksRule)
from tests.test_courtyard_affairs_skin import ImageRule, SkinFrames


class CompletionFrames(SkinFrames):
    configure_courtyard_skin = CompletionAwareCourtyardMixin.configure_courtyard_skin
    harvest_courtyard_affairs = CompletionAwareCourtyardMixin.harvest_courtyard_affairs

    def screenshot(self):
        if not self.frames:
            raise AssertionError('Frame script exhausted without termination')
        super().screenshot()


class CourtyardCompletionTests(unittest.TestCase):
    def image(self, name):
        path = Path(__file__).parent / 'fixtures' / 'courtyard_affairs' / f'{name}.png'
        crop = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[48:698, 690:1245] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return image

    def test_real_post_claim_state_is_positive(self):
        self.assertTrue(BlueCompletionDetector().ready(self.image('post')))

    def test_real_initial_bright_button_is_not_complete(self):
        self.assertFalse(BlueCompletionDetector().ready(self.image('before')))

    def test_brightness_invariant_template_alone_cannot_complete(self):
        detector = BlueCompletionDetector()
        self.assertTrue(detector.disabled.match(self.image('before')))
        self.assertFalse(detector.ready(self.image('before')))

    def test_bright_button_with_all_completed_stamps_is_not_done(self):
        post, before = self.image('post'), self.image('before')
        post[570:698, 1100:1242] = before[570:698, 1100:1242]
        self.assertFalse(BlueCompletionDetector().ready(post))

    def test_only_two_completed_rows_is_not_done(self):
        image = self.image('post')
        image[425:513, 1000:1145] = 245
        image[555:643, 1000:1145] = 245
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_stamps_are_independent_spatial_regions(self):
        image = self.image('post')
        image[295:383, 1000:1145] = 245
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_disabled_button_without_completed_stamps_is_not_done(self):
        image = self.image('post')
        image[165:643, 1000:1100] = 245
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_special_or_obscured_daily_tab_is_not_done(self):
        image = self.image('post')
        image[125:335, 1138:1226] = 0
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_missing_title_is_not_done(self):
        image = self.image('post')
        image[48:160, 690:1050] = 0
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_reward_shade_on_completed_page_is_not_done(self):
        image = (self.image('post').astype(np.float32) * .55).astype(np.uint8)
        self.assertFalse(BlueCompletionDetector().ready(image))

    def test_reward_shade_cannot_turn_bright_button_into_done(self):
        image = (self.image('before').astype(np.float32) * .55).astype(np.uint8)
        self.assertFalse(BlueCompletionDetector().ready(image))

    def context_rule(self):
        return CourtyardEmptyTasksRule(ImageRule('empty'), ImageRule('default-page'),
                                      ImageRule('blue-page'), ImageRule('daily'),
                                      ImageRule('inactive'), (ImageRule('other-page'),))

    def test_title_visible_but_reward_masks_tab_preserves_daily(self):
        rule = self.context_rule()
        rule.match({'blue-page', 'daily'})
        self.assertTrue(rule.match({'blue-page', 'reward', 'empty'}))

    def test_masked_tab_without_prior_confirmation_is_not_daily(self):
        self.assertFalse(self.context_rule().match({'blue-page', 'reward', 'empty'}))

    def test_inactive_daily_clears_old_context(self):
        rule = self.context_rule()
        rule.match({'blue-page', 'daily'})
        self.assertFalse(rule.match({'blue-page', 'inactive', 'empty'}))
        self.assertFalse(rule.match({'empty'}))

    def test_another_page_clears_old_context(self):
        rule = self.context_rule()
        rule.match({'blue-page', 'daily'})
        rule.match({'other-page'})
        self.assertFalse(rule.match({'empty'}))

    def test_default_empty_popup_is_retained(self):
        rule = self.context_rule()
        rule.match({'default-page'})
        self.assertTrue(rule.match({'empty'}))

    def timer(self):
        timer = Mock()
        timer.start.return_value = timer
        timer.reached.return_value = False
        return timer

    def run_frames(self, frames, max_clicks=3):
        task = CompletionFrames(frames, {'I_BLUE_PAGE'})
        detector = Mock()
        detector.ready.side_effect = lambda image: 'done' in image
        with patch('tasks.CourtyardAffairs.selected_skin.Timer', return_value=self.timer()), \
                patch('tasks.CourtyardAffairs.selected_skin.BlueCompletionDetector', return_value=detector):
            result = task.harvest_courtyard_affairs(max_complete_clicks=max_clicks)
        return result, task, detector

    def test_reopened_done_page_needs_two_samples_without_reclaim(self):
        success, task, detector = self.run_frames([{'done'}, {'done'}])
        self.assertTrue(success)
        self.assertFalse(task.clicks)
        self.assertEqual(detector.ready.call_count, 2)

    def test_reward_handler_runs_first_and_resets_stability(self):
        success, task, detector = self.run_frames([
            {'done'}, {'done', 'reward'}, {'done'}, {'done'}])
        self.assertTrue(success)
        self.assertFalse(task.frames)
        self.assertEqual(detector.ready.call_count, 3)

    def test_award_click_runs_first_and_resets_stability(self):
        success, task, detector = self.run_frames([
            {'done'}, {'done', 'I_UI_AWARD'}, {'done'}, {'done'}])
        self.assertTrue(success)
        self.assertEqual(task.clicks, [('I_UI_AWARD', .2)])
        self.assertEqual(detector.ready.call_count, 3)

    def test_a_transition_frame_resets_stability(self):
        success, task, detector = self.run_frames([
            {'done'}, {'transition'}, {'done'}, {'done'}])
        self.assertTrue(success)
        self.assertEqual(detector.ready.call_count, 4)

    def test_done_confirmation_takes_precedence_over_claim_limit(self):
        success, task, detector = self.run_frames([
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_COMPLETE_TASKS'},
            {'done'}, {'done'}], max_clicks=1)
        self.assertTrue(success)
        self.assertEqual(task.clicks, [('I_COMPLETE_TASKS', 2.3)])

    def test_takeover_exception_is_propagated(self):
        task = CompletionFrames([], {'I_BLUE_PAGE'})
        task.screenshot = Mock(side_effect=RequestHumanTakeover('owner returned'))
        with patch('tasks.CourtyardAffairs.selected_skin.Timer', return_value=self.timer()):
            with self.assertRaises(RequestHumanTakeover):
                task.harvest_courtyard_affairs()


if __name__ == '__main__':
    unittest.main()
