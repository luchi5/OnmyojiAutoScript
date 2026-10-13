"""Boss reward overlay must dismiss before accepting the summary beneath it."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods
ROOT=Path(__file__).resolve().parents[1]

def harness(frames):
    h=SimpleNamespace(index=0,clicks=[],device=Mock())
    for n in ('I_BOSS_SHARE','I_BOSS_FIRE','I_BOSS_BATTLE_GIVEUP','I_BOSS_USE_DOUBLE','I_BOSS_GET_EXP','I_UI_CANCEL','I_UI_CONFIRM_SAMLL','I_BOSS_SKIP','I_BOSS_SHUTU','I_MSTART'):
        setattr(h,n,n)
    def screenshot():
        h.frame=frames[min(h.index,len(frames)-1)];h.index+=1
        assert h.index<15
    h.screenshot=screenshot;h.appear=lambda n,**kw:n in h.frame
    def click(n,**kw):
        if n in h.frame:h.clicks.append(kw.get('action',n));return True
        return False
    h.appear_then_click=click;h.ui_reward_appear_click=lambda:False
    h.ui_click_until_disappear=Mock();h.wait_until_appear=lambda *a,**kw:False
    ns={'logger':Mock(),'RuleImage':lambda **kw:'reward','RuleClick':lambda **kw:'blank'}
    for name,m in extract_methods(ROOT/'tasks/SixRealms/moon_sea/moon_sea.py','MoonSea',('handle_boss_reward','boss_battle'),ns).items():setattr(h,name,m.__get__(h))
    return h

class RewardTests(unittest.TestCase):
    def test_overlay_before_underlying_share(self):
        h=harness([{'reward','I_BOSS_SHARE'},{'I_BOSS_SHARE'}]);h.boss_battle()
        self.assertEqual(h.clicks,['blank']);self.assertEqual(h.index,2)
    def test_double_reward_then_overlay_then_summary(self):
        h=harness([{'I_BOSS_USE_DOUBLE'},{'reward'},{'I_BOSS_SHARE'}]);h.boss_battle()
        self.assertEqual(h.clicks,['I_BOSS_USE_DOUBLE','blank'])
    def test_unrecognized_frame_does_not_click_blank(self):
        h=harness([set()]);h.screenshot();self.assertFalse(h.handle_boss_reward());self.assertEqual(h.clicks,[])

if __name__=='__main__':unittest.main()
