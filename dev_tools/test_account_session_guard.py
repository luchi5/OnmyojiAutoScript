"""Offline takeover and recovery regressions; never imports OAS/OCR/ADB."""
import ast
import copy
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
import unittest

import cv2
import numpy as np

from module.device.account_session_guard import other_device_login_visible
from module.exception import (AccountLoggedInElsewhere, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptError, TaskEnd)


ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'log/error/1791248563357/2026-10-06_09-02-43-318879.png'
SECOND = ROOT / 'log/error/1791248804842/2026-10-06_09-06-44-838822.png'


def load_rgb(path):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def message_frame():
    # Portable fixture contains only this specific message, no account details.
    frame = np.zeros((720, 1280, 3), np.uint8)
    template = load_rgb(ROOT / 'module/device/assets/account_logged_in_elsewhere.png')
    frame[309:346, 474:808] = template
    return frame


class FakeLogger:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.messages.append((name, args))


def isolated_class(path, class_name, names, namespace, base=None):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    selected = [copy.deepcopy(node) for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for method in selected:
        method.decorator_list = []
    definition = ast.ClassDef(name=class_name, bases=[ast.Name(id='FakeBase', ctx=ast.Load())] if base else [],
                              keywords=[], body=selected, decorator_list=[])
    if base:
        namespace['FakeBase'] = base
    module = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[class_name]


class FakeScreenCapture:
    def screenshot(self):
        self.captures += 1
        if self.capture_error:
            raise self.capture_error
        self.image = self.next_frame
        return self.image


def fake_device(frame=None, previous=None, timer_expired=False):
    logger = FakeLogger()
    cls = isolated_class(ROOT / 'module/device/device.py', 'Device',
                         {'screenshot', 'refresh_account_session', 'check_account_session'},
                         {'logger': logger, 'other_device_login_visible': other_device_login_visible,
                          'AccountLoggedInElsewhere': AccountLoggedInElsewhere,
                          'RequestHumanTakeover': RequestHumanTakeover}, FakeScreenCapture)
    device = cls()
    device.image = previous
    device.next_frame = np.zeros((720, 1280, 3), np.uint8) if frame is None else frame
    device.captures = device.stuck_checks = 0
    device.capture_error = None
    device.package = 'test.game'
    device.sleeps = []
    device.sleep = device.sleeps.append
    device.handle_night_commission = lambda: False

    def stuck_check():
        device.stuck_checks += 1
        if timer_expired:
            raise GameStuckError('simulated timer expiry')
    device.stuck_record_check = stuck_check
    return device


class FakeFunction:
    def __init__(self, key, data):
        self.command = 'Restart'
        self.enable = data['scheduler']['enable']
        self.next_run = data['scheduler']['next_run']
        self.priority = 0


def fake_script(device, task_error=None, task_callback=None):
    logger = FakeLogger()
    notifications = []
    actions = []

    class Task:
        def __init__(self, **kwargs):
            pass

        def run(self):
            if task_callback:
                task_callback()
            if task_error:
                raise task_error
            raise TaskEnd()

    exceptions = {value.__name__: value for value in
                  (AccountLoggedInElsewhere, GameBugError, GameNotRunningError, GamePageUnknownError,
                   GameStuckError, GameTooManyClickError, RequestHumanTakeover, ScriptError, TaskEnd)}
    cls = isolated_class(ROOT / 'script.py', 'Script',
                         {'_recover_account_session', '_get_next_task_with_recovery',
                          '_check_account_session_before_recovery', '_record_task_result',
                          'get_next_task', '_wait_goto_main', 'wait_until', 'run', 'loop'},
                         {**exceptions, 'logger': logger, 'Path': Path,
                          'Function': FakeFunction, 'datetime': datetime, 'date': date,
                          'timedelta': timedelta, 'time': SimpleNamespace(sleep=lambda seconds: None),
                          '_log_switch_lock': Lock(), 'IS_WINDOWS': False,
                          'inflection': SimpleNamespace(camelize=lambda name: name),
                          'del_cached_property': lambda *args: None,
                          'load_module': lambda *args: SimpleNamespace(ScriptTask=Task),
                          'I18n': SimpleNamespace(trans_zh_cn=lambda command: command)})
    script = cls()
    script.device = device
    script.config_name = '04-test'
    restart = SimpleNamespace(scheduler=SimpleNamespace(enable=False))
    restart.dict = lambda: {'scheduler': {'enable': restart.scheduler.enable,
                                          'next_run': '2099-01-01 00:00:00'}}

    def push(**kwargs):
        notifications.append(kwargs)
        return True

    script.config = SimpleNamespace(model=SimpleNamespace(running_task='KekkaiUtilize', restart=restart),
                                   notifier=SimpleNamespace(push=push),
                                   task_call=lambda task: actions.append(task))
    script.config.script = SimpleNamespace(device=SimpleNamespace(run_background_only=True),
                                           error=SimpleNamespace(handle_error=True, error_repeated=True))
    script.get_next_task = lambda: 'KekkaiUtilize'
    script.is_first_task = False
    script.state_queue = None
    script._emulator_down = False
    script.failure_record = {}
    script.anti_ban_guard = SimpleNamespace(reset=lambda: None, record_active=lambda seconds: None,
                                           wake_time=lambda now, config: None)
    device.stuck_record_clear = device.click_record_clear = lambda: None
    device.emulator_stop = lambda: actions.append('emulator_stop')
    device.app_is_running = lambda: True
    device.release_during_wait = lambda: actions.append('release_during_wait')
    script.instance_guard = None
    script._try_acquire_queue_token = lambda: True
    script.exception_handler = lambda **kwargs: actions.append('exception_handler')
    script.save_error_log = lambda: actions.append('save_evidence')
    script.logger, script.notifications, script.actions = logger, notifications, actions
    return script


class AccountSessionGuardTests(unittest.TestCase):
    def waiting_script(self, frame=None, task_error=None):
        script = fake_script(fake_device(frame), task_error)
        future = datetime.now() + timedelta(hours=3)
        polls = []

        def next_task():
            polls.append(True)
            if len(polls) > 3:
                raise AssertionError('Recovery was swallowed by the internal waiting loop')
            return SimpleNamespace(command='DailyTrifles', next_run=future)

        script.config.get_next = next_task
        script.config.script.anti_ban = SimpleNamespace()
        script.get_next_task = type(script).get_next_task.__get__(script)
        script._handle_wait_during_idle = script._wait_goto_main
        script.wait_until = lambda when: script.actions.append('wait_until') or True
        script.polls = polls
        return script, future

    def test_specific_message_matches_and_blank_is_negative(self):
        self.assertTrue(other_device_login_visible(message_frame()))
        self.assertFalse(other_device_login_visible(np.zeros((720, 1280, 3), np.uint8)))

    def test_real_network_dialog_and_common_confirm_button_are_negative(self):
        network = load_rgb(ROOT / 'tasks/GlobalGame/gg/gg_network_error.png')
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[253:253 + network.shape[0], 431:431 + network.shape[1]] = network
        self.assertFalse(other_device_login_visible(frame))
        # Its same yellow confirmation button must not identify a takeover.
        frame[309:346, 474:808] = 180
        self.assertFalse(other_device_login_visible(frame))

    def test_missing_or_wrong_size_frames_do_not_identify_takeover(self):
        for frame in (None, {}, np.zeros((1080, 1920, 3), np.uint8),
                      np.zeros((720, 1280, 4), np.uint8)):
            self.assertFalse(other_device_login_visible(frame))

    @unittest.skipUnless(FIRST.exists() and SECOND.exists(), 'Live incident screenshots retained locally')
    def test_independent_real_incident_frames_both_match(self):
        self.assertTrue(other_device_login_visible(load_rgb(FIRST)))
        self.assertTrue(other_device_login_visible(load_rgb(SECOND)))

    def test_new_takeover_frame_precedes_an_expired_stuck_timer(self):
        old = np.zeros((720, 1280, 3), np.uint8)
        device = fake_device(message_frame(), previous=old, timer_expired=True)
        script = fake_script(device)
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertEqual(device.captures, 1)
        self.assertEqual(device.stuck_checks, 0)
        self.assertNotIn('Restart', script.actions)
        self.assertEqual(device.sleeps, [10])
        self.assertTrue(script._account_session_restart_pending)
        self.assertEqual(script.config.model.running_task, '')
        self.assertEqual(len(script.notifications), 1)

    def test_fresh_recovery_check_catches_popup_after_original_failure(self):
        for error_type in (GameStuckError, GameTooManyClickError, GamePageUnknownError,
                           GameBugError, GameNotRunningError):
            with self.subTest(error=error_type.__name__):
                device = fake_device()
                original_capture = device.screenshot

                def initial_frame():
                    frame = original_capture()
                    device.next_frame = message_frame()
                    return frame
                device.screenshot = initial_frame
                script = fake_script(device, error_type('simulated failure'))
                self.assertFalse(script.run('KekkaiUtilize'))
                self.assertEqual(device.captures, 2)
                self.assertNotIn('Restart', script.actions)
                self.assertNotIn('exception_handler', script.actions)
                self.assertEqual(device.sleeps, [10])
                self.assertTrue(script._account_session_restart_pending)
                self.assertEqual(len(script.notifications), 1)

    def test_normal_network_or_stuck_failure_keeps_restart_recovery(self):
        device = fake_device()
        script = fake_script(device, GameStuckError('ordinary network timeout'))
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertIn('Restart', script.actions)
        self.assertEqual(device.sleeps, [10])

    def test_ordinary_expired_timer_keeps_original_stuck_recovery(self):
        device = fake_device(timer_expired=True)
        script = fake_script(device)
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertEqual(device.captures, 2)
        self.assertEqual(device.stuck_checks, 1)
        self.assertIn('Restart', script.actions)
        self.assertEqual(device.sleeps, [10])

    def test_diagnostic_capture_failure_does_not_mask_original_recovery(self):
        device = fake_device()
        original_capture = device.screenshot

        def initial_frame():
            frame = original_capture()
            device.capture_error = OSError('simulated diagnostic failure')
            return frame
        device.screenshot = initial_frame
        script = fake_script(device, GameStuckError('original timeout'))
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertIn('Restart', script.actions)
        self.assertEqual(device.sleeps, [10])

    def test_takeover_keeps_queue_for_recovery_without_immediate_game_actions(self):
        script = fake_script(fake_device(message_frame()))
        released = []
        script.instance_guard = SimpleNamespace(token_lost=False, release=lambda: released.append(True))
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertEqual(released, [])
        self.assertNotIn('Restart', script.actions)
        self.assertNotIn('emulator_stop', script.actions)
        self.assertTrue(script._account_session_restart_pending)

    def test_notification_or_evidence_exception_does_not_block_recovery(self):
        script = fake_script(fake_device(message_frame()))

        def fail(*args, **kwargs):
            raise OSError('simulated auxiliary failure')
        script.save_error_log = script.config.notifier.push = fail
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertNotIn('Restart', script.actions)
        self.assertTrue(script._account_session_restart_pending)

    def test_rejected_notification_is_logged_without_blocking_recovery(self):
        script = fake_script(fake_device(message_frame()))
        script.config.notifier.push = lambda **kwargs: False
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertNotIn('Restart', script.actions)
        self.assertEqual(script.config.model.running_task, '')
        self.assertTrue(script._account_session_restart_pending)
        self.assertTrue(any('顶号恢复通知请求失败' in str(args)
                            for level, args in script.logger.messages if level == 'warning'))

    def test_accepted_notification_does_not_log_request_failure(self):
        script = fake_script(fake_device(message_frame()))
        script.config.notifier.push = lambda **kwargs: True
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertNotIn('Restart', script.actions)
        self.assertTrue(script._account_session_restart_pending)
        self.assertFalse(any('顶号恢复通知请求失败' in str(args)
                             for level, args in script.logger.messages if level == 'warning'))

    def test_manual_new_worker_runs_normally_after_popup_is_handled(self):
        script = fake_script(fake_device())
        self.assertTrue(script.run('KekkaiUtilize'))
        self.assertEqual(script.actions, [])

    def test_disabled_scheduled_restart_recovers_once_without_enabling_it(self):
        script = fake_script(fake_device(message_frame()))
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertFalse(script.config.model.restart.scheduler.enable)
        self.assertEqual(script._get_next_task_with_recovery(), 'Restart')
        self.assertFalse(script._account_session_restart_pending)
        self.assertTrue(script.config.task.enable)
        self.assertEqual(script.config.task.command, 'Restart')
        self.assertFalse(script.config.model.restart.scheduler.enable)
        self.assertEqual(script._get_next_task_with_recovery(), 'KekkaiUtilize')
        self.assertNotIn('Restart', script.actions)

    def test_enabled_restart_is_not_also_queued_by_task_call(self):
        script = fake_script(fake_device(message_frame()))
        script.config.model.restart.scheduler.enable = True
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertEqual(script._get_next_task_with_recovery(), 'Restart')
        self.assertTrue(script.config.model.restart.scheduler.enable)
        self.assertEqual(script._get_next_task_with_recovery(), 'KekkaiUtilize')
        self.assertNotIn('Restart', script.actions)

    def test_forced_recovery_is_not_skipped_as_the_first_restart(self):
        script = fake_script(fake_device())
        script.is_first_task = True
        script._account_session_restart_pending = True
        self.assertEqual(script._get_next_task_with_recovery(), 'Restart')
        self.assertFalse(script.is_first_task)

    def test_restart_bypasses_first_frame_takeover_then_restores_detection(self):
        device = fake_device(message_frame())
        observed = []
        script = fake_script(device, task_callback=lambda: observed.append(device._account_session_recovery))
        self.assertTrue(script.run('Restart'))
        self.assertEqual(observed, [True])
        self.assertEqual(device.captures, 1)
        self.assertFalse(device._account_session_recovery)
        self.assertEqual(script.notifications, [])
        with self.assertRaises(AccountLoggedInElsewhere):
            device.check_account_session()

    def test_restart_restores_guard_after_failure_and_exception(self):
        for error in (GameStuckError('login timeout'), ScriptError('invalid setting'),
                      RuntimeError('unexpected failure'), RequestHumanTakeover('login failed')):
            with self.subTest(error=type(error).__name__):
                device = fake_device(message_frame())
                script = fake_script(device, error)
                if isinstance(error, GameStuckError):
                    self.assertFalse(script.run('Restart'))
                else:
                    with self.assertRaises(SystemExit):
                        script.run('Restart')
                self.assertFalse(device._account_session_recovery)
                with self.assertRaises(AccountLoggedInElsewhere):
                    device.check_account_session()

    def test_normal_task_enforces_guard_even_if_a_previous_scope_was_active(self):
        device = fake_device(message_frame())
        device._account_session_recovery = True
        script = fake_script(device)
        self.assertFalse(script.run('KekkaiUtilize'))
        self.assertTrue(script._account_session_restart_pending)
        # The previous scope is restored, rather than silently discarded.
        self.assertTrue(device._account_session_recovery)

    def test_original_three_failures_limit_stops_before_a_third_recovery(self):
        script = fake_script(fake_device(message_frame()))
        released, order = [], []
        script.instance_guard = SimpleNamespace(token_lost=False, release=lambda: released.append(True))
        original_run = script.run

        def record_run(command):
            order.append(command)
            return original_run(command)

        script.run = record_run
        with self.assertRaises(SystemExit):
            script.loop()
        self.assertEqual(order, ['KekkaiUtilize', 'Restart', 'KekkaiUtilize', 'Restart', 'KekkaiUtilize'])
        self.assertEqual(script.failure_record['KekkaiUtilize'], 3)
        self.assertEqual(script.failure_record['Restart'], 0)
        self.assertEqual(script.actions.count('emulator_stop'), 1)
        self.assertEqual(released, [])
        self.assertFalse(script.config.model.restart.scheduler.enable)
        self.assertEqual(len(script.notifications), 4)  # Three takeovers plus the terminal failure notice.

    def test_login_still_attempts_twice_and_reports_the_correct_number(self):
        logger = FakeLogger()
        cls = isolated_class(ROOT / 'tasks/Restart/login.py', 'LoginHandler', {'app_handle_login'},
                             {'logger': logger, 'GameStuckError': GameStuckError,
                              'GameTooManyClickError': GameTooManyClickError,
                              'RequestHumanTakeover': RequestHumanTakeover})
        handler = cls()
        attempts, actions = [], []
        handler.device = SimpleNamespace(stuck_record_clear=lambda: None, click_record_clear=lambda: None,
                                         app_stop=lambda: actions.append('stop'),
                                         app_start=lambda: actions.append('start'))
        handler.config = SimpleNamespace(restart=SimpleNamespace(harvest_config=SimpleNamespace(enable=False)))

        def failed_login():
            attempts.append(True)
            raise GameStuckError('simulated login timeout')

        handler._app_handle_login = failed_login
        with self.assertRaises(RequestHumanTakeover):
            handler.app_handle_login()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(actions, ['stop', 'start', 'stop', 'start'])
        self.assertTrue(any('Login failed after 2 attempts' in str(args) for _, args in logger.messages))

    def test_future_task_wait_returns_forced_restart_after_idle_takeover(self):
        script, _ = self.waiting_script(message_frame())
        self.assertEqual(script._get_next_task_with_recovery(), 'Restart')
        self.assertEqual(len(script.polls), 1)
        self.assertEqual(script.failure_record['GotoMain'], 1)
        self.assertFalse(script.config.model.restart.scheduler.enable)
        self.assertNotIn('wait_until', script.actions)
        self.assertNotIn('release_during_wait', script.actions)
        self.assertNotIn('Restart', script.actions)

    def test_repeated_idle_takeovers_stop_after_three_navigation_failures(self):
        script, _ = self.waiting_script(message_frame())
        released, order = [], []
        script.instance_guard = SimpleNamespace(token_lost=False, release=lambda: released.append(True))
        original_run = script.run

        def record_run(command):
            order.append(command)
            return original_run(command)

        script.run = record_run
        with self.assertRaises(SystemExit):
            script.loop()
        self.assertEqual(order, ['GotoMain', 'Restart', 'GotoMain', 'Restart', 'GotoMain'])
        self.assertEqual(script.failure_record['GotoMain'], 3)
        self.assertEqual(script.failure_record['Restart'], 0)
        self.assertNotIn('DailyTrifles', script.failure_record)
        self.assertEqual(script.actions.count('emulator_stop'), 1)
        self.assertEqual(len(script.notifications), 4)
        self.assertEqual(released, [])
        self.assertFalse(script.config.model.restart.scheduler.enable)

    def test_successful_idle_navigation_clears_its_takeover_failure_count(self):
        script, future = self.waiting_script()
        script.failure_record['GotoMain'] = 2
        self.assertTrue(script._wait_goto_main(future))
        self.assertEqual(script.failure_record['GotoMain'], 0)
        self.assertEqual(script.actions, ['release_during_wait', 'wait_until'])
        self.assertEqual(script.notifications, [])

    def test_ordinary_idle_navigation_error_keeps_existing_recovery_without_new_count(self):
        script, future = self.waiting_script(task_error=GameStuckError('ordinary network failure'))
        self.assertFalse(script._wait_goto_main(future))
        self.assertNotIn('GotoMain', script.failure_record)
        self.assertIn('Restart', script.actions)
        self.assertFalse(getattr(script, '_account_session_restart_pending', False))

    def test_pending_takeover_interrupts_time_wait_before_the_future_task(self):
        script = fake_script(fake_device())
        script._account_session_restart_pending = True
        script.config.start_watching = lambda: None

        def unexpected_poll():
            raise AssertionError('Pending recovery should interrupt before waiting or config polling')

        script.config.should_reload = unexpected_poll
        self.assertFalse(script.wait_until(datetime.now() + timedelta(hours=3)))
        self.assertEqual(script.device.captures, 0)


if __name__ == '__main__':
    unittest.main()
