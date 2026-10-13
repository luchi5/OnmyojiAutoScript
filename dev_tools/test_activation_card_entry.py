"""Bounded realm-card entry for both normal and compact realm plaques."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

ROOT = Path(__file__).resolve().parents[1]


def harness(frames):
    clock = FakeClock(1)
    h = SimpleNamespace(captures=0,current=set(),clicks=[],
                        I_A_CHECK_CARD='card_page',I_A_AUTO_INVITE='invite',I_SHI_CARD='normal')
    def screenshot():
        h.current = frames[min(h.captures,len(frames)-1)]
        h.captures += 1
        clock.advance()
        assert h.captures < 30
    def click(marker,**kw):
        if marker in h.current:
            h.clicks.append(marker)
            return True
        return False
    h.screenshot = screenshot
    h.appear = lambda marker: marker in h.current
    h.appear_then_click = click
    h.appear_then_click_multi_scale = click
    method = extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py','ScriptTask',('goto_cards',),{
        'RuleImage':lambda **kw:'compact','Timer':lambda t:FakeTimer(clock,t),
        'GameStuckError':RuntimeError,'logger':FakeLogger(),
    })['goto_cards']
    h.goto_cards = method.__get__(h)
    return h


class CardEntryTests(unittest.TestCase):
    def test_compact_plaque_enters_confirmed_card_page(self):
        h = harness([{'compact'},set(),{'card_page'}])
        h.goto_cards()
        self.assertEqual(h.clicks,['compact'])
        self.assertEqual(h.captures,3)

    def test_existing_normal_plaque_is_preserved(self):
        h = harness([{'normal'},{'invite'}])
        h.goto_cards()
        self.assertEqual(h.clicks,['normal'])

    def test_already_on_card_page_needs_no_click(self):
        h = harness([{'card_page','compact'}])
        h.goto_cards()
        self.assertEqual(h.clicks,[])

    def test_unknown_screen_times_out_without_blind_click(self):
        h = harness([set()])
        with self.assertRaisesRegex(RuntimeError,'20 seconds'):
            h.goto_cards()
        self.assertEqual(h.clicks,[])

    def test_clicked_entry_without_transition_is_not_success(self):
        h = harness([{'compact'}])
        with self.assertRaisesRegex(RuntimeError,'not confirmed'):
            h.goto_cards()


if __name__ == '__main__':
    unittest.main()
