"""Offline checks for guild overlays and screenshot blocking."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer

ROOT = Path(__file__).resolve().parents[1]

def load_popup():
    source = ast.parse((ROOT / 'tasks/GlobalGame/autumn_popup.py').read_text(encoding='utf-8'))
    source.body = [n for n in source.body if not isinstance(n, ast.ImportFrom)]
    ns = {'RuleImage': lambda **kw: SimpleNamespace(**kw)}
    exec(compile(source, 'autumn_popup.py', 'exec'), ns)
    return SimpleNamespace(**ns)

class PopupTests(unittest.TestCase):
    def test_requires_both_title_and_close(self):
        module = load_popup()
        for title, close in module.POPUPS:
            for visible, expected in (([title],False),([close],False),([title,close],True),([],False)):
                task = SimpleNamespace(appear=lambda r: any(r is x for x in visible), click=Mock())
                self.assertEqual(module.close_autumn_popup(task), expected)
                if expected:
                    task.click.assert_called_once_with(close, interval=1.5)
                else:
                    task.click.assert_not_called()

    def harness(self, frames):
        clock = FakeClock(1)
        module = SimpleNamespace(close_autumn_popup=Mock(side_effect=frames))
        method = extract_methods(ROOT/'tasks/base_task.py', 'BaseTask', ('screenshot',),
            {'Timer':lambda t:FakeTimer(clock,t), 'sleep':lambda _:clock.advance(),
             'GameStuckError':RuntimeError})['screenshot']
        task = SimpleNamespace(device=SimpleNamespace(screenshot=Mock(), image=None), _burst=Mock())
        return module, method, task

    def test_waits_for_overlay_to_disappear(self):
        module, method, task = self.harness([True,True,False])
        with patch.dict(sys.modules, {'tasks.GlobalGame.autumn_popup':module}):
            method(task)
        self.assertEqual(task.device.screenshot.call_count,3)

    def test_ordinary_frame_returns_immediately(self):
        module, method, task = self.harness([False])
        with patch.dict(sys.modules, {'tasks.GlobalGame.autumn_popup':module}):
            method(task)
        self.assertEqual(task.device.screenshot.call_count,1)

    def test_persistent_overlay_has_bounded_wait(self):
        module, method, task = self.harness([True]*30)
        with patch.dict(sys.modules, {'tasks.GlobalGame.autumn_popup':module}):
            with self.assertRaises(RuntimeError):
                method(task)
        self.assertLess(task.device.screenshot.call_count,15)
