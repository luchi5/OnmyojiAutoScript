from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import cv2
from dev_tools.test_bondling_battle_entry import extract_methods
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
ROOT=Path(__file__).resolve().parents[1]
class ResumeTests(unittest.TestCase):
    def harness(self):
        h=SimpleNamespace(_config=SimpleNamespace(general_battle_config=SimpleNamespace(random_click_swipt_enable=False)),
            appear=Mock(return_value=False),is_in_real_battle=Mock(return_value=False),is_in_prepare=Mock(return_value=False),
            battle_wait=Mock(return_value=True),run_general_battle=Mock())
        for name in ('I_REWARD','I_WIN','I_FALSE'):setattr(h,name,getattr(GeneralBattleAssets,name))
        m=extract_methods(ROOT/'tasks/Exploration/base.py','BaseExploration',('resume_exploration_encounter',),{'logger':Mock()})
        h.resume=m['resume_exploration_encounter'].__get__(h)
        return h
    def test_actual_reward_frame_routes_to_settlement(self):
        h=self.harness();im=cv2.cvtColor(cv2.imread(str(ROOT/'dev_tools/fixtures/wanted_exploration_settlement.png')),cv2.COLOR_BGR2RGB)
        h.appear=lambda marker:marker.match(im)
        self.assertTrue(h.resume());h.battle_wait.assert_called_once();h.run_general_battle.assert_not_called()
    def test_running_battle_is_resumed_without_reapplying_presets(self):
        h=self.harness();h.is_in_real_battle.return_value=True
        self.assertTrue(h.resume());h.battle_wait.assert_called_once();h.run_general_battle.assert_not_called()
    def test_prepare_uses_full_battle_flow(self):
        h=self.harness();h.is_in_prepare.return_value=True
        self.assertTrue(h.resume());h.run_general_battle.assert_called_once();h.battle_wait.assert_not_called()
    def test_map_or_unknown_does_not_trigger_battle_clicks(self):
        h=self.harness();self.assertFalse(h.resume());h.run_general_battle.assert_not_called();h.battle_wait.assert_not_called()
    def test_victory_and_defeat_are_settled(self):
        for name in ('I_WIN','I_FALSE'):
            h=self.harness();h.appear=lambda marker:marker==getattr(h,name)
            self.assertTrue(h.resume());h.battle_wait.assert_called_once()
    def test_actual_reward_is_clicked_by_wanted_battle_wait(self):
        h=self.harness();im=cv2.cvtColor(cv2.imread(str(ROOT/'dev_tools/fixtures/wanted_exploration_settlement.png')),cv2.COLOR_BGR2RGB)
        h.I_SE_BATTLE_WIN=object();h.screenshot=Mock();h.device=SimpleNamespace(stuck_record_add=Mock(),click_record_clear=Mock())
        h.appear=lambda marker:marker.match(im) if hasattr(marker,'match') else False
        h.appear_then_click=Mock(return_value=False);h.ui_click_until_disappear=Mock()
        m=extract_methods(ROOT/'tasks/WantedQuests/script_task.py','ScriptTask',('battle_wait',),{'logger':Mock(),'WantedBattleAuto':lambda:SimpleNamespace(inspect=Mock())})
        self.assertTrue(m['battle_wait'](h,False));h.ui_click_until_disappear.assert_called_once_with(h.I_REWARD)
if __name__=='__main__':unittest.main()
