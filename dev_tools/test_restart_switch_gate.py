"""Device-free behavior checks of the real scheduler methods via AST extraction."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from test_account_session_guard import (
    fake_script, fake_device, message_frame, GameStuckError, GameTooManyClickError,
    GamePageUnknownError, GameBugError, GameNotRunningError, ScriptError,
    RequestHumanTakeover,
)

RECOVERABLE = (GameStuckError, GameTooManyClickError, GamePageUnknownError,
               GameBugError, GameNotRunningError)


class RestartSwitchGateTests(unittest.TestCase):
    def test_disabled_restart_stops_every_ordinary_recovery_before_actions(self):
        for error_type in RECOVERABLE:
            with self.subTest(error=error_type.__name__):
                script = fake_script(fake_device(), error_type('ordinary failure'))
                with self.assertRaises(SystemExit) as stopped:
                    script.run('KekkaiUtilize')
                self.assertEqual(stopped.exception.code, 1)
                self.assertEqual(script.actions, ['save_evidence'])
                self.assertEqual(script.device.sleeps, [])
                self.assertEqual(len(script.notifications), 1)
                self.assertIn('重启任务未开启', script.notifications[0]['content'])
                self.assertFalse(script.config.model.restart.scheduler.enable)
                self.assertFalse(getattr(script, '_account_session_restart_pending', False))

    def test_enabled_restart_preserves_original_recovery_for_each_exception(self):
        for error_type in RECOVERABLE:
            with self.subTest(error=error_type.__name__):
                script = fake_script(fake_device(), error_type('ordinary failure'))
                script.config.model.restart.scheduler.enable = True
                self.assertEqual(script.run('KekkaiUtilize'), error_type is GameNotRunningError)
                self.assertIn('Restart', script.actions)
                self.assertIn('exception_handler', script.actions)
                self.assertEqual(script.device.sleeps, [] if error_type is GameNotRunningError else [10])

    def test_off_stops_on_first_failure_instead_of_three_attempts(self):
        script = fake_script(fake_device(), GameStuckError('failure'))
        called = []
        original = script.run
        script.run = lambda task: called.append(task) or original(task)
        with self.assertRaises(SystemExit): script.loop()
        self.assertEqual(called, ['KekkaiUtilize'])
        self.assertNotIn('emulator_stop', script.actions)

    def test_latest_off_overrides_cached_on_before_recovery(self):
        script = fake_script(fake_device(), GameStuckError('ordinary failure'))
        script.config.model.restart.scheduler.enable = True
        reads = []
        def latest(name):
            reads.append(name)
            return {'restart': {'scheduler': {'enable': False}}}
        script.config.model.read_json = latest
        with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
        self.assertEqual(reads, ['04-test'])
        self.assertNotIn('Restart', script.actions)

    def test_latest_on_overrides_cached_off_without_mutating_cached_model(self):
        script = fake_script(fake_device(), GameStuckError('ordinary failure'))
        script.config.model.read_json = lambda name: {'restart': {'scheduler': {'enable': True}}}
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertIn('Restart', script.actions)
        self.assertFalse(script.config.model.restart.scheduler.enable)

    def test_unreadable_or_malformed_switch_cannot_reuse_cached_permission(self):
        for latest in (None, {}, {'restart': {}}, {'restart': {'scheduler': {'enable': 'false'}}},
                       {'restart': {'scheduler': {'enable': 1}}}):
            with self.subTest(latest=latest):
                script = fake_script(fake_device(), GameStuckError('ordinary failure'))
                script.config.model.restart.scheduler.enable = True
                script.config.model.read_json = lambda name: latest
                with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
                self.assertNotIn('Restart', script.actions)
                self.assertIn('无法核验', script.notifications[0]['content'])
        script = fake_script(fake_device(), GameStuckError('ordinary failure'))
        script.config.model.restart.scheduler.enable = True
        def broken(name): raise OSError('offline read failure')
        script.config.model.read_json = broken
        with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
        self.assertNotIn('Restart', script.actions)

    def test_queued_takeover_cannot_bypass_a_newly_disabled_switch(self):
        script = fake_script(fake_device(message_frame()))
        script.config.model.restart.scheduler.enable = True
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertEqual(script._get_next_task_with_recovery(), 'Restart')
        script.config.model.restart.scheduler.enable = False
        with self.assertRaises(SystemExit): script.run('Restart')
        self.assertNotIn('Restart', script.actions)

    def test_direct_restart_is_blocked_even_if_a_cached_task_is_enabled(self):
        script = fake_script(fake_device())
        script.config.task = SimpleNamespace(command='Restart', enable=True)
        with self.assertRaises(SystemExit): script.run('Restart')
        self.assertEqual(script.device.captures, 0)
        self.assertEqual(script.actions, ['save_evidence'])

    def test_idle_game_missing_uses_normal_start_with_restart_disabled(self):
        script = fake_script(fake_device())
        script.device.app_is_running = lambda: False
        calls = []
        script.run = lambda command: calls.append(command) or True
        script.wait_until = lambda future: True
        self.assertTrue(script._wait_goto_main(datetime.now() + timedelta(hours=1)))
        self.assertEqual(calls, ['GameStart', 'GotoMain'])
        self.assertFalse(script.config.model.restart.scheduler.enable)

    def test_takeover_stops_with_switch_off(self):
        script = fake_script(fake_device(message_frame()))
        with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
        self.assertFalse(getattr(script, '_account_session_restart_pending', False))
        self.assertEqual(script.device.sleeps, [])
        self.assertNotIn('Restart', script.actions)

    def test_pending_takeover_rechecks_switch(self):
        script = fake_script(fake_device())
        script._account_session_restart_pending = True
        with self.assertRaises(SystemExit): script._get_next_task_with_recovery()
        self.assertFalse(getattr(script, '_account_session_restart_authorized', False))

    def test_notifications_or_evidence_failures_cannot_prevent_stop(self):
        for which in ('evidence', 'notification', 'both', 'rejected'):
            with self.subTest(which=which):
                script = fake_script(fake_device(), GameStuckError('failure'))
                def fail(*args, **kwargs): raise OSError('offline auxiliary failure')
                if which in ('evidence', 'both'): script.save_error_log = fail
                if which in ('notification', 'both'): script.config.notifier.push = fail
                if which == 'rejected': script.config.notifier.push = lambda **kwargs: False
                with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
                self.assertNotIn('Restart', script.actions)
                self.assertNotIn('emulator_stop', script.actions)
                self.assertEqual(script.device.sleeps, [])

    def test_normal_tasks_and_unrecoverable_errors_keep_previous_behavior(self):
        script = fake_script(fake_device())
        self.assertTrue(script.run('KekkaiUtilize'))
        self.assertEqual(script.actions, [])
        for enabled in (False, True):
            for error in (ScriptError('invalid'), RequestHumanTakeover('manual'), RuntimeError('unexpected')):
                with self.subTest(enabled=enabled, error=type(error).__name__):
                    script = fake_script(fake_device(), error)
                    script.config.model.restart.scheduler.enable = enabled
                    with self.assertRaises(SystemExit): script.run('KekkaiUtilize')
                    self.assertNotIn('Restart', script.actions)


if __name__ == '__main__': unittest.main()
