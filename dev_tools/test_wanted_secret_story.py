"""Replay post-battle secret story transitions without a device or backend."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

ROOT = Path(__file__).resolve().parents[1]
FIRE = {'I_WQSE_FIRE'}
STORY = {'I_SECRET_STORY'}
ZONES = {'I_CHECK_SECRET_ZONES'}
EXPLORE = {'I_CHECK_EXPLORATION'}
RACE = {'I_UI_BACK_BLUE', 'I_SE_FIRE', 'I_SE_PLACEMENT'}


class Harness:
    def __init__(self, frames):
        self.frames = frames
        self.current = set()
        self.captures = 0
        self.clicks = []
        self.battles = 0
        self.routes = []
        self.battle_config = object()
        self.clock = FakeClock(1)
        self.device = SimpleNamespace(click_record_clear=lambda: None)
        for name in set.union(FIRE, STORY, ZONES, EXPLORE, RACE,
                              {'I_UI_BACK_RED', 'C_SECRET_CHAT'}):
            setattr(self, name, name)
        methods = extract_methods(ROOT/'tasks/WantedQuests/script_task.py', 'ScriptTask',
                                  ('secret',), {
            'Timer': lambda seconds: FakeTimer(self.clock, seconds),
            'logger': FakeLogger(), 'GameStuckError': RuntimeError,
            'page_exploration': 'exploration',
            'RuleClick': lambda **kw: 'C_SECRET_CHAT',
            'RuleImage': lambda **kw: 'kingyo_story' if 'kingyo' in kw['file'] else 'inugami_story',
        })
        self.secret = methods['secret'].__get__(self)
        self.advance_secret_story = self.click

    def screenshot(self):
        self.current = self.frames[min(self.captures, len(self.frames)-1)]
        self.captures += 1
        self.clock.advance()
        assert self.captures < 150, 'Unbounded transition'

    def appear(self, marker, **kwargs):
        return marker in self.current

    def click(self, marker, **kwargs):
        self.clicks.append(marker)

    def appear_then_click(self, marker, **kwargs):
        if self.appear(marker):
            self.click(marker)
            return True
        return False

    def ui_click(self, *args, **kwargs):
        return True

    def run_general_battle(self, config):
        self.battles += 1
        return True

    def goto_page(self, page):
        self.routes.append(page)

    def wait_until_stable(self, marker):
        pass


class SecretStoryTests(unittest.TestCase):
    def test_kingyo_story_between_battles_and_at_exit(self):
        h = Harness([FIRE, set(), {'kingyo_story'}, FIRE, set(), {'kingyo_story'}, ZONES, EXPLORE])
        self.assertTrue(h.secret('goto', num=2))
        self.assertEqual(h.battles, 2)
        self.assertEqual(h.clicks, ['C_SECRET_CHAT']*2)

    def test_story_between_two_battles_and_after_last_battle(self):
        h = Harness([FIRE, set(), STORY, STORY, FIRE, set(), STORY, ZONES, EXPLORE])
        self.assertTrue(h.secret('goto', num=2))
        self.assertEqual(h.battles, 2)
        self.assertEqual(h.clicks, ['C_SECRET_CHAT']*3)
        self.assertEqual(h.routes, ['exploration'])

    def test_inugami_dialogue_before_battle_is_dismissed_first(self):
        h = Harness([FIRE, {'inugami_story','I_UI_BACK_RED','I_WQSE_FIRE'},
                     {'inugami_story','I_UI_BACK_RED'}, set(), ZONES, EXPLORE])
        self.assertTrue(h.secret('goto'))
        self.assertEqual(h.clicks,['C_SECRET_CHAT','C_SECRET_CHAT'])
        self.assertEqual(h.battles,1)

    def test_long_dialogue_can_continue_past_old_twenty_second_limit(self):
        h = Harness([FIRE] + [{'inugami_story','I_UI_BACK_RED'}]*30 + [set(),ZONES,EXPLORE])
        self.assertTrue(h.secret('goto'))
        self.assertEqual(h.battles,1)
        self.assertEqual(len(h.clicks),30)

    def test_single_battle_also_closes_final_story(self):
        h = Harness([FIRE, set(), STORY, ZONES, EXPLORE])
        self.assertTrue(h.secret('goto'))
        self.assertEqual(h.clicks, ['C_SECRET_CHAT'])

    def test_unknown_post_battle_screen_times_out_without_clicking(self):
        h = Harness([FIRE, set()])
        with self.assertRaisesRegex(RuntimeError, 'entry did not reappear'):
            h.secret('goto', num=2)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.battles, 1)

    def test_story_never_finishing_keeps_bounded_timeout(self):
        for num, message in ((1, 'zone list'), (2, 'entry did not reappear')):
            with self.subTest(num=num):
                h = Harness([FIRE, set(), STORY])
                with self.assertRaisesRegex(RuntimeError, message):
                    h.secret('goto', num=num)
                self.assertLess(h.captures, 110)
                self.assertEqual(h.battles, 1)

    def test_weekly_race_still_rechecks_wanted_progress(self):
        h = Harness([FIRE, set(), STORY, RACE, RACE, ZONES, EXPLORE])
        self.assertFalse(h.secret('goto', num=2))
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.clicks, ['C_SECRET_CHAT', 'I_UI_BACK_BLUE'])


class StoryProgressTests(unittest.TestCase):
    def test_only_new_nonempty_dialogue_resets_click_guard(self):
        from unittest.mock import Mock
        values=iter(['犬神说话','犬神说话','','晴明说话'])
        reader=SimpleNamespace(detect_and_ocr=lambda image:[SimpleNamespace(ocr_text=next(values))])
        method=extract_methods(ROOT/'tasks/WantedQuests/script_task.py','ScriptTask',
                              ('advance_secret_story',),{'RuleOcr':lambda **kw:reader})['advance_secret_story']
        h=SimpleNamespace(device=SimpleNamespace(image=None,click_record_clear=Mock()),click=Mock())
        for _ in range(4):method(h,'chat')
        self.assertEqual(h.device.click_record_clear.call_count,2)
        self.assertEqual(h.click.call_count,4)

if __name__ == '__main__':
    unittest.main()
