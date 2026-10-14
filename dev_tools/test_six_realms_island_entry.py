"""Offline moving-island and bounded-entry regression tests."""
from enum import Enum
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
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
        self.handle_poor_store_entry = lambda: False
        self.I_M_STORE_ACTIVITY = 'active'
        ns = {'MoonSeaType': Kinds, 'Timer': lambda t: FakeTimer(self.clock, t),
              'GameStuckError': RuntimeError, 'logger': FakeLogger()}
        for name, method in extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py',
                'MoonSeaMap', ('enter_island', 'entry_island_random', 'visible_islands'), ns).items():
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

class PoorStoreDialogTests(unittest.TestCase):
    def make_task(self, texts):
        h=SimpleNamespace(I_UI_CANCEL='cancel', appear=lambda _:True,
                          device=SimpleNamespace(image=None),click=Mock())
        h.map_ocr=SimpleNamespace(detect_and_ocr=lambda **kw:[
            SimpleNamespace(ocr_text=text,box=[[720,420],[780,420],[780,460],[720,460]]) for text in texts])
        ns={'RuleClick':lambda **kw:kw['name'],'logger':Mock()}
        f=extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py','MoonSeaMap',('handle_poor_store_entry',),ns)['handle_poor_store_entry']
        h.handle=lambda:f(h)
        return h

    def test_insufficient_coins_enters_instead_of_cancel(self):
        h=self.make_task(['大人的万相铃不足，是否仍要进入宁','息之屿？','取消','进入'])
        self.assertTrue(h.handle())
        h.click.assert_called_once_with('poor_store_enter',interval=2)

    def test_other_warning_not_confirmed(self):
        h=self.make_task(['是否离开神秘之屿','取消','进入'])
        self.assertFalse(h.handle());h.click.assert_not_called()

    def test_missing_enter_does_not_cancel_known_warning(self):
        h=self.make_task(['万相铃不足，是否仍要进入宁息之屿','取消'])
        self.assertTrue(h.handle());h.click.assert_not_called()

class ShopTests(unittest.TestCase):
    def test_only_shop_must_remain_even_without_money(self):
        h = Harness([{'SHOP':(650,416),'poor':True}, {'main':False,'exit':True}])
        h.entry_island_random()
        self.assertEqual(h.clicks, [('SHOP',(650,416))])

    def test_overlapping_summon_templates_do_not_exclude_shop(self):
        h = Harness([{'SHOP':True,'FIRE':True,'poor':True,'active':True}])
        h.screenshot()
        self.assertEqual(h.visible_islands(2)[0][0], Kinds.island101)

    def test_skip_shop_only_with_alternative_and_disabled_summon(self):
        h = Harness([{'SHOP':True,'FIRE':True,'poor':True}])
        h.screenshot()
        self.assertEqual([k for k,_ in h.visible_islands(2)], [Kinds.island104])
        self.assertEqual(h.visible_islands(1)[0][0], Kinds.island101)

    def test_star_before_normal_battle(self):
        h = Harness([{'STAR':True,'FIRE':True}])
        h.screenshot()
        self.assertEqual(h.visible_islands()[0][0], Kinds.island105)

if __name__ == '__main__':
    unittest.main()
