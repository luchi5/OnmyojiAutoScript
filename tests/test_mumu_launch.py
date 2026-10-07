"""MuMu CLI routing tests with only two AST-extracted methods and fake processes.

No PlatformWindows imports, logger cleanup, Windows calls, ADB or emulator starts.
Run: toolkit/python.exe -B tests/test_mumu_launch.py
"""
import ast
import copy
import ntpath
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class EmulatorUnknown(Exception):
    pass


class Emulator:
    MuMuPlayer = "MuMuPlayer"
    MuMuPlayerX = "MuMuPlayerX"
    MuMuPlayer12 = "MuMuPlayer12"
    LDPlayerFamily = "LDPlayerFamily"
    NoxPlayerFamily = "NoxPlayerFamily"
    BlueStacks5 = "BlueStacks5"
    BlueStacks4 = "BlueStacks4"
    MEmuPlayer = "MEmuPlayer"
    single_to_console = Mock()


class FakeInstance:
    def __init__(self, path, index, version=12):
        self.emulator = SimpleNamespace(path=path)
        self.name = f"MuMuPlayer-{version}.0-{index}" if index is not None else "MuMuPlayer-unrecognized"
        self.MuMuPlayer12_id = index

    def __eq__(self, family):
        return family == Emulator.MuMuPlayer12


def isolated_platform_class():
    source = Path(__file__).resolve().parents[1] / "module/device/platform2/platform_windows.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    platform = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PlatformWindows")
    methods = [copy.deepcopy(node) for node in platform.body
               if isinstance(node, ast.FunctionDef) and node.name in {"_emulator_start", "_emulator_stop"}]
    if len(methods) != 2:
        raise AssertionError("Expected both emulator control methods")
    test_class = ast.ClassDef(name="IsolatedPlatform", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[test_class], type_ignores=[]))
    namespace = {"Emulator": Emulator, "EmulatorInstance": FakeInstance,
                 "EmulatorUnknown": EmulatorUnknown, "logger": Mock(),
                 "re": re, "os": os, "ntpath": ntpath, "Path": Path}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["IsolatedPlatform"]


class MuMuLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform_class = isolated_platform_class()

    def setUp(self):
        self.platform = self.platform_class()
        self.platform.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
            emulator_window_minimize=False, run_background_only=False)))
        self.process = Mock()
        self.platform.execute = Mock(return_value=self.process)
        self.platform.kill_process_by_regex = Mock(side_effect=AssertionError("must not kill unrelated processes"))
        Emulator.single_to_console.reset_mock()
        Emulator.single_to_console.return_value = r"C:\Synthetic MuMu\shell\MuMuManager.exe"

    def assert_command(self, expected, *, visible=False):
        self.platform.execute.assert_called_once_with(expected, show_window=visible)
        self.assertNotIn(" all ", expected)
        self.assertNotIn(" None", expected)

    def test_nxmain12_launch_targets_index_zero(self):
        instance = FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 0)
        self.platform._emulator_start(instance)
        self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 0 --version 12 launch')
        self.process.wait.assert_not_called()

    def test_nxmain12_launch_targets_index_eight_only(self):
        instance = FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 8)
        self.platform._emulator_start(instance)
        self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 8 --version 12 launch')

    def test_nxmain12_basename_is_case_insensitive(self):
        self.platform._emulator_start(FakeInstance(r"C:\Synthetic MuMu\shell\MUMUNXMAIN.EXE", 8))
        self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 8 --version 12 launch')

    def test_existing_mumu15_keeps_version15_control(self):
        instance = FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 8, version=15)
        self.platform._emulator_start(instance)
        self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 8 --version 15 launch')

    def test_legacy12_keeps_direct_player_launch(self):
        instance = FakeInstance(r"C:\Synthetic MuMu\shell\MuMuPlayer.exe", 0)
        self.platform._emulator_start(instance)
        self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuPlayer.exe" -v 0', visible=True)
        Emulator.single_to_console.assert_not_called()

    def test_non_nxmain12_does_not_gain_new_cli_route(self):
        instance = FakeInstance(r"C:\Synthetic MuMu\MuMuNxMain.exe.backup", 8)
        self.platform._emulator_start(instance)
        self.assert_command(r'"C:\Synthetic MuMu\MuMuNxMain.exe.backup" -v 8', visible=True)

    def test_nxmain12_shutdown_waits_for_single_target_completion(self):
        for index in (0, 8):
            with self.subTest(index=index):
                self.platform.execute.reset_mock()
                self.process.wait.reset_mock()
                self.platform._emulator_stop(FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", index))
                self.platform.execute.assert_called_once_with(
                    f'"C:\\Synthetic MuMu\\shell\\MuMuManager.exe" control -v {index} --version 12 shutdown', show_window=False)
                self.process.wait.assert_called_once_with(timeout=30)
                self.platform.kill_process_by_regex.assert_not_called()

    def test_mumu15_shutdown_preserves_version_and_wait(self):
        self.platform._emulator_stop(FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 8, version=15))
        self.platform.execute.assert_called_once_with(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 8 --version 15 shutdown', show_window=False)
        self.process.wait.assert_called_once_with(timeout=30)

    def test_legacy12_shutdown_preserves_api_command(self):
        self.platform._emulator_stop(FakeInstance(r"C:\Synthetic MuMu\shell\MuMuPlayer.exe", 0))
        self.platform.execute.assert_called_once_with(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" api -v 0 shutdown_player')

    def test_restart_sequence_cannot_launch_before_shutdown_wait_finishes(self):
        events = []
        instance = FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 8)
        def execute(command, **kwargs):
            events.append("shutdown" if command.endswith(" shutdown") else "launch")
            return SimpleNamespace(wait=lambda **kwargs: events.append("wait_complete"))
        self.platform.execute = execute
        self.platform._emulator_stop(instance)
        self.platform._emulator_start(instance)
        self.assertEqual(events, ["shutdown", "wait_complete", "launch"])

    def test_missing_instance_id_never_executes_launch_or_shutdown(self):
        for path in (r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", r"C:\Synthetic MuMu\shell\MuMuPlayer.exe"):
            for method in ("_emulator_start", "_emulator_stop"):
                with self.subTest(path=path, method=method):
                    self.platform.execute.reset_mock()
                    try:
                        getattr(self.platform, method)(FakeInstance(path, None))
                    except EmulatorUnknown:
                        pass
                    self.platform.execute.assert_not_called()
                    self.platform.kill_process_by_regex.assert_not_called()

    def test_manager_launch_never_shows_command_window(self):
        device = self.platform.config.script.device
        for minimize, background in ((True, False), (False, True), (True, True)):
            with self.subTest(minimize=minimize, background=background):
                self.platform.execute.reset_mock()
                device.emulator_window_minimize = minimize
                device.run_background_only = background
                self.platform._emulator_start(FakeInstance(r"C:\Synthetic MuMu\shell\MuMuNxMain.exe", 0))
                self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuManager.exe" control -v 0 --version 12 launch', visible=False)

    def test_legacy_launch_respects_minimize_and_background_visibility(self):
        device = self.platform.config.script.device
        for minimize, background in ((True, False), (False, True), (True, True)):
            with self.subTest(minimize=minimize, background=background):
                self.platform.execute.reset_mock()
                device.emulator_window_minimize = minimize
                device.run_background_only = background
                self.platform._emulator_start(FakeInstance(r"C:\Synthetic MuMu\shell\MuMuPlayer.exe", 0))
                self.assert_command(r'"C:\Synthetic MuMu\shell\MuMuPlayer.exe" -v 0', visible=False)


if __name__ == "__main__":
    unittest.main()
