"""Exercise failed emulator recovery without importing any device/runtime code.

Only the relevant AST methods are loaded; all devices and notifications are fake.
Run: toolkit/python.exe -B tests/test_emulator_failure_propagation.py
"""
import ast
import copy
from functools import cached_property, wraps
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


class RequestHumanTakeover(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


def source_tree(relative):
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def isolated_class(relative, class_name, method_names, namespace, bases=()):
    tree = source_tree(relative)
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [copy.deepcopy(node) for node in original.body
               if isinstance(node, ast.FunctionDef) and node.name in method_names]
    if len(methods) != len(method_names):
        raise AssertionError("Missing requested methods")
    isolated = ast.ClassDef(name=class_name,
                            bases=[ast.Name(id=name, ctx=ast.Load()) for name in bases],
                            keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[]))
    exec(compile(module, relative, "exec"), namespace)
    return namespace[class_name]


class DeviceRecoveryTests(unittest.TestCase):
    def make_device(self, recovery_result):
        initialize = Mock(side_effect=[EmulatorNotRunningError(), None])
        restart = Mock(return_value=recovery_result)
        screenshot_setup = Mock()

        class FakeBase:
            def __init__(self):
                self.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
                    screenshot_method="adb", emulatorinfo_type="MuMuPlayer12")))
                self.emulator_instance = object()
                self.emulator_start = restart
                self.screenshot_interval_set = screenshot_setup
                initialize()

        cls = isolated_class("module/device/device.py", "Device", {"__init__"}, {
            "FakeBase": FakeBase, "logger": Mock(), "IS_WINDOWS": False,
            "RequestHumanTakeover": RequestHumanTakeover,
            "EmulatorNotRunningError": EmulatorNotRunningError,
        }, bases=("FakeBase",))
        return cls, initialize, restart, screenshot_setup

    def test_failed_recovery_does_not_restart_outer_initialization(self):
        cls, initialize, restart, setup = self.make_device(False)
        with self.assertRaisesRegex(RequestHumanTakeover, "bounded retries"):
            cls()
        initialize.assert_called_once_with()
        restart.assert_called_once_with()
        setup.assert_not_called()

    def test_successful_recovery_retries_connection_normally(self):
        cls, initialize, restart, setup = self.make_device(True)
        cls()
        self.assertEqual(initialize.call_count, 2)
        restart.assert_called_once_with()
        setup.assert_called_once_with()

    def test_legacy_unspecified_result_keeps_existing_behavior(self):
        cls, initialize, restart, setup = self.make_device(None)
        cls()
        self.assertEqual(initialize.call_count, 2)
        setup.assert_called_once_with()


class AdbRecoveryTests(unittest.TestCase):
    def make_retry(self):
        node = next(node for node in source_tree("module/device/method/adb.py").body
                    if isinstance(node, ast.FunctionDef) and node.name == "retry")
        namespace = {
            "wraps": wraps, "RETRY_TRIES": 5, "logger": Mock(), "retry_sleep": Mock(),
            "RequestHumanTakeover": RequestHumanTakeover,
            "EmulatorNotRunningError": EmulatorNotRunningError,
            "AdbError": type("AdbError", (Exception,), {}),
            "PackageNotInstalled": type("PackageNotInstalled", (Exception,), {}),
            "ImageTruncated": type("ImageTruncated", (Exception,), {}),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated_adb", "exec"), namespace)
        return namespace["retry"]

    def test_adb_failure_stops_after_one_bounded_platform_recovery(self):
        device = SimpleNamespace(emulator_start=Mock(return_value=False), adb_reconnect=Mock())
        calls = Mock(side_effect=EmulatorNotRunningError)

        @self.make_retry()
        def action(self):
            return calls()

        with self.assertRaisesRegex(RequestHumanTakeover, "bounded retries"):
            action(device)
        calls.assert_called_once_with()
        device.emulator_start.assert_called_once_with()
        device.adb_reconnect.assert_not_called()

    def test_adb_successful_recovery_reconnects_and_resumes_action(self):
        device = SimpleNamespace(emulator_start=Mock(return_value=True), adb_reconnect=Mock())
        calls = Mock(side_effect=[EmulatorNotRunningError(), "result"])

        @self.make_retry()
        def action(self):
            return calls()

        self.assertEqual(action(device), "result")
        self.assertEqual(calls.call_count, 2)
        device.emulator_start.assert_called_once_with()
        device.adb_reconnect.assert_called_once_with()


class ScriptNotificationTests(unittest.TestCase):
    def test_running_task_recovery_failure_stops_even_if_notification_fails(self):
        namespace = {'logger': Mock(), 'RequestHumanTakeover': RequestHumanTakeover,
                     'I18n': SimpleNamespace(trans_zh_cn=lambda command: command)}
        for name in ('AccountLoggedInElsewhere', 'TaskEnd', 'GameNotRunningError',
                     'GameStuckError', 'GameTooManyClickError', 'GameBugError',
                     'GamePageUnknownError', 'ScriptError'):
            namespace[name] = type(name, (Exception,), {})
        cls = isolated_class('script.py', 'Script', {'run'}, namespace)
        script = cls()
        script._start_game_normally = Mock()
        script._notify_error_email = Mock()
        script.instance_guard = None
        script._try_acquire_queue_token = Mock(return_value=True)
        script.config_name = 'test-account'
        push = Mock(side_effect=RuntimeError('notification offline'))
        script.config = SimpleNamespace(notifier=SimpleNamespace(push=push))
        screenshot = Mock(side_effect=RequestHumanTakeover('Emulator recovery failed'))
        script.device = SimpleNamespace(screenshot=screenshot)
        script.exception_handler = Mock()
        with self.assertRaises(SystemExit) as caught:
            script.run('DailyTrifles')
        self.assertEqual(caught.exception.code, 1)
        screenshot.assert_called_once()
        script._start_game_normally.assert_called_once_with()
        script._notify_error_email.assert_called_once_with('DailyTrifles', screenshot.side_effect)
        push.assert_called_once()
        self.assertFalse(script.device._account_session_recovery)

    def make_script(self, *, error=None, notifier_error=None, accepted=True):
        created = object()
        factory = Mock(return_value=created, side_effect=error)
        script_class = isolated_class("script.py", "Script", {"device", "_create_device"}, {
            "cached_property": cached_property, "logger": Mock(), "Device": factory,
            "RequestHumanTakeover": RequestHumanTakeover,
        })
        script = script_class()
        script._notify_error_email = Mock()
        script.config_name = "test-account"
        push = Mock(return_value=accepted, side_effect=notifier_error)
        script.config = SimpleNamespace(notifier=SimpleNamespace(push=push))
        return script, factory, push, created

    def test_initial_device_failure_notifies_once_without_recursion(self):
        script, factory, push, _ = self.make_script(error=RequestHumanTakeover("failed"))
        with self.assertRaises(SystemExit) as caught:
            _ = script.device
        self.assertEqual(caught.exception.code, 1)
        factory.assert_called_once_with(config=script.config)
        push.assert_called_once()
        self.assertNotIn("device", script.__dict__)

    def test_idle_wakeup_failure_also_notifies_once_and_stops(self):
        script, factory, push, _ = self.make_script(error=RequestHumanTakeover("failed"))
        previous_device = object()
        script.device = previous_device
        with self.assertRaises(SystemExit) as caught:
            script.device = script._create_device()
        self.assertEqual(caught.exception.code, 1)
        self.assertIs(script.device, previous_device)
        factory.assert_called_once()
        push.assert_called_once()

    def test_notification_exception_cannot_restart_or_keep_script_running(self):
        script, factory, push, _ = self.make_script(
            error=RequestHumanTakeover("failed"), notifier_error=RuntimeError("offline"))
        with self.assertRaises(SystemExit) as caught:
            _ = script.device
        self.assertEqual(caught.exception.code, 1)
        factory.assert_called_once()
        push.assert_called_once()

    def test_rejected_notification_still_stops(self):
        script, factory, push, _ = self.make_script(error=RequestHumanTakeover(), accepted=False)
        with self.assertRaises(SystemExit):
            script._create_device()
        factory.assert_called_once()
        push.assert_called_once()

    def test_successful_creation_is_cached_without_failure_notification(self):
        script, factory, push, created = self.make_script()
        self.assertIs(script.device, created)
        self.assertIs(script.device, created)
        factory.assert_called_once_with(config=script.config)
        push.assert_not_called()

    def test_unrelated_device_exception_keeps_original_propagation(self):
        script, factory, push, _ = self.make_script(error=ValueError("configuration"))
        with self.assertRaisesRegex(ValueError, "configuration"):
            script._create_device()
        factory.assert_called_once()
        push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
