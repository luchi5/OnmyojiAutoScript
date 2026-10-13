"""Run actual DemonEncounter wait/guard methods without a game or account.

The real device timeout and UI close loops are compiled from source. Synthetic
frames and a fake clock exercise 300-second waits and settlement transitions.
Private screenshots are replayed separately, outside the source tree.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tasks/DemonEncounter/script_task.py'


class GameStuckError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class AccountLoggedInElsewhere(Exception):
    pass


class ClockTimer:
    def __init__(self, clock, limit):
        self.clock, self.limit = clock, limit
        self.started = clock.now
        self.resets = 0

    def reset(self):
        self.started = self.clock.now
        self.resets += 1
        return self

    def start(self):
        return self.reset()

    def reached(self):
        return self.clock.now - self.started >= self.limit


def frame(*visible, **extra):
    return {'visible': set(visible), **extra}


class CaptureBase:
    def screenshot(self):
        self.capture_count += 1
        if self.capture_count > 50:
            raise AssertionError('Battle wait did not finish')
        self.clock.now += 0.05
        current = self.frames[min(self.capture_count - 1, len(self.frames) - 1)]
        self.device.image = current
        if current is not None and 'error' in current:
            raise current['error']
        self.device.stuck_record_check()
        return current

    def exist_image(self):
        return self.device.image is not None

    def is_in_real_battle(self, is_screenshot=True):
        if is_screenshot:
            self.screenshot()
        return self.appear('I_BATTLE_INFO')

    def appear(self, marker, **kwargs):
        self.appearance_checks.append(marker)
        return marker in self.device.image['visible']

    def appear_then_click(self, marker, **kwargs):
        if not self.appear(marker):
            return False
        self.clicks.append(marker)
        self.device.stuck_record_clear()
        return True


def harness(frames):
    clock = SimpleNamespace(now=0)
    logger = FakeLogger()
    namespace = {
        'CaptureBase': CaptureBase, 'GameStuckError': GameStuckError,
        'GameNotRunningError': GameNotRunningError, 'logger': logger,
        'Timer': lambda limit: ClockTimer(clock, limit),
        'RuleOcr': lambda **kwargs: 'O_' + kwargs['name'].upper(),
    }
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8-sig'))
    source = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                  and n.name == 'ScriptTask')
    body = [n for n in source.body if (
        isinstance(n, ast.FunctionDef) and n.name in ('screenshot', 'battle_wait', '_finish_failed_settlement')
    ) or (isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id.startswith('O_DE_') for t in n.targets
    ))]
    # Keep a real class body so zero-argument super() retains its class cell.
    production = ast.ClassDef(name='ProductionTask', bases=[ast.Name(id='CaptureBase', ctx=ast.Load())],
                              keywords=[], body=body, decorator_list=[])
    module = ast.Module(body=[production], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SCRIPT), 'exec'), namespace)
    task = namespace['ProductionTask']()
    task.clock, task.frames, task.capture_count = clock, frames, 0
    task.clicks, task.appearance_checks = [], []
    task.device = SimpleNamespace(
        image=frame(), detect_record={'BATTLE_STATUS_S'},
        stuck_timer=ClockTimer(clock, 60), stuck_timer_long=ClockTimer(clock, 300),
        stuck_long_wait_list=['BATTLE_STATUS_S'], app_is_running=lambda: True,
        click_record_clear=lambda: None,
    )
    methods = extract_methods(ROOT / 'module/device/device.py', 'Device',
                              ('stuck_record_check', 'stuck_record_clear', 'stuck_record_add'),
                              namespace)
    for name, method in methods.items():
        setattr(task.device, name, method.__get__(task.device))
    ui = extract_methods(ROOT / 'tasks/base_task.py', 'BaseTask',
                         ('ui_click_until_disappear',), namespace)
    task.ui_click_until_disappear = ui['ui_click_until_disappear'].__get__(task)
    for name in ('I_DE_WIN', 'I_WIN', 'I_REWARD', 'I_FALSE', 'I_UI_CONFIRM_SAMLL', 'I_DE_LOCATION'):
        setattr(task, name, name)
    return task


class DemonBattleWaitTests(unittest.TestCase):
    def test_old_device_guard_rejects_an_active_battle_after_five_minutes(self):
        h = harness([frame('I_BATTLE_INFO')])
        h.clock.now = 301
        with self.assertRaises(GameStuckError):
            CaptureBase.screenshot(h)

    def test_xy_guard_recovers_an_active_battle_after_five_minutes(self):
        h = harness([frame('I_BATTLE_INFO')])
        h.clock.now = 301
        self.assertIs(h.screenshot(), h.frames[0])
        self.assertEqual(h.capture_count, 2)
        self.assertEqual(h.device.detect_record, {'BATTLE_STATUS_S'})
        self.assertFalse(h.device.stuck_timer_long.reached())

    def test_another_five_minutes_can_be_recovered_while_battle_stays_active(self):
        h = harness([frame('I_BATTLE_INFO')])
        for _ in range(2):
            h.clock.now += 301
            h.screenshot()
        self.assertEqual(h.capture_count, 4)

    def test_normal_capture_does_not_reset_the_wait_timer(self):
        h = harness([frame('I_BATTLE_INFO')])
        h.screenshot()
        self.assertEqual(h.capture_count, 1)
        self.assertEqual(h.device.stuck_timer_long.resets, 0)

    def test_fresh_nonbattle_frame_keeps_original_timeout(self):
        for current in (frame(), frame('I_UI_CONFIRM_SAMLL', 'O_DE_BOSS_ESCAPED'),
                        frame('I_PREPARE_HIGHLIGHT')):
            with self.subTest(current=current):
                h = harness([current])
                h.device.image = frame('I_BATTLE_INFO')  # Previous frame was a battle.
                h.clock.now = 301
                with self.assertRaises(GameStuckError):
                    h.screenshot()
                self.assertEqual(h.capture_count, 1)
                self.assertEqual(h.device.detect_record, set())

    def test_no_screenshot_keeps_original_timeout(self):
        h = harness([None])
        h.clock.now = 301
        with self.assertRaises(GameStuckError):
            h.screenshot()
        self.assertEqual(h.capture_count, 1)

    def test_app_stopped_and_other_device_login_errors_are_not_swallowed(self):
        for error in (GameNotRunningError('stopped'), AccountLoggedInElsewhere('login')):
            with self.subTest(error=error):
                h = harness([frame('I_BATTLE_INFO', error=error)])
                with self.assertRaises(type(error)) as caught:
                    h.screenshot()
                self.assertIs(caught.exception, error)
                self.assertEqual(h.capture_count, 1)

    def test_escape_dialog_is_confirmed_and_settlement_closed_before_returning_failure(self):
        dialog = frame('I_UI_CONFIRM_SAMLL', 'O_DE_BOSS_ESCAPED', 'O_DE_SETTLEMENT_CONTINUE')
        settlement = frame('O_DE_SETTLEMENT_CONTINUE')
        h = harness([dialog, dialog, settlement, settlement, frame('I_DE_LOCATION')])
        self.assertFalse(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_UI_CONFIRM_SAMLL', 'O_DE_SETTLEMENT_CONTINUE'])
        self.assertIn('I_DE_LOCATION', h.device.image['visible'])

    def test_unrelated_confirmation_is_not_clicked(self):
        h = harness([frame('I_UI_CONFIRM_SAMLL'), frame('I_FALSE'),
                     frame('I_FALSE'), frame(), frame('I_DE_LOCATION')])
        self.assertFalse(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_FALSE'])

    def test_loss_waits_for_delayed_reward_and_map(self):
        h = harness([frame('I_FALSE'), frame('I_FALSE'), frame(), frame(),
                     frame('I_REWARD'), frame('O_DE_SETTLEMENT_CONTINUE'),
                     frame(), frame('I_DE_LOCATION')])
        self.assertFalse(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_FALSE', 'I_REWARD', 'O_DE_SETTLEMENT_CONTINUE'])
        self.assertIn('I_DE_LOCATION', h.device.image['visible'])

    def test_failed_settlement_times_out_instead_of_starting_boss(self):
        h = harness([frame()])
        capture = h.screenshot
        def advance():
            h.clock.now += 1
            return capture()
        h.screenshot = advance
        with self.assertRaisesRegex(GameStuckError, 'settlement'):
            h._finish_failed_settlement()
        self.assertEqual(h.clicks, [])

    def test_settlement_does_not_click_reward_markers_during_real_battle(self):
        h = harness([frame('I_BATTLE_INFO', 'I_REWARD', 'O_DE_SETTLEMENT_CONTINUE'),
                     frame('I_DE_LOCATION')])
        h._finish_failed_settlement()
        self.assertEqual(h.clicks, [])

    def test_active_battle_does_not_run_escape_ocr_or_click_confirmation(self):
        h = harness([frame('I_BATTLE_INFO', 'I_UI_CONFIRM_SAMLL', 'O_DE_BOSS_ESCAPED'),
                     frame('I_REWARD'), frame('I_REWARD'), frame()])
        self.assertTrue(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_REWARD'])
        self.assertNotIn('O_DE_BOSS_ESCAPED', h.appearance_checks)

    def test_normal_victory_keeps_its_win_and_reward_clicks(self):
        h = harness([frame('I_DE_WIN'), frame('I_DE_WIN'), frame(),
                     frame('I_REWARD'), frame('I_REWARD'), frame()])
        self.assertTrue(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_DE_WIN', 'I_REWARD'])


if __name__ == '__main__':
    unittest.main()
