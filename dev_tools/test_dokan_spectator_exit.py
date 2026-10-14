from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods
ROOT = Path(__file__).resolve().parents[1]

def run(frames):
    clock = FakeClock(1)
    h = SimpleNamespace(clicks=[], index=0, frame=set())
    for name in ('I_RYOU_DOKAN_TOPPA_RANK','I_RYOU_DOKAN_WIN','I_FALSE','I_RYOU_DOKAN_CENTER_TOP','I_EXIT_ENSURE','I_EXIT'):
        setattr(h,name,name)
    def screenshot():
        h.frame = set(frames[min(h.index,len(frames)-1)])
        h.index += 1
        clock.advance()
    def click(marker, **kwargs):
        if marker in h.frame:
            h.clicks.append(marker)
            return True
        return False
    h.screenshot = screenshot
    h.appear = lambda marker: marker in h.frame
    h.appear_then_click = click
    h.is_in_battle = lambda _: 'battle' in h.frame
    method = extract_methods(ROOT/'tasks/Dokan/script_task.py','ScriptTask',('exit_cheering',),
        {'Timer':lambda t:FakeTimer(clock,t),'GameStuckError':RuntimeError})['exit_cheering']
    method(h)
    return h

class ExitTests(unittest.TestCase):
    def test_victory_continue_preempts_exit(self):
        def settle(task):
            if 'continue' in task.frame:
                task.clicks.append('settlement_continue')
                return True
            return False
        module = SimpleNamespace(handle_settlement=settle)
        with patch.dict('sys.modules', {'tasks.Dokan.settlement_recovery':module}):
            h = run([['continue','battle','I_EXIT']])
        self.assertEqual(h.clicks,['settlement_continue'])

    def test_rank_arrives_during_exit(self):
        h = run([['battle','I_EXIT'],['I_RYOU_DOKAN_TOPPA_RANK','I_EXIT']])
        self.assertEqual(h.clicks,['I_EXIT'])
    def test_already_settled_never_clicks_exit(self):
        self.assertEqual(run([['I_RYOU_DOKAN_WIN','I_EXIT']]).clicks,[])
    def test_confirm_then_gathering(self):
        h = run([['battle','I_EXIT'],['I_EXIT_ENSURE'],['I_RYOU_DOKAN_CENTER_TOP']])
        self.assertEqual(h.clicks,['I_EXIT','I_EXIT_ENSURE'])
    def test_unknown_screen_is_bounded(self):
        with self.assertRaises(RuntimeError):
            run([[]])
