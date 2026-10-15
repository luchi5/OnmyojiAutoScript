"""Dokan cheering must recover dialogs and confirm battle before proceeding."""
from pathlib import Path
from types import SimpleNamespace
import re
import unittest
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

SOURCE = Path(__file__).resolve().parents[1]/'tasks/Dokan/script_task.py'


class Harness:
    def __init__(self, frames, points=(0, 0), warning='退出后将无法领取本次道馆突破奖励'):
        self.clock = FakeClock(1)
        self.frames = frames
        self.current = set()
        self.captures = self.scans = self.exits = 0
        self.clicks = []
        self.device = SimpleNamespace(image=None, click=lambda *p: self.clicks.append(p),
                                      stuck_record_add=lambda *a: None,
                                      stuck_record_clear=lambda: None,
                                      click_record_clear=lambda: None)
        for name in ('I_RYOU_DOKAN_EXIT_ENSURE', 'I_RYOU_DOKAN_CD',
                     'I_RYOU_DOKAN_TOPPA_RANK', 'I_RYOU_DOKAN_WIN', 'I_FALSE',
                     'I_RYOU_DOKAN_CHEERING_SCORES', 'I_RYOU_DOKAN_CHEERING_GRAY',
                     'I_RYOU_DOKAN_CHEERING'):
            setattr(self, name, name)
        self.O_DOKEN_FAIL_CD = SimpleNamespace(detect_text=lambda a: '114')
        def scan(*a, **kw):
            self.scans += 1
            return points
        self.L_GOTO_CHEERING = SimpleNamespace(roi_back=(950,172,54,324), ocr_appear=scan)
        ns = dict(Timer=lambda t: FakeTimer(self.clock,t), re=re, logger=FakeLogger(),
                  RuleOcr=lambda **kw: SimpleNamespace(detect_text=lambda a: warning),
                  RuleClick=lambda **kw: kw['name'],
                  sleep=lambda t: setattr(self.clock,'now',self.clock.now+t),
                  random=SimpleNamespace(uniform=lambda a,b:1), random_click=lambda: 'result_close')
        methods = extract_methods(SOURCE, 'ScriptTask', ('start_cheering','_cancel_cheering_exit'), ns)
        for name, method in methods.items():
            setattr(self,name,method.__get__(self))

    def screenshot(self):
        self.current = self.frames[min(self.captures,len(self.frames)-1)]
        self.captures += 1
        self.clock.advance()
        assert self.captures < 150

    def appear(self, name, **kw):
        return name in self.current

    def click(self, name, **kw):
        self.clicks.append(name)

    def appear_then_click(self,name,**kw):
        if self.appear(name):
            self.click(name)
            return True
        return False

    def is_in_battle(self, screenshot=False):
        return 'battle' in self.current

    def exit_battle(self):
        self.exits += 1


class CheeringTests(unittest.TestCase):
    def test_cancel_before_scanning_then_enter_real_battle(self):
        h = Harness([{'I_RYOU_DOKAN_EXIT_ENSURE'}, set(), {'battle'}, {'I_RYOU_DOKAN_WIN'}], (975,200))
        h.start_cheering()
        self.assertEqual(h.clicks[:2], ['dokan_leave_cancel',(975,200)])
        self.assertEqual(h.scans,1)
        self.assertEqual(h.exits,0)

    def test_empty_ocr_never_clicks_origin_or_exits_battle(self):
        h = Harness([set()])
        h.start_cheering()
        self.assertEqual(h.clicks,[])
        self.assertEqual(h.exits,0)
        self.assertLess(h.captures,25)

    def test_unrecognized_dialog_does_not_scan_or_confirm(self):
        h = Harness([{'I_RYOU_DOKAN_EXIT_ENSURE'}],warning='其他确认提示')
        h.start_cheering()
        self.assertEqual(h.scans,0)
        self.assertEqual(h.clicks,[])

    def test_click_without_battle_transition_is_not_success(self):
        h = Harness([set()],(975,200))
        h.start_cheering()
        self.assertEqual(h.exits,0)
        self.assertNotIn('result_close',h.clicks)

    def test_missing_list_does_not_toggle_report(self):
        h = Harness([{'I_RYOU_DOKAN_CHEERING_SCORES'}], points=2)
        h.start_cheering()
        self.assertEqual(h.clicks, [])

    def test_unopened_spectator_list_clicks_once_and_skips_same_cooldown(self):
        h = Harness([{'I_RYOU_DOKAN_CD', 'I_RYOU_DOKAN_CHEERING_SCORES'}])
        h.start_cheering()
        self.assertEqual(h.clicks, ['I_RYOU_DOKAN_CD'])
        self.assertEqual(h.scans, 0)
        captures = h.captures
        for _ in range(20):
            h.start_cheering()
        self.assertEqual(h.captures, captures)
        h.clock.now += 120
        h.start_cheering()
        self.assertEqual(h.clicks, ['I_RYOU_DOKAN_CD'] * 2)

    def test_visible_target_without_transition_has_bounded_clicks(self):
        h = Harness([set()], (975, 200))
        h.start_cheering()
        self.assertEqual(h.clicks, [(975, 200)] * 2)
        h.start_cheering()
        self.assertEqual(len(h.clicks), 2)

    def test_ranking_returns_to_outer_task_without_scanning(self):
        h = Harness([{'I_RYOU_DOKAN_TOPPA_RANK'}])
        h.start_cheering()
        self.assertEqual(h.scans,0)


if __name__ == '__main__':
    unittest.main()
