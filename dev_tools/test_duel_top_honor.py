from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
import cv2
import numpy as np
from tasks.Duel.assets import DuelAssets
from dev_tools.test_demon_preset_transition import extract
ROOT=Path(__file__).resolve().parents[1]

class TopHonorTests(unittest.TestCase):
    def test_template_and_shift_with_unrelated_background(self):
        template=cv2.imread(str(ROOT/'tasks/Duel/duel/duel_d_top_honor.png'))
        for dx,dy in [(0,0),(12,8),(-12,-8)]:
            frame=np.full((720,1280,3),40,np.uint8)
            frame[35+dy:250+dy,965+dx:1220+dx]=template
            self.assertTrue(DuelAssets.I_D_TOP_HONOR.match(frame))
        self.assertFalse(DuelAssets.I_D_TOP_HONOR.match(np.zeros((720,1280,3),np.uint8)))

    def test_showcase_then_home_preserves_win_and_avoids_share(self):
        timer=lambda *a: NS(start=lambda:None,started=lambda:False,reached=lambda:False)
        fn=extract(ROOT/'tasks/Duel/script_task.py','ScriptTask',('wait_battle',),
                   {'Timer':timer,'logger':Mock()})['wait_battle']
        h=NS(frame=-1,I_D_TOP_HONOR='showcase',I_CHECK_DUEL='home',I_D_HELP='help',
             C_D_RESULT_CONTINUE='safe',check_and_get_reward=Mock(),click=Mock())
        def screenshot(): h.frame+=1
        h.screenshot=screenshot
        h.appear=lambda marker,**kw: marker in ({'showcase'} if h.frame==0 else {'home','help'})
        self.assertIs(fn(h),True)
        h.click.assert_called_once_with('safe',interval=1.2)

    def test_prepare_recognizes_showcase_as_battle_win(self):
        fn=extract(ROOT/'tasks/Duel/script_task.py','ScriptTask',('is_battle_win',),{})['is_battle_win']
        h=NS(I_WIN='old',I_D_VICTORY='duel',I_D_TOP_HONOR='new',appear=lambda x:x=='new')
        self.assertTrue(fn(h))

if __name__=='__main__':unittest.main()
