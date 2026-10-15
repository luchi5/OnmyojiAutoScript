"""Replay a battle taking over records entry or a shared soul-preset loop."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, extract_methods

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/Dokan/script_task.py'
SWITCH = ROOT / 'tasks/Component/SwitchSoul/switch_soul.py'
MARKERS = (
    'I_RYOU_DOKAN_SHIKIGAMI', 'I_SOU_CHECK_IN', 'I_SOUL_PRESET',
    'I_SOU_SWITCH_1', 'I_SOU_SWITCH_2', 'I_SOU_SWITCH_3', 'I_SOU_SWITCH_4',
    'I_SOU_TEAM_PRESENT', 'I_RYOU_DOKAN_IN_FIELD', 'I_PREPARE_HIGHLIGHT',
    'I_BATTLE_INFO', 'I_WIN', 'I_FALSE', 'I_RYOU_DOKAN_TOPPA_RANK', 'I_RYOU_DOKAN_WIN',
)


def harness(frames, *, named=False):
    clock = FakeClock(1)

    class Capture:
        def screenshot(self):
            self.current = frames[min(self.captures, len(frames) - 1)]
            self.captures += 1
            clock.advance()
            if self.captures > 75:
                raise AssertionError('Still waiting in the old soul-switch loop')
            return self.current

    # Compile a real class to preserve screenshot()'s zero-argument super().
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    exception = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == 'DokanSoulSwitchInterrupted')
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
    cls.bases = [ast.Name(id='Capture', ctx=ast.Load())]
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in (
        'screenshot', '_enter_dokan_soul_records', 'switch_soul_in_dokan')]
    ns = {'Capture': Capture, 'Timer': lambda t: FakeTimer(clock, t),
          'GameStuckError': RuntimeError, 'logger': Mock()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[exception, cls], type_ignores=[])),
                 str(SOURCE), 'exec'), ns)
    task = ns['ScriptTask']()
    task.current, task.captures, task.clicks = set(), 0, []
    task.switch_soul_done = False
    for marker in MARKERS:
        setattr(task, marker, marker)
    task.appear = lambda marker: marker in task.current
    task.click = lambda marker, **kw: task.clicks.append(marker)

    def click_visible(marker, **kw):
        if task.appear(marker):
            task.click(marker)
            return True
        return False

    task.appear_then_click = click_visible
    task.ui_click_until_disappear = Mock()
    task.config = SimpleNamespace(dokan=SimpleNamespace(switch_soul_config=SimpleNamespace(
        enable=not named, enable_switch_by_name=named,
        switch_group_team=(1, 2), group_name='group', team_name='team')))
    methods = extract_methods(SWITCH, 'SwitchSoul',
        ('click_preset', 'run_switch_soul', 'run_switch_soul_by_name'), {'logger': Mock()})
    for name, method in methods.items():
        setattr(task, name, method.__get__(task))
    task.switch_souls = Mock()
    task.switch_soul_by_name = Mock()
    return task, clock


def run(task):
    fake = SimpleNamespace(GameUiAssets=SimpleNamespace(I_BACK_Y='back'))
    with patch.dict('sys.modules', {'tasks.GameUi.assets': fake}):
        return task.switch_soul_in_dokan()


class SoulTransitionTests(unittest.TestCase):
    def assert_interrupted(self, task):
        self.assertFalse(run(task))
        self.assertFalse(task.switch_soul_done)
        self.assertFalse(task._dokan_soul_switching)
        task.ui_click_until_disappear.assert_not_called()

    def test_records_click_opens_battle_instead(self):
        task, _ = harness([{'I_RYOU_DOKAN_SHIKIGAMI'}, {'I_RYOU_DOKAN_IN_FIELD'}])
        self.assert_interrupted(task)
        self.assertEqual(task.clicks, ['I_RYOU_DOKAN_SHIKIGAMI'])
        task.switch_souls.assert_not_called()

    def test_source_button_disappearing_is_not_arrival(self):
        task, _ = harness([{'I_RYOU_DOKAN_SHIKIGAMI'}, set(), set(),
                           {'I_SOU_CHECK_IN'}, {'I_SOU_SWITCH_1'}])
        self.assertTrue(run(task))
        self.assertEqual(task.captures, 5)
        task.switch_souls.assert_called_once_with((1, 2))
        self.assertTrue(task.switch_soul_done)

    def test_battle_interrupts_shared_preset_wait_for_both_modes(self):
        for named in (False, True):
            with self.subTest(named=named):
                task, _ = harness([{'I_SOU_CHECK_IN'}, {'I_SOUL_PRESET'},
                                   {'I_PREPARE_HIGHLIGHT', 'I_SOU_SWITCH_1'}], named=named)
                self.assert_interrupted(task)
                self.assertEqual(task.clicks, ['I_SOUL_PRESET'])
                task.switch_souls.assert_not_called()
                task.switch_soul_by_name.assert_not_called()

    def test_battle_during_group_or_team_selection_also_unwinds(self):
        task, _ = harness([{'I_SOU_CHECK_IN'}, {'I_SOU_SWITCH_1'}, {'I_BATTLE_INFO'}])
        task.switch_souls.side_effect = lambda _: task.screenshot()
        self.assert_interrupted(task)

    def test_battle_during_return_does_not_mark_success(self):
        task, _ = harness([{'I_SOU_CHECK_IN'}, {'I_SOU_SWITCH_1'}, {'I_PREPARE_HIGHLIGHT'}])
        task.ui_click_until_disappear.side_effect = lambda *a, **k: task.screenshot()
        self.assertFalse(run(task))
        self.assertFalse(task.switch_soul_done)
        self.assertFalse(task._dokan_soul_switching)

    def test_named_switch_still_completes_in_records(self):
        task, _ = harness([{'I_SOU_CHECK_IN'}, {'I_SOU_SWITCH_1'}], named=True)
        self.assertTrue(run(task))
        task.switch_soul_by_name.assert_called_once_with('group', 'team')
        self.assertTrue(task.switch_soul_done)
        self.assertFalse(task._dokan_soul_switching)

    def test_missing_records_page_has_bounded_wait(self):
        task, _ = harness([set()])
        with self.assertRaisesRegex(RuntimeError, 'records were not confirmed'):
            run(task)
        self.assertLess(task.captures, 20)
        self.assertFalse(task._dokan_soul_switching)
        task.switch_souls.assert_not_called()

    def test_normal_battle_screenshots_are_not_interrupted(self):
        task, _ = harness([{'I_PREPARE_HIGHLIGHT'}])
        self.assertEqual(task.screenshot(), {'I_PREPARE_HIGHLIGHT'})

    def test_interruption_keeps_future_soul_switch_available(self):
        frames = [{'I_PREPARE_HIGHLIGHT'}]
        task, _ = harness(frames)
        self.assert_interrupted(task)
        frames[:] = [{'I_SOU_CHECK_IN', 'I_SOU_SWITCH_1'}]
        self.assertTrue(run(task))
        task.switch_souls.assert_called_once_with((1, 2))

    def test_real_account12_error_crop_is_detected_before_records_clicks(self):
        import cv2
        import numpy as np
        from dev_tools.test_compact_realm_harvest import RuleImage

        # Only the prepare button from the 2026-10-15 error frame is retained.
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        crop = cv2.imread(str(ROOT / 'dev_tools/fixtures/dokan_prepare_12.png'))
        image[500:720, 1100:1280] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        tree = ast.parse((ROOT / 'tasks/Dokan/assets.py').read_text(encoding='utf-8'))
        declaration = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == 'I_RYOU_DOKAN_IN_FIELD'
                                   for t in n.targets))
        ns = {'RuleImage': RuleImage}
        exec(compile(ast.Module(body=[declaration], type_ignores=[]), 'asset', 'exec'), ns)
        rule = ns['I_RYOU_DOKAN_IN_FIELD']
        self.assertTrue(rule.match(image))
        task, _ = harness([set()])
        task.appear = lambda name: name == 'I_RYOU_DOKAN_IN_FIELD' and rule.match(image)
        self.assert_interrupted(task)
        self.assertEqual(task.clicks, [])


if __name__ == '__main__':
    unittest.main()
