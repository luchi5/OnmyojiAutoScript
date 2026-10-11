"""Actual matcher/refresh methods on synthetic frames and published UI crops."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LOG = NS(**{name: lambda *a, **kw: None for name in ('info', 'debug', 'warning', 'error', 'attr')})

def definition(path, name):
    return next(n for n in ast.parse(path.read_bytes()).body if isinstance(n, ast.ClassDef) and n.name == name)

rule = definition(ROOT / 'module/atom/image.py', 'RuleImage')
rule.bases = []
rule.body = [n for n in rule.body if isinstance(n, ast.FunctionDef) and n.name in ('__init__', 'corp', 'match')]
env = {'cv2': cv2, 'np': np, 'logger': LOG}
exec(compile(ast.fix_missing_locations(ast.Module(body=[rule], type_ignores=[])), '<actual RuleImage methods>', 'exec'), env)
ActualRule = env['RuleImage']
def image(rule):
    if rule._image is None:
        path = Path(rule.file)
        rule._image = cv2.imread(str(path if path.is_absolute() else ROOT / path))
        assert rule._image is not None, path
    return rule._image
ActualRule.image = property(image)
ActualRule.name = property(lambda rule: Path(rule.file).stem.upper())
ActualRule.is_template_match = True
ActualRule.debug_mode = False

button = definition(ROOT / 'tasks/GlobalGame/dialog_buttons.py', 'DialogButtonRule')
button_env = {'RuleImage': ActualRule, 'Path': Path, '__file__': str(ROOT / 'tasks/GlobalGame/dialog_buttons.py')}
exec(compile(ast.fix_missing_locations(ast.Module(body=[button], type_ignores=[])), '<actual DialogButtonRule>', 'exec'), button_env)
DialogButtonRule = button_env['DialogButtonRule']

def asset_rules():
    node = definition(ROOT / 'tasks/RealmRaid/assets.py', 'RealmRaidAssets')
    names = ('I_FRESH', 'I_FRESH_ENSURE')
    assignments = [copy.deepcopy(n) for n in node.body if isinstance(n, ast.Assign)
                   and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names]
    scope = {'RuleImage': ActualRule}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), '<actual RealmRaid assets>', 'exec'), scope)
    return {name: scope[name] for name in names}

def task_type():
    base = type('FakeAssets', (), asset_rules())
    task = definition(ROOT / 'tasks/RealmRaid/script_task.py', 'ScriptTask')
    task.bases = [ast.Name(id='FakeAssets', ctx=ast.Load())]
    task.body = [n for n in task.body if (isinstance(n, ast.FunctionDef) and n.name == 'check_refresh')
                 or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'I_FRESH_ENSURE')]
    scope = {'FakeAssets': base, 'DialogButtonRule': DialogButtonRule, 'logger': LOG}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[task], type_ignores=[])), '<actual ScriptTask refresh>', 'exec'), scope)
    return scope['ScriptTask']

def fixture(number):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    refresh = asset_rules()['I_FRESH']
    bx, by, _, _ = refresh.roi_front
    background = refresh.image
    frame[by:by+background.shape[0], bx:bx+background.shape[1]] = background
    pixels = cv2.imread(str(ROOT / 'tasks/GlobalGame/ui/ui_ui_confirm_v2.png'))
    x, y = ((699, 418), (700, 419), (698, 417), (701, 420))[number-1]
    frame[y:y+pixels.shape[0], x:x+pixels.shape[1]] = pixels
    return frame

def ready_frame(number=1):
    frame = fixture(number)
    frame[210:500, 400:880] = 0
    return frame

def legacy_frame():
    frame = ready_frame()
    rule = asset_rules()['I_FRESH_ENSURE']
    x, y, _, _ = rule.roi_front; mat = rule.image
    frame[y:y+mat.shape[0], x:x+mat.shape[1]] = mat
    return frame

class TooManyClickError(RuntimeError): pass

class Harness:
    def __init__(self, popup=None, *, cooldown=False, initial_popup=False, linger=0):
        self.ready = ready_frame()
        if cooldown: self.ready[564:630, 957:1139] = 0
        self.popup = popup if popup is not None else fixture(1)
        self.state = 'popup' if initial_popup else 'ready'
        self.frame = self.popup if initial_popup else self.ready
        self.clicks = []; self.pending_clear = False; self.linger = linger
        self.cycles = 0
        self.task = task_type()()
        self.task.screenshot = self.screenshot
        self.task.appear = lambda rule: bool(rule.match(self.frame))
        self.task.appear_then_click = self.appear_then_click

    def screenshot(self):
        self.cycles += 1
        if self.cycles > 40: raise RuntimeError('Offline finite observation budget exceeded')
        if self.pending_clear:
            if self.linger: self.linger -= 1
            else: self.state = 'ready'
        self.frame = self.popup if self.state == 'popup' else self.ready

    def appear_then_click(self, rule, **kwargs):
        if not rule.match(self.frame): return False
        is_confirm = rule is self.task.I_FRESH_ENSURE
        label = 'confirm' if is_confirm else 'refresh'
        self.clicks.append((label, tuple(rule.roi_front)))
        if is_confirm:
            self.pending_clear = True
        else:
            self.state = 'popup'
            if sum(label == 'refresh' for label, _ in self.clicks) >= 10:
                raise TooManyClickError('Same RES_FRESH repeated behind confirmation dialog')
        return True

class RefreshTests(unittest.TestCase):
    def test_four_new_button_positions_are_confirmed_without_reclicking_background(self):
        for number in range(1, 5):
            with self.subTest(incident=number):
                h = Harness(fixture(number))
                self.assertTrue(h.task.check_refresh())
                self.assertEqual([name for name, _ in h.clicks], ['refresh', 'confirm'])
                x, y, w, height = h.clicks[-1][1]
                self.assertGreaterEqual(x, 665); self.assertLessEqual(x+w, 845)
                self.assertGreaterEqual(y, 407); self.assertLessEqual(y+height, 473)
                self.assertEqual(h.state, 'ready')

    def test_legacy_confirmation_still_refreshes(self):
        h = Harness(legacy_frame())
        self.assertTrue(h.task.check_refresh())
        self.assertEqual([name for name, _ in h.clicks], ['refresh', 'confirm'])
        self.assertEqual(h.task.I_FRESH_ENSURE.name, 'RES_FRESH_ENSURE')

    def test_refresh_cooldown_returns_false_and_does_not_click(self):
        h = Harness(cooldown=True)
        self.assertFalse(h.task.check_refresh())
        self.assertEqual(h.clicks, [])

    def test_existing_new_popup_is_confirmed_without_clicking_refresh(self):
        h = Harness(initial_popup=True)
        self.assertTrue(h.task.check_refresh(screenshot=False))
        self.assertEqual([name for name, _ in h.clicks], ['confirm'])

    def test_confirmation_is_not_success_until_popup_disappears(self):
        h = Harness(linger=2)
        self.assertTrue(h.task.check_refresh())
        self.assertEqual(h.state, 'ready')
        self.assertGreaterEqual(h.cycles, 5)
        self.assertEqual(sum(name == 'refresh' for name, _ in h.clicks), 1)

    def test_old_new_old_cache_transition_recognizes_each_correct_button(self):
        rule = task_type()().I_FRESH_ENSURE
        self.assertTrue(rule.match(legacy_frame()))
        self.assertTrue(rule.match(fixture(1)))
        self.assertEqual(rule.roi_front[2:], [112, 42])
        self.assertTrue(rule.match(legacy_frame()))
        self.assertEqual(rule.roi_front[2:], list(rule.image.shape[:2][::-1]))

    def test_blank_cancel_and_displaced_confirm_are_negative(self):
        rule = task_type()().I_FRESH_ENSURE
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(rule.match(blank))
        cancel = cv2.imread(str(ROOT / 'tasks/GlobalGame/ui/ui_ui_cancel_v2.png'))
        negative = blank.copy()
        negative[418:418+cancel.shape[0], 699:699+cancel.shape[1]] = cancel
        self.assertFalse(rule.match(negative))
        confirm = cv2.imread(str(ROOT / 'tasks/GlobalGame/ui/ui_ui_confirm_v2.png'))
        displaced = blank.copy()
        displaced[100:100+confirm.shape[0], 100:100+confirm.shape[1]] = confirm
        self.assertFalse(rule.match(displaced))

    def test_low_similarity_does_not_become_confirmation(self):
        rule = task_type()().I_FRESH_ENSURE
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        noise = np.random.default_rng(42).integers(0, 256, (78, 150, 3), dtype=np.uint8)
        frame[400:478, 682:832] = noise
        self.assertFalse(rule.match(frame))

    def test_new_button_keeps_stricter_requested_threshold(self):
        rule = task_type()().I_FRESH_ENSURE
        self.assertTrue(rule.match(fixture(1), threshold=0.99))
        self.assertFalse(rule.match(fixture(1), threshold=1.01))

    def test_unrecognized_dialog_preserves_failure_instead_of_false_success(self):
        unknown = ready_frame()
        h = Harness(unknown)
        with self.assertRaises(TooManyClickError): h.task.check_refresh()
        self.assertFalse(any(label == 'confirm' for label, _ in h.clicks))

if __name__ == '__main__': unittest.main(verbosity=2)
