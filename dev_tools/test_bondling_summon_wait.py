import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from dev_tools.test_bondling_battle_entry import SCRIPT_FILE,FakeClock,FakeTimer,extract_methods
class Checks(unittest.TestCase):
 def make(self):
  h=SimpleNamespace();clock=FakeClock();h.clock=clock
  ns={'Timer':lambda n:FakeTimer(clock,n),'sleep':lambda n:setattr(clock,'now',clock.now+n)}
  for k,v in extract_methods(SCRIPT_FILE,'ScriptTask',('wait_summon_map','recheck_existing_ball'),ns).items():setattr(h,k,v.__get__(h))
  h.screenshot=Mock();h.I_STONE_SURE='stone';h.I_UI_CONFIRM='confirm';h.I_STONE_CLOSE='close';h.appear=Mock(return_value=False);h.in_search_ui=Mock(return_value=True);h.ui_click_until_disappear=Mock();h._defer_stone_check=Mock(side_effect=RuntimeError('defer'))
  return h
 def test_waits_for_three_stable_map_frames(self):
  h=self.make();h.in_search_ui.side_effect=[False,True,False,True,True,True];self.assertTrue(h.wait_summon_map());self.assertEqual(h.screenshot.call_count,6)
 def test_unknown_map_defers(self):
  h=self.make();h.in_search_ui.return_value=False
  with self.assertRaises(RuntimeError):h.recheck_existing_ball(1)
 def test_existing_ball_after_delay(self):
  h=self.make();h.ball_click=Mock(side_effect=[False,True]);self.assertTrue(h.recheck_existing_ball(1));self.assertEqual(h.ball_click.call_count,2)
 def test_missing_ball_stops_after_two_checks(self):
  h=self.make();h.ball_click=Mock(return_value=False);self.assertFalse(h.recheck_existing_ball(1));self.assertEqual(h.ball_click.call_count,2)
if __name__=='__main__':unittest.main()
