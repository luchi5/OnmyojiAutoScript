"""Offline Hunt preset modal regressions using real methods and fake frames."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from dev_tools.test_demon_preset_transition import Harness, Logger, Timer, extract, frame, prepare, battle

ROOT = Path(__file__).resolve().parents[1]


class HuntPresetDialogTests(unittest.TestCase):
    def test_slow_preset_gets_fresh_frame_and_time_for_modal(self):
        h = Harness([prepare(), frame('I_CONFIRM_CLOSE_DIFF_SOUL'), battle()], demon=False)
        def slow_switch(*args):
            h.clock.now += 12
        h.switch_preset_team = slow_switch
        h.current_count = 1
        self.assertTrue(h.battle_before(None, h.conf))
        self.assertEqual(h.clicks, ['I_CONFIRM_CLOSE_DIFF_SOUL'])

    def test_late_hunt_modal_is_closed_before_preparation(self):
        h = Harness([frame('I_CONFIRM_CLOSE_DIFF_SOUL', 'I_PREPARE_HIGHLIGHT'),
                     prepare(), battle(), frame('I_WIN')], demon=False)
        h.device.stuck_timer = SimpleNamespace(reset=lambda: None)
        h.device.stuck_timer_long = SimpleNamespace(reset=lambda: None)
        namespace = {'logger': Logger(), 'Timer': lambda seconds: Timer(h.clock, seconds)}
        h.battle_wait = extract(ROOT/'tasks/Hunt/script_task.py', 'ScriptTask',
                               ('battle_wait',), namespace)['battle_wait'].__get__(h)
        self.assertTrue(h.battle_wait(False))
        self.assertEqual(h.clicks, ['I_CONFIRM_CLOSE_DIFF_SOUL', 'I_PREPARE_HIGHLIGHT'])
        self.assertEqual(h.rewards, ['I_WIN'])


if __name__ == '__main__':
    unittest.main()
