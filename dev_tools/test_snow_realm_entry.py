"""Real screenshot crops across snow realm entry states and growth overlay."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import cv2
import numpy as np
from dev_tools.test_compact_realm_harvest import RuleImage
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer, FakeLogger

ROOT = Path(__file__).resolve().parents[1]


class SnowEntryTests(unittest.TestCase):
    def replay(self, index):
        crop = cv2.imdecode(np.frombuffer(
            (ROOT/f'dev_tools/fixtures/snow_realm_entry_{index}.png').read_bytes(), np.uint8), 1)
        frame = np.zeros((720,1280,3), dtype=np.uint8)
        frame[330:540,760:950] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        clock = FakeClock(1)
        h = SimpleNamespace(I_A_CHECK_CARD='page', I_A_AUTO_INVITE='invite',
                            I_SHI_CARD='normal', clicks=[])
        h.screenshot = clock.advance
        h.appear = lambda marker: bool(h.clicks)
        def click(rule, multi_scale=False, **kw):
            if isinstance(rule, str):
                return False
            match = rule.match_multi_scale if multi_scale else rule.match
            if match(frame):
                h.clicks.append(tuple(rule.roi_front))
                return True
            return False
        h.appear_then_click = click
        h.appear_then_click_multi_scale = lambda rule, **kw: click(rule, multi_scale=True, **kw)
        fn = extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py', 'ScriptTask',
            ('goto_cards',), {'RuleImage':RuleImage, 'Timer':lambda t:FakeTimer(clock,t),
                             'GameStuckError':RuntimeError, 'logger':FakeLogger()})['goto_cards']
        return h, lambda: fn(h)

    def test_reward_present_and_two_collected_states(self):
        for index in range(3):
            with self.subTest(index=index):
                h, run = self.replay(index)
                run()
                self.assertEqual(len(h.clicks),1)
                x,y,w,height = h.clicks[0]
                self.assertTrue(800 <= x <= 850 and 400 <= y <= 450, h.clicks)

    def test_growth_overlay_does_not_match_card_entry(self):
        h, run = self.replay(3)
        with self.assertRaises(RuntimeError):
            run()
        self.assertEqual(h.clicks,[])

    def test_freshly_collected_snow_card_enters_without_restart(self):
        # Account 11, 2026-10-15 18:18:11: the reward was collected, but
        # none of the old plaque templates reached the 0.85 threshold.
        h, run = self.replay(4)
        run()
        self.assertEqual(len(h.clicks), 1)
        x,y,w,height = h.clicks[0]
        self.assertTrue(815 <= x <= 835 and 410 <= y <= 435, h.clicks)


if __name__ == '__main__':
    unittest.main()
