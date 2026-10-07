"""Guild reward regressions without initializing OAS, OCR or a device."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import (
    FakeClock, FakeLogger, FakeTimer, GameStuckError, extract_methods,
)


SCRIPT_FILE = Path(__file__).resolve().parents[1] / 'tasks/KekkaiUtilize/script_task.py'


class GuildHarvestHarness:
    def __init__(self, frames, reward_last_click=float('-inf')):
        self.clock = FakeClock(step=0.33)
        self.logger = FakeLogger()
        self.frames = frames
        self.frame = {}
        self.screenshots = 0
        self.reward_last_click = reward_last_click
        self.reward_clicks = 0
        self.ap_clicks = 0
        self.ap_checks = 0
        for name in ('I_UI_REWARD', 'I_GUILD_AP', 'I_GUILD_EXPAND',
                     'I_GUILD_ASSETS', 'I_GUILD_ASSETS_RECEIVE'):
            setattr(self, name, name)
        method = extract_methods(
            SCRIPT_FILE, 'ScriptTask', ('check_guild_ap_or_assets',),
            {'Timer': lambda limit: FakeTimer(self.clock, limit),
             'time': SimpleNamespace(sleep=self.sleep), 'logger': self.logger,
             'GameStuckError': GameStuckError},
        )['check_guild_ap_or_assets']
        self.run = method.__get__(self)

    def screenshot(self):
        if self.screenshots >= 200:
            raise AssertionError('Harvest loop has no effective overall timeout')
        self.clock.advance()
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1

    def sleep(self, seconds):
        self.clock.now += seconds

    def appear(self, target, threshold=None):
        if target == self.I_UI_REWARD:
            return self.frame.get('reward_score', 0) > (
                0.73 if threshold is None else threshold)
        if target == self.I_GUILD_AP:
            return self.frame.get('ap', False)
        return False

    def ui_reward_appear_click(self, screenshot=False):
        if screenshot:
            self.screenshot()
        # BaseTask's reward helper checks its 0.4-second cooldown before matching.
        if self.clock.now - self.reward_last_click <= 0.4:
            return False
        if not self.appear(self.I_UI_REWARD, threshold=0.6):
            return False
        self.reward_last_click = self.clock.now
        self.reward_clicks += 1
        return True

    def appear_then_click(self, target, **kwargs):
        if target == self.I_GUILD_AP:
            self.ap_checks += 1
            if self.frame.get('reward_score', 0) > 0.6:
                raise AssertionError('AP entry checked while reward popup is visible')
            if self.frame.get('ap', False):
                self.ap_clicks += 1
                return True
        if target == self.I_GUILD_EXPAND:
            return self.frame.get('expand', False)
        return False


REWARD_WITH_AP = {'reward_score': 0.6461701, 'ap': True}


class GuildHarvestRewardTests(unittest.TestCase):
    def test_reward_cooldown_cannot_fall_through_to_reward_item(self):
        task = GuildHarvestHarness([
            {'ap': True}, REWARD_WITH_AP, REWARD_WITH_AP, REWARD_WITH_AP, {},
        ])
        self.assertTrue(task.run())
        self.assertEqual((task.ap_clicks, task.ap_checks, task.reward_clicks), (1, 1, 2))

    def test_popup_at_entry_is_protected_even_when_click_is_cooling_down(self):
        task = GuildHarvestHarness([REWARD_WITH_AP, {}], reward_last_click=100.0)
        self.assertFalse(task.run())
        self.assertEqual(task.ap_clicks, 0)

    def test_low_score_partially_covered_reward_still_blocks_guild_entries(self):
        task = GuildHarvestHarness([REWARD_WITH_AP, REWARD_WITH_AP, {}])
        self.assertFalse(task.run())
        self.assertEqual(task.ap_clicks, 0)
        self.assertEqual(task.reward_clicks, 1)

    def test_repeated_reward_clicks_do_not_reset_overall_timeout(self):
        task = GuildHarvestHarness([REWARD_WITH_AP])
        with self.assertRaisesRegex(GameStuckError, 'Guild harvest'):
            task.run()
        self.assertGreater(task.reward_clicks, 1)
        self.assertLess(task.screenshots, 100)
        self.assertEqual(task.ap_checks, 0)

    def test_repeated_banner_clicks_also_have_overall_timeout(self):
        task = GuildHarvestHarness([{'expand': True}])
        with self.assertRaisesRegex(GameStuckError, 'Guild harvest'):
            task.run()
        self.assertLess(task.screenshots, 100)

    def test_no_harvest_keeps_short_idle_timeout(self):
        task = GuildHarvestHarness([{}])
        self.assertFalse(task.run())
        self.assertLess(task.screenshots, 10)

    def test_successful_ap_harvest_returns_after_reward_disappears(self):
        task = GuildHarvestHarness([{'ap': True}, {'reward_score': 0.9}, {}])
        self.assertTrue(task.run())
        self.assertEqual((task.ap_clicks, task.reward_clicks), (1, 1))


if __name__ == '__main__':
    unittest.main()
