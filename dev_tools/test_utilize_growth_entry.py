"""Replay friend realm transitions and prevent class clicks on the homepage."""
from enum import Enum
from pathlib import Path
import unittest
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

ROOT = Path(__file__).resolve().parents[1]
Rarity = Enum('Rarity','MATERIAL N R SR SSR SP UR')


class Harness:
    def __init__(self, frames):
        self.frames = frames
        self.current = set()
        self.captures = 0
        self.clicks = []
        self.clock = FakeClock(1)
        for name in ('I_U_ENTER_REALM','I_SHI_GROWN','I_CHECK_FRIEND_REALM_2',
                     'I_RS_RECORDS_SHIKI','C_SHIKIGAMI_SWITCH_1'):
            setattr(self,name,name)
        for rarity in Rarity:
            for suffix in ('','_SELECTED'):
                name = 'I_RS_'+rarity.name+suffix
                setattr(self,name,name)
        ns = {'Timer':lambda t:FakeTimer(self.clock,t),'logger':FakeLogger(),
              'ShikigamiClass':Rarity,'GameStuckError':RuntimeError}
        methods = extract_methods(ROOT/'tasks/KekkaiUtilize/script_task.py','ScriptTask',
                                  ('_enter_friend_growth',),ns)
        methods.update(extract_methods(ROOT/'tasks/Component/ReplaceShikigami/replace_shikigami.py',
                                       'ReplaceShikigami',('switch_shikigami_class','in_shikigami_growth'),ns))
        for name, method in methods.items():
            setattr(self,name,method.__get__(self))

    def screenshot(self):
        self.current = self.frames[min(self.captures,len(self.frames)-1)]
        self.captures += 1
        self.clock.advance()
        assert self.captures < 100

    def appear(self,name,**kw):
        return name in self.current

    def click(self,name,**kw):
        self.clicks.append(name)
        return True

    def appear_then_click(self,name,**kw):
        return self.appear(name) and self.click(name)

    appear_then_click_multi_scale = appear_then_click

    def wait_animate_stable(self,**kw):
        pass

    def wait_until_pos_stable(self,*args,**kw):
        return True


class GrowthTests(unittest.TestCase):
    def test_homepage_opens_growth_and_requires_two_frames(self):
        h = Harness([{'I_U_ENTER_REALM'},{'I_SHI_GROWN'},
                     {'I_RS_RECORDS_SHIKI'},{'I_RS_RECORDS_SHIKI'}])
        self.assertTrue(h._enter_friend_growth())
        self.assertEqual(h.captures,4)
        self.assertEqual(h.clicks,['I_U_ENTER_REALM','I_SHI_GROWN'])

    def test_transient_slot_marker_does_not_confirm_growth(self):
        h = Harness([{'I_U_ADD_1'}])
        self.assertFalse(h._enter_friend_growth())
        self.assertEqual(h.clicks,[])

    def test_transient_growth_resets_confirmation(self):
        h = Harness([{'I_RS_RECORDS_SHIKI'},set(),{'I_RS_RECORDS_SHIKI'},{'I_RS_RECORDS_SHIKI'}])
        self.assertTrue(h._enter_friend_growth())
        self.assertEqual(h.captures,4)

    def test_homepage_never_receives_class_click(self):
        h = Harness([{'I_SHI_GROWN'}])
        with self.assertRaisesRegex(RuntimeError,'page lost'):
            h.switch_shikigami_class(Rarity.N,require_growth=True)
        self.assertEqual(h.clicks,[])

    def test_page_loss_during_animation_is_detected(self):
        h = Harness([{'I_RS_RECORDS_SHIKI'},set()])
        with self.assertRaisesRegex(RuntimeError,'while waiting'):
            h.switch_shikigami_class(Rarity.N,require_growth=True)
        self.assertEqual(h.clicks,[])

    def test_selected_class_needs_no_extra_click(self):
        h = Harness([{'I_RS_N_SELECTED','I_RS_RECORDS_SHIKI'}])
        h.switch_shikigami_class(Rarity.N,require_growth=True)
        self.assertEqual(h.clicks,[])

    def test_class_selection_has_bounded_timeout(self):
        h = Harness([{'I_RS_RECORDS_SHIKI'}])
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            h.switch_shikigami_class(Rarity.N,require_growth=True)
        self.assertLess(h.captures,30)


if __name__ == '__main__':
    unittest.main()
