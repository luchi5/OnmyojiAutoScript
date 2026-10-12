"""Sign-in transitions shared by all courtyard skins, without game input."""
import unittest
from unittest.mock import patch

from tasks.CourtyardAffairs.rewards import CourtyardRewardMixin
from dev_tools import test_courtyard_skin_options as skin_tests


class RewardFrames(CourtyardRewardMixin):
    def __init__(self, frames, throttled=False):
        self.frames = [set(frame) for frame in frames]
        self.frame = set()
        self.clock = 0
        self.clicks = []
        self.throttled = throttled
        self.I_PAGE = 'courtyard'
        self.I_CHECK_MAIN = 'main'
        self.I_UI_BACK_YELLOW = 'back'
        self.I_UI_REWARD = 'reward'
        self.C_UI_REWARD = 'reward-close'
        self.I_UI_AWARD = 'award'
        for name in ('I_HARVEST_SIGN', 'I_HARVEST_SIGN_2', 'I_HARVEST_SIGN_3',
                     'I_HARVEST_SIGN_4', 'I_HARVEST_SIGN_999', 'I_LOGIN_RED_CLOSE'):
            setattr(self, name, name)

    def screenshot(self):
        self.clock += 1
        if self.frames:
            self.frame = self.frames.pop(0)

    def appear(self, rule, **kwargs):
        return rule in self.frame

    def appear_then_click(self, rule, action=None, **kwargs):
        if self.appear(rule) and not self.throttled:
            self.clicks.append(action or rule)
            return True
        return False

    def finish(self, timeout=30):
        owner = self

        class Timer:
            def __init__(self, limit):
                self.limit = limit
                self.start_time = owner.clock

            def start(self):
                return self

            def reset(self):
                self.start_time = owner.clock

            def reached(self):
                return owner.clock - self.start_time >= self.limit

        with patch('tasks.CourtyardAffairs.rewards.Timer', Timer):
            return self.finish_courtyard_rewards(timeout)


class CourtyardRewardTests(unittest.TestCase):
    def test_az_ordinary_sign_claim_reward_close_and_return(self):
        task = RewardFrames([
            {'I_HARVEST_SIGN'}, {'I_HARVEST_SIGN_2', 'courtyard'},
            {'reward'}, {'I_LOGIN_RED_CLOSE'}, {'courtyard', 'back'}, {'main'},
        ])
        self.assertTrue(task.finish())
        self.assertEqual(task.clicks, ['I_HARVEST_SIGN', 'I_HARVEST_SIGN_2',
                                      'reward-close', 'I_LOGIN_RED_CLOSE', 'back'])

    def test_special_signs_and_999_day_bag(self):
        task = RewardFrames([
            {'I_HARVEST_SIGN_3'}, {'I_HARVEST_SIGN_4'},
            {'I_HARVEST_SIGN_999'}, {'main'},
        ])
        self.assertTrue(task.finish())
        self.assertEqual(task.clicks, ['I_HARVEST_SIGN_3', 'I_HARVEST_SIGN_4',
                                      'I_HARVEST_SIGN_999'])

    def test_delayed_sign_popup_resets_quiet_wait(self):
        task = RewardFrames([
            {'main'}, {'main'}, {'main'}, {'I_HARVEST_SIGN_2'}, {'main'},
        ])
        self.assertTrue(task.finish())
        self.assertGreaterEqual(task.clock, 9)
        self.assertEqual(task.clicks, ['I_HARVEST_SIGN_2'])

    def test_throttled_sign_never_clicks_underlying_return(self):
        task = RewardFrames([{'I_HARVEST_SIGN_2', 'courtyard'}], throttled=True)
        self.assertFalse(task.finish(timeout=8))
        self.assertEqual(task.clicks, [])
        self.assertLessEqual(task.clock, 8)

    def test_unrecognized_page_is_bounded(self):
        task = RewardFrames([{'unknown'}])
        self.assertFalse(task.finish(timeout=8))
        self.assertEqual(task.clicks, [])

    def test_minimum_configured_timeout_can_finish_on_main(self):
        task = RewardFrames([{'main'}])
        self.assertTrue(task.finish(timeout=5))

    def test_main_under_reward_overlay_is_not_finished_early(self):
        task = RewardFrames([{'main', 'reward'}], throttled=True)
        self.assertFalse(task.finish(timeout=8))
        self.assertEqual(task.clicks, [])

    def test_skin_claim_loop_handles_sign_before_repeating_claim(self):
        # Use the actual skin loop, so adding only a post-task close cannot
        # mask repeated claims while a sign-in covers the daily screen.
        runner = skin_tests.CourtyardSkinClaimLoopTests()
        from dev_tools.test_courtyard_skin_options import ClaimFrames, ImageRule
        original = ClaimFrames.__init__

        def initialize(task, *args, **kwargs):
            original(task, *args, **kwargs)
            task.I_HARVEST_SIGN_2 = ImageRule('I_HARVEST_SIGN_2')

        with patch.object(ClaimFrames, '__init__', initialize):
            success, task, _ = runner.run_frames([
                {'I_PAGE', 'I_COMPLETE_TASKS'},
                {'I_HARVEST_SIGN_2', 'I_PAGE', 'I_COMPLETE_TASKS'},
                {'I_NO_TASKS'},
            ], skin=skin_tests.CourtyardSkin.DEFAULT, entry={'I_PAGE'})
        self.assertTrue(success)
        self.assertEqual(task.actions, ['I_COMPLETE_TASKS', 'I_HARVEST_SIGN_2'])


if __name__ == '__main__':
    unittest.main()
