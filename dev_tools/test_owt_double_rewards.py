import unittest
from datetime import datetime,timedelta
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import numpy as np
from tasks.OtherWorldTwilight.reward_counter import parse_remaining,read_remaining
from dev_tools.test_bondling_battle_entry import extract_methods,FakeLogger
ROOT=Path(__file__).resolve().parents[1]
class RewardTests(unittest.TestCase):
 def test_parser(self):
  for t,n in [('27/60',27),('０／６０',0),('60 / 60',60),('奖励 3/60',3)]:self.assertEqual(parse_remaining(t),n)
  for t in ['',None,'61/60','27/600','27','680/1000','-1/60']:self.assertIsNone(parse_remaining(t))
 def test_real_screenshot(self):
  self.assertEqual(read_remaining(np.array(Image.open(ROOT/'dev_tools/fixtures/owt_double_27.png'))),27)
 def harness(self,counts):
  values=iter(counts);h=SimpleNamespace(start_time=datetime.now(),battles=0,checks=0)
  h.conf=SimpleNamespace(other_world_twilight_config=SimpleNamespace(limit_count=1,limit_time_v=timedelta(hours=1)),general_battle_config=SimpleNamespace(lock_team_enable=False))
  h.I_OWT_LOCK=h.I_OWT_UNLOCK=h.I_OWT_FIRE='x';h.goto_page=lambda *a:None;h.check_lock=lambda *a:None
  h.screenshot=lambda:None;h.get_current_page=lambda *a:'battle';h.read_double_rewards_stable=lambda:next(values)
  h.appear_then_click=lambda *a,**k:True
  def battle(*a):h.battles+=1
  h.run_general_battle=battle
  h.wait_double_reward_page=lambda:True
  ns={'logger':FakeLogger(),'datetime':datetime,'sleep':lambda *a:None,'page_owt':'owt','page_battle':'battle','page_battle_prepare':'prepare','page_reward':'reward','Timer':lambda *a:SimpleNamespace(start=lambda:SimpleNamespace(reached=lambda:False))}
  m=extract_methods(ROOT/'tasks/OtherWorldTwilight/script_task.py','ScriptTask',('run_double_rewards',),ns)
  h.run=m['run_double_rewards'].__get__(h);return h
 def test_zero_stops_without_battle(self):
  h=self.harness([0]);self.assertTrue(h.run());self.assertEqual(h.battles,0)
 def test_ignores_fixed_count_and_uses_actual_remaining(self):
  h=self.harness([3,2,1,0]);self.assertTrue(h.run());self.assertEqual(h.battles,3)
 def test_unreadable_stops(self):
  h=self.harness([None]);self.assertFalse(h.run());self.assertEqual(h.battles,0)
 def test_no_progress_stops_after_three(self):
  h=self.harness([5,5,5,5]);self.assertFalse(h.run());self.assertEqual(h.battles,3)
 def test_failure_does_not_decrement_counter(self):
  h=self.harness([2,2,1,0]);self.assertTrue(h.run());self.assertEqual(h.battles,3)
 def test_increased_count_defers(self):
  h=self.harness([2,3]);self.assertFalse(h.run());self.assertEqual(h.battles,1)
 def test_time_limit_preserved(self):
  h=self.harness([2]);h.start_time-=timedelta(hours=2);self.assertFalse(h.run());self.assertEqual(h.battles,0)
 def test_stable_reader_requires_two_matching_frames(self):
  ns={'sleep':lambda *a:None,'page_owt':'owt'}
  m=extract_methods(ROOT/'tasks/OtherWorldTwilight/script_task.py','ScriptTask',('read_double_rewards_stable',),ns)
  h=SimpleNamespace(screenshot=lambda:None,get_current_page=lambda *a:'owt',device=SimpleNamespace(image=None))
  with patch('tasks.OtherWorldTwilight.reward_counter.read_remaining',side_effect=[None,0,1,1]) as reader:
   self.assertEqual(m['read_double_rewards_stable'](h),1)
   self.assertEqual(reader.call_count,4)
 def test_reader_does_not_accept_other_pages(self):
  ns={'sleep':lambda *a:None,'page_owt':'owt'}
  m=extract_methods(ROOT/'tasks/OtherWorldTwilight/script_task.py','ScriptTask',('read_double_rewards_stable',),ns)
  h=SimpleNamespace(screenshot=lambda:None,get_current_page=lambda *a:'reward',device=SimpleNamespace(image=None))
  with patch('tasks.OtherWorldTwilight.reward_counter.read_remaining') as reader:
   self.assertIsNone(m['read_double_rewards_stable'](h));reader.assert_not_called()
 def settlement(self, frames, fallback='owt'):
  h=SimpleNamespace(nav=[],clicks=[],page=None)
  seq=iter(frames)
  h.screenshot=lambda:setattr(h,'page',next(seq,h.page))
  h.get_current_page=lambda *a:h.page
  h.goto_page=lambda page:(h.nav.append(page),setattr(h,'page',fallback))
  h.I_REWARD='reward';h.I_REWARD_GOLD='gold';h.C_REWARD_1='safe'
  h.appear_then_click=lambda rule,**kw:h.clicks.append(rule)
  ns={'logger':FakeLogger(),'sleep':lambda *a:None,'page_owt':'owt','page_battle':'battle','page_battle_prepare':'prepare','page_reward':'reward'}
  m=extract_methods(ROOT/'tasks/OtherWorldTwilight/script_task.py','ScriptTask',('wait_double_reward_page',),ns)
  return m['wait_double_reward_page'](h),h
 def test_normal_settlement_never_navigates_back(self):
  ok,h=self.settlement(['reward',None,None,'owt'])
  self.assertTrue(ok);self.assertEqual(h.nav,[])
 def test_visible_battle_not_abandoned(self):
  ok,h=self.settlement(['battle'])
  self.assertFalse(ok);self.assertEqual(h.nav,[])
 def test_unknown_settlement_has_single_fallback(self):
  ok,h=self.settlement([None])
  self.assertTrue(ok);self.assertEqual(h.nav,['owt'])
 def test_failed_fallback_defers(self):
  ok,h=self.settlement([None],fallback=None)
  self.assertFalse(ok);self.assertEqual(len(h.nav),1)
if __name__=='__main__':unittest.main()
