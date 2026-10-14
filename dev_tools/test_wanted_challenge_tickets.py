from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods,FakeClock,FakeTimer
ROOT=Path(__file__).resolve().parents[1]
class TicketTests(unittest.TestCase):
    def run_case(self,readings,visible=True):
        clock=FakeClock(1);h=SimpleNamespace(I_WQC_FIRE='fire',device=SimpleNamespace(image=None))
        h.screenshot=Mock(side_effect=clock.advance);h.appear=Mock(return_value=visible)
        h.appear_then_click=Mock(return_value=True)
        ns={'RuleOcr':lambda **kw:SimpleNamespace(ocr=Mock(side_effect=readings)),
            'Timer':lambda t:FakeTimer(clock,t),'logger':Mock(),'GameStuckError':RuntimeError}
        method=extract_methods(ROOT/'tasks/WantedQuests/script_task.py','ScriptTask',('enter_shikigami_challenge',),ns)['enter_shikigami_challenge']
        h.run=lambda:method(h)
        return h
    def test_zero_never_clicks(self):
        h=self.run_case(['0','0']);self.assertFalse(h.run());h.appear_then_click.assert_not_called()
    def test_unknown_never_clicks(self):
        h=self.run_case(['']*30)
        with self.assertRaises(RuntimeError):h.run()
        h.appear_then_click.assert_not_called()
    def test_unresponsive_challenge_is_bounded(self):
        h=self.run_case(['2']*30)
        with self.assertRaises(RuntimeError):h.run()
        self.assertEqual(h.appear_then_click.call_count,3)
    def test_existing_transition_needs_no_click(self):
        h=self.run_case([],False);self.assertTrue(h.run());h.appear_then_click.assert_not_called()
if __name__=='__main__':unittest.main()
