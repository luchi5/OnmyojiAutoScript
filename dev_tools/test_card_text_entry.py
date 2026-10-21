import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
import cv2
from dev_tools.test_compact_realm_harvest import ROOT, RuleImage
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer


class EntryTests(unittest.TestCase):
    def run_entry(self, account, opens=True):
        image = cv2.cvtColor(cv2.imread(str(ROOT/f'dev_tools/fixtures/realm{account}_collected.png')),cv2.COLOR_BGR2RGB)
        clock = FakeClock(1)
        elapsed = [0]
        clicked = []
        def sleep(s):
            elapsed[0] += s
            clock.advance()
        def appear(rule):
            if isinstance(rule,str): return opens and bool(clicked)
            return rule.match(image)
        task=NS(I_A_CHECK_CARD='panel',I_A_AUTO_INVITE='invite',I_SHI_CARD='legacy',
                screenshot=lambda:None,appear=appear,appear_multi_scale=lambda _:False,
                click=lambda r:clicked.append(tuple(r.roi_front)))
        fn=extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py','ScriptTask',('goto_cards',),
            dict(RuleImage=RuleImage, Timer=lambda t:FakeTimer(clock,t),
                 time=NS(monotonic=lambda:elapsed[0],sleep=sleep),logger=Mock(),GameStuckError=RuntimeError))['goto_cards']
        self.clicked=clicked
        fn(task)
        return clicked

    def test_09_text_then_panel_confirmation(self):
        self.assertEqual(self.run_entry('09'),[(768,500,11,31)])

    def test_15_text_then_panel_confirmation(self):
        self.assertEqual(self.run_entry('15'),[(831,424,11,32)])

    def test_click_without_panel_is_failure_and_bounded(self):
        with self.assertRaises(RuntimeError): self.run_entry('09',False)
        self.assertLessEqual(len(self.clicked),3)

if __name__ == '__main__': unittest.main()
