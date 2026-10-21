"""Recovery control flow, using fake startup/shutdown only. No live devices."""
import ast
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_mumu_launch import Emulator, FakeInstance, isolated_platform_class


def recovery_platform():
    path = Path(__file__).resolve().parents[1] / 'module/device/platform2/platform_windows.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PlatformWindows')
    methods = [copy.deepcopy(n) for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {'emulator_start', 'emulator_stop'}]
    ns = {'logger': Mock(), 'Emulator': Emulator, 'wait_for_launch_slot': Mock()}
    cls = ast.ClassDef(name='Platform', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'), ns)
    return ns['Platform']()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.platform = recovery_platform()
        self.platform.emulator_instance = FakeInstance(r'C:\MuMu\nx_main\MuMuNxMain.exe', 6)
        self.events = []
        self.platform._emulator_stop = Mock(side_effect=lambda: self.events.append('stop'))
        self.platform._emulator_start = Mock(side_effect=lambda: self.events.append('start'))
        self.platform._emulator_function_wrapper = lambda f: (f(), True)[1]

    def watch(self, results):
        iterator = iter(results)
        def ready():
            self.events.append('watch')
            value = next(iterator)
            if isinstance(value, Exception):
                raise value
            return value
        self.platform.emulator_start_watch = ready

    def test_failed_start_is_cleaned_before_retry_and_final_failure(self):
        self.watch([False, False, False])
        self.assertFalse(self.platform.emulator_start())
        self.assertEqual(self.events, ['stop', 'start', 'watch'] * 3 + ['stop'])

    def test_success_stops_retrying_and_leaves_healthy_instance_running(self):
        self.watch([False, True])
        self.assertTrue(self.platform.emulator_start())
        self.assertEqual(self.events, ['stop', 'start', 'watch'] * 2)

    def test_readiness_exception_is_bounded_and_cleaned(self):
        self.watch([RuntimeError('ADB failed')] * 3)
        self.assertFalse(self.platform.emulator_start())
        self.assertEqual(self.events, ['stop', 'start', 'watch'] * 3 + ['stop'])

    def test_cannot_verify_shutdown_prevents_any_launch(self):
        self.platform._emulator_function_wrapper = Mock(return_value=False)
        self.assertFalse(self.platform.emulator_start())
        self.platform._emulator_start.assert_not_called()
        self.platform._emulator_function_wrapper.assert_called_once_with(self.platform._emulator_stop)

    def test_stop_failure_does_not_launch_an_instance(self):
        self.platform._emulator_function_wrapper = Mock(return_value=False)
        self.assertFalse(self.platform.emulator_stop())
        self.platform._emulator_start.assert_not_called()
        self.assertEqual(self.platform._emulator_function_wrapper.call_count, 1)

    def test_timed_out_manager_is_reaped_then_actual_instance_is_checked(self):
        platform = isolated_platform_class()()
        command = Mock()
        command.wait.side_effect = [subprocess.TimeoutExpired('shutdown', 30), 0]
        platform.execute = Mock(return_value=command)
        platform._emulator_stop(self.platform.emulator_instance)
        command.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in command.wait.call_args_list], [30, 5])
        platform.guard_factory.return_value.ensure_stopped.assert_called_once()

    def test_shutdown_exit_code_alone_is_not_success(self):
        platform = isolated_platform_class()()
        platform.execute = Mock(return_value=SimpleNamespace(wait=lambda **kwargs: 0))
        platform.guard_factory.return_value.ensure_stopped.side_effect = RuntimeError('still running')
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            platform._emulator_stop(self.platform.emulator_instance)


if __name__ == '__main__':
    unittest.main()
