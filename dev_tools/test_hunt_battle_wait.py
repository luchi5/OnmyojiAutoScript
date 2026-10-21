"""Exercise the production Hunt wait loop with virtual time, offline."""
import ast
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ClockTimer:
    def __init__(self, clock):
        self.clock = clock
        self.last = 0
        self.resets = 0

    def reset(self):
        self.last = self.clock[0]
        self.resets += 1


class HuntWaitTests(unittest.TestCase):
    def harness(self, battle_until=1200, result='win', result_at=1201):
        source = ast.parse((ROOT / 'tasks/Hunt/script_task.py').read_text('utf-8'))
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'battle_wait')
        ns = {'logger': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<secret>', 'exec'), ns)
        clock = [0]
        device = SimpleNamespace(stuck_timer=ClockTimer(clock), stuck_timer_long=ClockTimer(clock),
                                 stuck_record_add=Mock(), click_record_clear=Mock())
        h = SimpleNamespace(device=device, I_SE_BATTLE_WIN='special_win', I_WIN='win',
                            I_REWARD='reward', I_FALSE='loss', I_CONFIRM_CLOSE_DIFF_SOUL='modal', I_PREPARE_HIGHLIGHT='prepare', ui_click_until_disappear=Mock())
        def screenshot():
            clock[0] += 1
            if clock[0] - device.stuck_timer_long.last >= 300:
                raise TimeoutError('normal stuck guard')
        h.screenshot = Mock(side_effect=screenshot)
        h.appear = Mock(side_effect=lambda target, **kwargs: target == result and clock[0] >= result_at)
        h.appear_then_click = Mock(return_value=False)
        h.is_in_real_battle = Mock(side_effect=lambda **kw: clock[0] <= battle_until)
        h.wait = MethodType(ns['battle_wait'], h)
        return h, clock

    def test_twenty_minute_battle_reaches_reward_without_timeout(self):
        h, clock = self.harness()
        self.assertTrue(h.wait(False))
        self.assertEqual(clock[0], 1201)
        self.assertEqual(h.device.stuck_timer_long.resets, 1200)
        h.device.click_record_clear.assert_called_once()
        h.is_in_real_battle.assert_called_with(is_screenshot=False)

    def test_missing_battle_marker_still_times_out(self):
        h, _ = self.harness(battle_until=0, result_at=9999)
        with self.assertRaises(TimeoutError):
            h.wait(False)
        self.assertEqual(h.device.stuck_timer_long.resets, 0)

    def test_marker_disappears_then_timeout_remains_active(self):
        h, clock = self.harness(battle_until=400, result_at=9999)
        with self.assertRaises(TimeoutError):
            h.wait(False)
        self.assertEqual(clock[0], 700)

    def test_brief_transition_without_marker_can_finish(self):
        h, _ = self.harness(battle_until=600, result_at=610)
        self.assertTrue(h.wait(False))

    def test_win_and_loss_take_priority_over_battle_marker(self):
        for result, expected in [('win', True), ('loss', False)]:
            with self.subTest(result=result):
                h, _ = self.harness(result=result, result_at=1)
                self.assertEqual(h.wait(False), expected)
                self.assertEqual(h.device.stuck_timer_long.resets, 0)
                h.ui_click_until_disappear.assert_called_once()


if __name__ == '__main__':
    unittest.main()
