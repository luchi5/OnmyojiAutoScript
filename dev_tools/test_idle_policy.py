"""Idle policy regressions without backend/device/OCR initialization."""
from datetime import datetime, timedelta, time
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeLogger, extract_methods

SOURCE = Path(__file__).resolve().parents[1] / 'script.py'


class IdleHarness:
    def __init__(self, method='goto_main', down=False, interrupted=False, app_running=True):
        self.actions = []
        self._emulator_down = down
        self.config = SimpleNamespace(script=SimpleNamespace(optimization=SimpleNamespace(
            when_task_queue_empty=method, close_game_wait_duration=time(minute=10),
            close_emulator_wait_duration=time(minute=30))))
        self.device = SimpleNamespace(
            app_stop=lambda: self.actions.append('close_game'),
            emulator_stop=lambda: self.actions.append('close_emulator'),
            app_is_running=lambda: app_running and not down,
            release_during_wait=lambda: self.actions.append('release'))
        self.run = lambda command: self.actions.append(command) or True
        self._time_to_timedelta = lambda value: timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
        self.wait_until = lambda future: self.wait('wait', interrupted)
        self._wait_until_with_emulator_preheat = lambda future: self.wait('preheat', interrupted)
        self._create_device = self.wake_device
        methods = extract_methods(SOURCE, 'Script',
            ('_handle_wait_during_idle', '_wait_goto_main', '_wait_close_game', '_wait_stay_there'),
            {'logger': FakeLogger(), 'datetime': datetime, 'timedelta': timedelta,
             'stop_idle_emulator': lambda *args: self.actions.append('check_stop_existing') or True,
             'idle_emulator_running': lambda *args: getattr(self, 'probe_running', False)})
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def wait(self, action, interrupted):
        self.actions.append(action)
        return not interrupted

    def wake_device(self):
        self.actions.append('wake_emulator')
        return self.device


class IdlePolicyTests(unittest.TestCase):
    def test_starting_long_idle_script_does_not_initialize_device_to_close_it(self):
        harness = IdleHarness('close_game', interrupted=True)
        del harness.device
        self.assertFalse(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['check_stop_existing', 'preheat'])
        self.assertTrue(harness._emulator_down)
        self.assertNotIn('device', harness.__dict__)

    def test_long_idle_start_resumes_restart_only_after_preheat_finishes(self):
        harness = IdleHarness('close_game')
        del harness.device
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['check_stop_existing', 'preheat', 'GameStart'])

    def test_closed_cold_start_never_creates_device_for_short_or_medium_wait(self):
        for minutes in (7, 15, 30):
            with self.subTest(minutes=minutes):
                harness = IdleHarness('close_game', interrupted=True)
                del harness.device
                self.assertFalse(harness._handle_wait_during_idle(datetime.now() + timedelta(minutes=minutes)))
                self.assertEqual(harness.actions, ['preheat'])
                self.assertNotIn('device', harness.__dict__)
                self.assertTrue(harness._emulator_down)

    def test_unknown_cold_state_waits_without_claiming_closed(self):
        harness = IdleHarness('close_game', interrupted=True)
        del harness.device
        harness.probe_running = None
        self.assertFalse(harness._handle_wait_during_idle(datetime.now() + timedelta(minutes=15)))
        self.assertEqual(harness.actions, ['wait'])
        self.assertFalse(harness._emulator_down)

    def test_courtyard_ignores_both_close_timers_during_long_wait(self):
        harness = IdleHarness()
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['GotoMain', 'release', 'wait'])
        self.assertFalse(harness._emulator_down)

    def test_courtyard_config_change_interrupts_wait_without_closing(self):
        harness = IdleHarness(interrupted=True)
        self.assertFalse(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['GotoMain', 'release', 'wait'])

    def test_close_game_policy_can_still_close_emulator_for_long_wait(self):
        harness = IdleHarness('close_game')
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['close_emulator', 'preheat', 'GameStart'])

    def test_close_game_policy_only_closes_game_for_medium_wait(self):
        harness = IdleHarness('close_game')
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(minutes=15)))
        self.assertEqual(harness.actions, ['close_game', 'release', 'wait', 'GameStart'])

    def test_close_game_policy_keeps_game_for_short_wait(self):
        harness = IdleHarness('close_game')
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(minutes=5)))
        self.assertEqual(harness.actions, ['release', 'wait'])

    def test_courtyard_immediately_restores_an_emulator_that_was_already_down(self):
        harness = IdleHarness(down=True)
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['wake_emulator', 'GameStart', 'GotoMain', 'release', 'wait'])
        self.assertFalse(harness._emulator_down)

    def test_courtyard_starts_closed_game_before_navigation(self):
        harness = IdleHarness(app_running=False)
        self.assertTrue(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['GameStart', 'GotoMain', 'release', 'wait'])

    def test_failed_courtyard_navigation_returns_to_scheduler_recovery(self):
        harness = IdleHarness()
        harness.run = lambda command: harness.actions.append(command) or False
        self.assertFalse(harness._handle_wait_during_idle(datetime.now() + timedelta(hours=3)))
        self.assertEqual(harness.actions, ['GotoMain'])


if __name__ == '__main__':
    unittest.main()
