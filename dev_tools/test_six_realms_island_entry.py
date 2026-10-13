"""Offline moving-island and bounded-entry regression tests."""
from enum import Enum
from pathlib import Path
import unittest
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

Kinds = Enum('Kinds', 'island100 island101 island102 island103 island104 island105')
ROOT = Path(__file__).resolve().parents[1]

class Harness:
    def __init__(self, frames):
        self.frames, self.index, self.clicks = frames, 0, []
        self.clock = FakeClock(1)
        for name in ('SHOP', 'MYSTERY', 'CHAOS', 'FIRE', 'STAR'):
            setattr(self, 'I_LAND_' + name, name)
        self.I_BACK_EXIT, self.I_UI_CANCEL, self.I_M_STORE = 'exit', 'cancel', 'poor'
        ns = {'MoonSeaType': Kinds, 'Timer': lambda t: FakeTimer(self.clock, t),
              'GameStuckError': RuntimeError, 'logger': FakeLogger()}
        for name, method in extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py',
                'MoonSeaMap', ('enter_island', 'entry_island_random'), ns).items():
            setattr(self, name, method.__get__(self))

    def screenshot(self):
        self.frame = self.frames[min(self.index, len(self.frames)-1)]
        self.index += 1
        self.clock.advance()
        assert self.index < 30

    def in_main(self):
        return self.frame.get('main', True)

    def appear(self, target):
        return target in self.frame

    def appear_then_click(self, target, **kwargs):
        if self.appear(target):
            self.clicks.append((target, self.frame[target]))
            return True
        return False

class EntryTests(unittest.TestCase):
    def test_moving_island_uses_each_new_position(self):
        h = Harness([{'MYSTERY': (790,450)}, {'MYSTERY': (919,472)}, {'main':False,'exit':True}])
        h.enter_island(Kinds.island102, (773,332,35,93))
        self.assertEqual(h.clicks, [('MYSTERY',(790,450)),('MYSTERY',(919,472))])

    def test_missing_target_does_not_click_old_roi_or_other_island(self):
        h = Harness([{'FIRE': (390,415)}])
        with self.assertRaises(RuntimeError):
            h.enter_island(Kinds.island102, (773,332,35,93))
        self.assertEqual(h.clicks, [])

    def test_visible_but_unresponsive_is_bounded(self):
        h = Harness([{'MYSTERY': (919,472)}])
        with self.assertRaises(RuntimeError):
            h.enter_island(Kinds.island102, None)
        self.assertEqual(len(h.clicks), 6)

    def test_unknown_chooses_visible_and_keeps_choice(self):
        h = Harness([{'FIRE': (390,415)}, {'SHOP':(300,300),'FIRE':(400,420)}, {'main':False,'exit':True}])
        h.entry_island_random()
        self.assertEqual([v[0] for v in h.clicks], ['FIRE','FIRE'])

    def test_transition_does_not_click(self):
        h = Harness([{'main':False,'MYSTERY':(919,472)}, {'main':False,'exit':True}])
        h.enter_island(Kinds.island102, None)
        self.assertEqual(h.clicks, [])

if __name__ == '__main__':
    unittest.main()
