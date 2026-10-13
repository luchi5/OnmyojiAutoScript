"""Own growth skips max-level candidates and never loops on unavailable cards."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger


def run(full=(), usable=(), count=1, lost=False):
    h = SimpleNamespace(clicks=[], empty=count, I_RS_NO_ADD='empty',
                        I_RS_LEVEL_MAX=SimpleNamespace(file='max.png'),
                        I_U_CONFIRM_SMALL='confirm', I_UI_CONFIRM='confirm2',
                        I_UI_CONFIRM_SAMLL='confirm3')
    slots = {i:SimpleNamespace(roi_front=(i*100,487,80,180),index=i) for i in range(1,8)}
    h.screenshot = lambda:None
    h.in_shikigami_growth = lambda:not lost
    h.appear = lambda marker: h.empty>0 if marker=='empty' else marker in full
    h.appear_then_click = lambda *a,**kw:False
    def click(slot,**kw):
        h.clicks.append(slot.index)
        if slot.index in usable:
            h.empty -= 1
    h.click = click
    method = extract_methods(Path(__file__).resolve().parents[1]/'tasks/Component/ReplaceShikigami/replace_shikigami.py',
                             'ReplaceShikigami',('_fill_own_growth',),{
        'RuleImage':lambda **kw:(kw['roi_back'][0]+15)//100,
        'GameStuckError':RuntimeError,'time':SimpleNamespace(sleep=lambda t:None),'logger':FakeLogger(),
    })['_fill_own_growth']
    h.result = method(h,7,slots)
    return h


class OwnFillTests(unittest.TestCase):
    def test_max_level_seven_through_three_choose_available_two(self):
        h = run(full=(3,4,5,6,7),usable=(2,))
        self.assertTrue(h.result)
        self.assertEqual(h.clicks,[2])

    def test_unavailable_card_is_tried_only_once(self):
        h = run(full=(3,4,5,6,7))
        self.assertFalse(h.result)
        self.assertEqual(h.clicks,[2,1])

    def test_partial_fill_leaves_remaining_slots_without_looping(self):
        h = run(full=(3,4,5,6,7),usable=(2,),count=3)
        self.assertFalse(h.result)
        self.assertEqual(h.empty,2)

    def test_full_growth_needs_no_click(self):
        self.assertEqual(run(count=0).clicks,[])

    def test_lost_page_does_not_click(self):
        with self.assertRaisesRegex(RuntimeError,'page lost'):
            run(lost=True)


if __name__ == '__main__':
    unittest.main()
