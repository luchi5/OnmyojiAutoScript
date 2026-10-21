"""Slow-response and wrong-page regressions; no game input."""
from pathlib import Path
from types import SimpleNamespace as S
from unittest.mock import Mock
from datetime import datetime
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods

ROOT=Path(__file__).resolve().parents[1]


class Harness:
    def __init__(self):
        self.now=0;self.clicks=[];self.frame=0
        self.clock=S(monotonic=lambda:self.now,sleep=self.sleep)
        self.device=S(image=None,detect_record=set(),stuck_record_add=lambda x:self.device.detect_record.add(x))
        self.screenshot=lambda:None
        self.I_DE_FIND='find';self.I_SE_ENTER='enter';self.I_SE_FIRE='fire';self.I_SE_PLACEMENT='rank'
        self.goto_page=Mock()
    def sleep(self,n):self.now+=n
    def appear_then_click(self,marker,**kw):self.clicks.append((marker,self.now));return True
    def bind(self,path,name):
        return extract_methods(ROOT/path,'ScriptTask',(name,),{'time':self.clock,'logger':Mock(),
            'page_secret_zones':'weekly-list','WantedQuestsAssets':S(I_WQSE_FIRE='normal_fire')})[name].__get__(self)


class LagTests(unittest.TestCase):
    def lantern(self, counter):
        h=Harness();h.O_DE_COUNTER=S(ocr=lambda _:counter(h))
        return h,h.bind('tasks/DemonEncounter/script_task.py','wait_lantern_discovery')
    def test_slow_click_is_not_repeated_before_sixty_seconds(self):
        h,run=self.lantern(lambda h:(4,0,4) if h.now<45 else (0,4,4))
        self.assertTrue(run());self.assertEqual(len(h.clicks),1);self.assertGreaterEqual(h.now,46)
        self.assertNotIn('PAUSE',h.device.detect_record)
    def test_no_progress_stops_after_three_spaced_clicks(self):
        h,run=self.lantern(lambda h:(4,0,4));self.assertFalse(run())
        self.assertEqual([t for _,t in h.clicks],[0,60,120]);self.assertGreaterEqual(h.now,180)
    def test_ocr_fourteen_is_rejected_without_clicking(self):
        h,run=self.lantern(lambda h:(14,-10,4));self.assertFalse(run());self.assertFalse(h.clicks)
    def test_progress_allows_next_click_and_needs_stable_zero(self):
        h,run=self.lantern(lambda h:((4,0,4) if h.now<3 else (3,1,4) if h.now<10 else (0,4,4)))
        self.assertTrue(run());self.assertEqual(len(h.clicks),2)
    def test_single_bad_zero_never_claims_completion(self):
        h,run=self.lantern(lambda h:(0,4,4) if h.now==1 else (4,0,4))
        self.assertFalse(run())
    def test_secret_normal_challenge_button_is_not_weekly(self):
        h=Harness();h.appear=lambda marker:marker=='fire'
        self.assertIs(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')(),False)
        self.assertFalse(h.clicks);h.goto_page.assert_not_called()
    def test_secret_never_loaded_is_not_classified_as_locked(self):
        h=Harness();h.appear=lambda marker:False
        self.assertIsNone(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')())
        self.assertEqual(len(h.clicks),2)
    def test_secret_ranking_after_animation_is_accepted(self):
        h=Harness();h.appear=lambda marker:marker=='fire' or (marker=='rank' and h.now>=1)
        self.assertIs(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')(),True)
    def test_secret_locked_skips_with_normal_schedule(self):
        h=Harness();h.check_time=Mock();h.enter_weekly_secret=lambda:False
        h.config=S(config_name='09-test',secret=S(scheduler=S(next_run='2026-10-26 18:00:00'),secret_config=S(),switch_soul=S(enable=False,enable_switch_by_name=False)))
        h.set_next_run=Mock()
        writer=Mock()
        class End(Exception):pass
        run=extract_methods(ROOT/'tasks/Secret/script_task.py','ScriptTask',('run',),
            {'logger':Mock(),'page_secret_zones':'weekly-list','page_main':'main','TaskEnd':End,
             'Path':Path,'datetime':datetime,'write_file':writer})['run'].__get__(h)
        with self.assertRaises(End):run()
        h.set_next_run.assert_called_once_with(task='Secret',success=False,finish=True)
        self.assertEqual(writer.call_args.args[1]['outcome'],'failed')
        self.assertEqual(writer.call_args.args[1]['reason'],'not_unlocked')
        self.assertEqual(h.goto_page.call_args.args,('main',))
    def test_secret_normal_variant_is_locked_without_ranking(self):
        h=Harness();h.appear=lambda marker:marker=='normal_fire'
        self.assertIs(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')(),False)
        self.assertFalse(h.clicks)
    def test_secret_late_weekly_page_is_accepted(self):
        h=Harness();h.appear=lambda marker:h.now>=22 and marker in ('rank','fire')
        self.assertTrue(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')())
        self.assertEqual(len(h.clicks),1)
    def test_secret_already_entered_never_clicks_entry(self):
        h=Harness();h.appear=lambda marker:marker in ('rank','fire')
        self.assertTrue(h.bind('tasks/Secret/script_task.py','enter_weekly_secret')())
        self.assertFalse(h.clicks)


if __name__=='__main__':unittest.main()
