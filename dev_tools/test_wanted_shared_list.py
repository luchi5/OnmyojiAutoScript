from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import numpy as np
from dev_tools.test_bondling_battle_entry import extract_methods

SOURCE = Path(__file__).resolve().parents[1] / 'tasks/WantedQuests/script_task.py'

class End(Exception):
    pass

def item(text):
    return SimpleNamespace(ocr_text=text, box=np.array([[10,20],[40,20],[40,40],[10,40]]))

class SharedListTests(unittest.TestCase):
    def test_completed_progress_is_not_overridden_by_background_text(self):
        h = self.harness()
        results = [item('协'), item('8/8'), item('悬赏封印'), item('子倒是老老'), item('事，我倒也')]
        self.assertEqual(h.find_wq(object(), results), (-2, -2, -2, [0, 0, 0, 0]))

    def test_unreadable_progress_does_not_guess_three_remaining(self):
        h = self.harness()
        self.assertEqual(h.find_wq(object(), [item('悬赏封印'), item('识别不清')]),
                         (-1, -1, -1, [0, 0, 0, 0]))

    def harness(self):
        h = SimpleNamespace()
        h.O_WQ_WANTED = SimpleNamespace(roi=(15,116,85,401), detect_and_ocr=Mock())
        h.O_WQ_TEXT_ALL = SimpleNamespace(area=None)
        ns={'logger':Mock(),'sleep':lambda _:None,'TaskEnd':End}
        for n,m in extract_methods(SOURCE,'ScriptTask',('run','find_wq','is_wq_remained'),ns).items():
            setattr(h,n,m.__get__(h))
        return h

    def test_shared_progress_and_coordinates_without_second_ocr(self):
        h=self.harness(); results=[item('悬赏封印'),item('0/12')]
        self.assertTrue(h.is_wq_remained(results))
        self.assertEqual(h.find_wq(object(),results),(0,1,12,[25,136,30,20]))
        h.O_WQ_WANTED.detect_and_ocr.assert_not_called()

    def test_reward_priority_and_new_frame_after_actions(self):
        h=self.harness()
        conf=SimpleNamespace(wanted_quests_config=SimpleNamespace(unwanted_boss_names='',cooperation_only=False),
            switch_soul_config=SimpleNamespace(enable=False,enable_switch_by_name=False))
        h.config=SimpleNamespace(model=SimpleNamespace(wanted_quests=conf))
        h.get_config=lambda:conf.wanted_quests_config
        h.pre_work=lambda:True;h.current_count=0
        h.device=SimpleNamespace(image=None);frames=iter(['initial','box','mission','scroll','end'])
        h.screenshot=lambda:setattr(h.device,'image',next(frames))
        h.O_WQ_NUMBER=SimpleNamespace(ocr=lambda _:0)
        h.I_WQ_BOX='box';h.I_TREASURE_BOX_CLICK='treasure';h.S_WQ_LIST_UP='up'
        h.appear=lambda target:target==h.device.image
        h.ui_get_reward=Mock();h.execute_mission=Mock();h.swipe=Mock();h.next_run=Mock()
        h.O_WQ_WANTED.detect_and_ocr.side_effect=lambda im: {'mission':[item('悬赏封印'),item('0/2')],
            'scroll':[item('悬赏封印')],'end':[]}[im]
        with self.assertRaises(End):h.run()
        h.ui_get_reward.assert_called_once_with('box')
        self.assertEqual([c.args[0] for c in h.O_WQ_WANTED.detect_and_ocr.call_args_list],['mission','scroll','end'])
        h.execute_mission.assert_called_once_with(h.O_WQ_TEXT_ALL,2,0)
        h.swipe.assert_called_once()

if __name__=='__main__':unittest.main()
