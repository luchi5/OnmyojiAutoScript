import ast,unicodedata,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import numpy as np
import cv2
from module.atom.ocr import RuleOcr
from tasks.Component.GeneralBuff.assets import GeneralBuffAssets as Assets
ROOT=Path(__file__).resolve().parents[1]
def row(text,y=143):
 return SimpleNamespace(ocr_text=text,box=np.array([[5,y-120],[330,y-120],[330,y-120+21],[5,y-120+21]],dtype=float))
class GoldSwitchTests(unittest.TestCase):
 def test_anchor_search_is_one_direction_and_bounded(self):
  from dev_tools.test_bondling_battle_entry import extract_methods
  method=extract_methods(ROOT/'tasks/Component/GeneralBuff/general_buff.py','GeneralBuff',('_locate_buff_top',),
      {'logger':Mock(),'time':SimpleNamespace(sleep=lambda _:None)})['_locate_buff_top']
  for visible_after in [0,2,99]:
   h=SimpleNamespace(I_CLOUD='panel',I_AWAKE='awake',S_BUFF_UP='up',screenshot=Mock(),swipe=Mock())
   h.appear=lambda marker:marker=='panel' or h.swipe.call_count>=visible_after
   self.assertEqual(method(h),visible_after<=3)
   self.assertEqual(h.swipe.call_count,min(visible_after,3))
   for call in h.swipe.call_args_list:self.assertEqual(call.args,('up',))
 def harness(self,frames,percent=50):
  tree=ast.parse((ROOT/'tasks/Component/GeneralBuff/general_buff.py').read_text(encoding='utf-8'))
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
  cls.bases=[];cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ['_gold_row_area','_set_gold_buff','gold_50','gold_100']]
  clock=[0.0];ns={'unicodedata':unicodedata,'deepcopy':copy.deepcopy,'logger':Mock(),'time':SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda t:clock.__setitem__(0,clock[0]+t))}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'<gold>','exec'),ns)
  h=ns['GeneralBuff']();h.I_OPEN_YELLOW=copy.deepcopy(Assets.I_OPEN_YELLOW);h.I_CLOSE_RED=copy.deepcopy(Assets.I_CLOSE_RED);h.I_CLOUD='panel'
  h._locate_buff_top=Mock(return_value=True);h.device=SimpleNamespace(image=None,swipe=Mock());h.reject_invite=Mock();h.clicks=[];index=[-1];current=[None]
  keyword=f'战斗胜利获得的金币增加{percent}%'
  buff=SimpleNamespace(keyword=keyword,name='gold',roi=(428,120,338,389))
  def screenshot():
   index[0]=min(index[0]+1,len(frames)-1);current[0]=frames[index[0]]
   text,on,y=current[0];im=np.zeros((720,1280,3),np.uint8)
   rule=h.I_OPEN_YELLOW if on else h.I_CLOSE_RED
   t=rule.image;im[y:y+t.shape[0],778:778+t.shape[1]]=t
   h.device.image=im
  h.screenshot=screenshot;h.appear=lambda r:True if r=='panel' else r.match(h.device.image)
  buff.detect_and_ocr=lambda image:[row(t,current[0][2]) for t in current[0][0]]
  h.click=lambda rule:h.clicks.append(tuple(rule.roi_front))
  h.O_GOLD_50=h.O_GOLD_100=buff
  return h,buff,keyword
 def test_missing_gold_never_clicks_awake_or_experience(self):
  texts=['觉醒副本掉落额外的觉醒材料','八岐大蛇掉落额外的御魂材料','战斗胜利获得的经验增加50%']
  h,b,k=self.harness([(texts,False,143)])
  self.assertFalse(h.gold_50());self.assertEqual(h.clicks,[]);h.device.swipe.assert_not_called()
 def test_both_percentages_open_and_close_with_verification(self):
  for pct in [50,100]:
   for desired in [True,False]:
    k=f'战斗胜利获得的金币增加{pct}%'
    h,b,_=self.harness([([k],not desired,143),([k],desired,143),([k],desired,143)],pct)
    before=h.I_OPEN_YELLOW.roi_back
    self.assertTrue(h._set_gold_buff(b,desired));self.assertEqual(len(h.clicks),1)
    self.assertEqual(h.I_OPEN_YELLOW.roi_back,before)
 def test_already_enabled_does_not_toggle(self):
  k='战斗胜利获得的金币增加50%';h,b,_=self.harness([([k],True,143)])
  self.assertTrue(h.gold_50());self.assertEqual(h.clicks,[])
 def test_missing_target_is_skipped_without_searching_lower_rows(self):
  k='战斗胜利获得的金币增加50%'
  for desired in [True,False]:
   h,b,_=self.harness([([],False,143),([],False,143),([k],False,280)])
   self.assertFalse(h._set_gold_buff(b,desired));h.device.swipe.assert_not_called();self.assertEqual(h.clicks,[])
 def test_missing_anchor_prevents_gold_operations(self):
  h,b,_=self.harness([(['战斗胜利获得的金币增加50%'],False,143)])
  h._locate_buff_top.return_value=False
  self.assertFalse(h.gold_50());self.assertEqual(h.clicks,[])
 def test_target_moves_after_click_and_is_reidentified(self):
  k='战斗胜利获得的金币增加50%';h,b,_=self.harness([([k],False,143),([k],True,280),([k],True,280)])
  self.assertTrue(h.gold_50());self.assertEqual(len(h.clicks),1)
 def test_nonresponsive_switch_stops_after_three_clicks(self):
  k='战斗胜利获得的金币增加50%';h,b,_=self.harness([([k],False,143)])
  self.assertFalse(h.gold_50());self.assertEqual(len(h.clicks),3)
 def test_ambiguous_or_wrong_percentage_never_clicks(self):
  k='战斗胜利获得的金币增加50%'
  for texts in [[k,k],['战斗胜利获得的金币增加100%'],['战斗胜利获得的金币增加150%']]:
   h,b,_=self.harness([(texts,False,143)])
   self.assertFalse(h.gold_50());self.assertEqual(h.clicks,[])
 def test_fullwidth_and_whitespace_are_safe_to_normalize(self):
  h,b,_=self.harness([([' 战斗胜利获得的金币增加５０％。 '],True,143)])
  self.assertTrue(h.gold_50());self.assertEqual(h.clicks,[])
 def test_clipped_row_never_clicked(self):
  k='战斗胜利获得的金币增加50%';h,b,_=self.harness([([k],False,120)])
  self.assertFalse(h.gold_50());self.assertEqual(h.clicks,[])
 def test_closed_panel_never_clicks_or_scrolls(self):
  h,b,_=self.harness([(['战斗胜利获得的金币增加50%'],False,143)])
  h.appear=lambda _:False
  self.assertFalse(h.gold_50());self.assertEqual(h.clicks,[]);h.device.swipe.assert_not_called()
 def test_disappearing_target_after_click_is_not_reported_success(self):
  k='战斗胜利获得的金币增加50%';h,b,_=self.harness([([k],False,143),([],True,143)])
  self.assertFalse(h.gold_50());self.assertEqual(len(h.clicks),1)
 def test_battle_caller_reports_failed_gold_operation(self):
  from dev_tools.test_bondling_battle_entry import extract_methods
  from tasks.Component.GeneralBuff.config_buff import BuffClass
  logger=Mock()
  method=extract_methods(ROOT/'tasks/Component/GeneralBattle/general_battle.py','GeneralBattle',('check_and_open_buff',),
      {'BuffClass':BuffClass,'logger':logger,'time':SimpleNamespace(sleep=lambda _:None)})['check_and_open_buff']
  h=SimpleNamespace(I_BUFF='buff',I_CLOUD='cloud',ui_click=Mock(),screenshot=Mock(),appear=Mock(return_value=False))
  for name in ['awake','soul','gold_50','gold_100','exp_50','exp_100']:setattr(h,name,Mock(return_value=True))
  h.gold_50.return_value=False
  method(h,[BuffClass.GOLD_50,BuffClass.GOLD_100])
  logger.warning.assert_called_once();h.gold_100.assert_called_once_with(True)
if __name__=='__main__':unittest.main()
