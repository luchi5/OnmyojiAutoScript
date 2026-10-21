"""Isolated MuMu shutdown guard tests; never inspect or kill real processes.

Run: toolkit/python.exe -B tests/test_mumu_process.py
"""
import importlib.util
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "module/device/platform2/mumu_process.py"
SPEC = importlib.util.spec_from_file_location("isolated_mumu_process", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MuMuProcessGuardTests(unittest.TestCase):
    NAME = "MuMuPlayer-12.0-6"
    UUID = "12345678-1234-5678-9abc-123456789abc"

    def setUp(self):
        self.directory = TemporaryDirectory(prefix="mumu-process-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.launcher = self.root / "shell" / "MuMuNxMain.exe"
        self.config = self.root / "vms" / self.NAME / f"{self.NAME}.nemu"
        self.config.parent.mkdir(parents=True)
        self.write_identity()
        self.logger = Mock()
        self.guard = MODULE.MuMuProcessGuard(self.NAME, self.launcher, self.logger)
        self.live = []
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        # Even an accidentally added enumeration in the guard is isolated.
        self.process_iter = self.patches.enter_context(patch.object(
            MODULE.psutil, "process_iter", side_effect=lambda: list(self.live)))
        self.clock = 0.0

        def sleep(seconds):
            self.clock += seconds

        self.time = SimpleNamespace(monotonic=lambda: self.clock, sleep=Mock(side_effect=sleep))
        self.patches.enter_context(patch.object(MODULE, "time", self.time))

    def write_identity(self, *, name=NAME, uuid=UUID):
        self.config.write_text(
            f'<VirtualBox xmlns="http://www.innotek.de/VirtualBox-settings">'
            f'<Machine name="{name}" uuid="{{{uuid}}}" />'
            f'</VirtualBox>', encoding="utf-8")

    def process(self, name="MuMuNxDevice.exe", *, vm=NAME, index="6", uuid=UUID,
                root=None, pid=600, args=None):
        root = self.root if root is None else root
        exe = str(Path(root) / "shell" / name)
        if args is None:
            args = [exe, "--vm", vm, "-v", index] if name.lower() == "mumunxdevice.exe" else [
                exe, "--comment", vm, "--startvm", uuid]
        process = Mock()
        process.pid = pid
        process.name.return_value = name
        process.exe.return_value = exe
        process.cmdline.return_value = args
        return process

    def test_constructor_reads_namespaced_vm_identity(self):
        self.assertEqual(self.guard.name, self.NAME)
        self.assertEqual(self.guard.index, "6")
        self.assertEqual(self.guard.root, self.root)
        self.assertEqual(self.guard.uuid, self.UUID)

    def test_missing_or_invalid_vm_identity_refuses_construction(self):
        variants = (
            "<VirtualBox />",
            f'<VirtualBox><Machine name="MuMuPlayer-12.0-16" uuid="{self.UUID}" /></VirtualBox>',
            f'<VirtualBox><Machine name="MuMuPlayer-15.0-6" uuid="{self.UUID}" /></VirtualBox>',
            f'<VirtualBox><Machine name="{self.NAME}" /></VirtualBox>',
            f'<VirtualBox><Machine name="{self.NAME}" uuid="not-a-uuid" /></VirtualBox>',
            "invalid XML",
        )
        for xml in variants:
            with self.subTest(xml=xml):
                self.config.write_text(xml, encoding="utf-8")
                with self.assertRaises((RuntimeError, KeyError, ValueError, MODULE.ET.ParseError)):
                    MODULE.MuMuProcessGuard(self.NAME, self.launcher, self.logger)
        self.config.unlink()
        with self.assertRaises(FileNotFoundError):
            MODULE.MuMuProcessGuard(self.NAME, self.launcher, self.logger)
        self.process_iter.assert_not_called()

    def test_invalid_instance_name_refuses_construction(self):
        for name in ("MuMuPlayer", "MuMuPlayer-12.0-6-suffix", "MuMuPlayer-13.0-6"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                MODULE.MuMuProcessGuard(name, self.launcher, self.logger)

    def test_nxdevice_requires_exact_index_name_engine_and_installation_root(self):
        self.assertTrue(self.guard.matches(self.process()))
        variants = (
            {"vm": "MuMuPlayer-12.0-16", "index": "16"},
            {"vm": "MuMuPlayer-15.0-6"},
            {"vm": self.NAME + "-suffix"},
            {"root": Path(str(self.root) + "-other")},
            {"root": self.root.parent / "unrelated-install"},
        )
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.guard.matches(self.process(**kwargs)))

    def test_real_nxdevice_vm_only_command_is_valid(self):
        exe = str(self.root / "shell" / "MuMuNxDevice.exe")
        self.assertTrue(self.guard.matches(self.process(args=[exe, '--vm', self.NAME])))
        self.assertFalse(self.guard.matches(self.process(args=[exe, '--vm', 'MuMuPlayer-12.0-16'])))

    def test_nxdevice_partial_or_duplicate_identity_refuses_recovery(self):
        exe = str(self.root / "shell" / "MuMuNxDevice.exe")
        for args in (
            [exe, "-v", "6"],
            [exe, "--vm", self.NAME, "-v"],
            [exe, "--vm", self.NAME, "-v", "6", "-v", "16"],
            [exe, "--vm", self.NAME, "--vm", self.NAME, "-v", "6"],
        ):
            with self.subTest(args=args):
                process = self.process(args=args)
                self.live = [process]
                with self.assertRaisesRegex(RuntimeError, "Incomplete identity"):
                    self.guard.ensure_stopped(grace=0, kill_timeout=0)
                process.kill.assert_not_called()
                self.logger.info.assert_not_called()

    def test_nxdevice_conflicting_target_index_refuses_recovery(self):
        process = self.process(index="16")
        self.live = [process]
        with self.assertRaisesRegex(RuntimeError, "Conflicting identity"):
            self.guard.ensure_stopped(grace=0, kill_timeout=0)
        process.kill.assert_not_called()
        self.logger.info.assert_not_called()

    def test_headless_requires_complete_name_and_exact_uuid(self):
        self.assertTrue(self.guard.matches(self.process("MuMuVMMHeadless.exe")))
        self.assertTrue(self.guard.matches(self.process(
            "MuMuVMMHeadless.exe", uuid="{" + self.UUID.upper() + "}")))
        for kwargs in (
            {"vm": "MuMuPlayer-12.0-16"},
            {"vm": "MuMuPlayer-15.0-6"},
            {"vm": self.NAME + "-suffix"},
            {"uuid": "87654321-1234-5678-9abc-123456789abc"},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.guard.matches(self.process("MuMuVMMHeadless.exe", **kwargs)))

    def test_headless_invalid_uuid_for_target_refuses_recovery(self):
        process = self.process("MuMuVMMHeadless.exe", uuid=self.UUID + "-suffix")
        self.live = [process]
        with self.assertRaisesRegex(RuntimeError, "Invalid identity"):
            self.guard.ensure_stopped(grace=0, kill_timeout=0)
        process.kill.assert_not_called()
        self.logger.info.assert_not_called()

    def test_headless_partial_or_duplicate_identity_refuses_recovery(self):
        exe = "MuMuVMMHeadless.exe"
        for args in (
            [exe, "--comment", self.NAME],
            [exe, "--comment", self.NAME, "--startvm"],
            [exe, "--comment", self.NAME, "--startvm", ""],
            [exe, "--comment", self.NAME, "--startvm", self.UUID, "--startvm", self.UUID],
            [exe, "--startvm", self.UUID],
            [exe, "--startvm", self.UUID, "--comment"],
            [exe, "--startvm", self.UUID, "--comment", self.NAME, "--comment", self.NAME],
        ):
            with self.subTest(args=args):
                process = self.process(exe, args=args)
                self.live = [process]
                with self.assertRaisesRegex(RuntimeError, "Incomplete identity"):
                    self.guard.ensure_stopped(grace=0, kill_timeout=0)
                process.kill.assert_not_called()
                self.logger.info.assert_not_called()

    def test_shared_services_adb_manager_and_other_executables_never_match(self):
        for name in ("MuMuManager.exe", "MuMuNxMain.exe", "MuMuService.exe", "adb.exe",
                     "MuMuVMMManage.exe", "MuMuNxDevice.exe.backup", "python.exe"):
            with self.subTest(name=name):
                process = self.process(name)
                self.assertFalse(self.guard.matches(process))
                process.cmdline.assert_not_called()
                process.kill.assert_not_called()
        spoof = self.process()
        spoof.exe.return_value = str(self.root / "shell" / "unrelated.exe")
        self.assertFalse(self.guard.matches(spoof))

    def test_empty_command_line_cannot_be_treated_as_proof_of_exit(self):
        self.live = [self.process(args=[])]
        with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
            self.guard.ensure_stopped(grace=0)
        self.live[0].kill.assert_not_called()

    def test_graceful_exit_does_not_kill(self):
        target = self.process()
        self.process_iter.side_effect = [[target], []]
        self.guard.ensure_stopped(grace=1)
        target.kill.assert_not_called()
        self.assertEqual(self.process_iter.call_count, 2)
        self.time.sleep.assert_called_once_with(0.25)

    def test_stuck_processes_kill_only_target_and_verify_exit(self):
        target = self.process()
        headless = self.process("MuMuVMMHeadless.exe", pid=601)
        unrelated = [self.process(vm="MuMuPlayer-12.0-16", index="16", pid=1600),
                     self.process(vm="MuMuPlayer-15.0-6", pid=1506),
                     self.process("MuMuManager.exe", pid=100),
                     self.process("MuMuService.exe", pid=101),
                     self.process("adb.exe", pid=102)]
        self.live = [target, headless] + unrelated
        target.kill.side_effect = lambda: self.live.remove(target)
        headless.kill.side_effect = lambda: self.live.remove(headless)
        self.guard.ensure_stopped(grace=0, kill_timeout=1)
        target.kill.assert_called_once_with()
        headless.kill.assert_called_once_with()
        for process in unrelated:
            process.kill.assert_not_called()
        self.assertEqual(self.live, unrelated)
        self.assertGreaterEqual(self.process_iter.call_count, 3)
        self.assertIn("processes have exited", self.logger.info.call_args.args[0])

    def test_identity_is_rechecked_before_kill(self):
        target = self.process()
        original = target.cmdline.return_value
        changed = self.process(vm="MuMuPlayer-12.0-16", index="16").cmdline.return_value
        target.cmdline.side_effect = [original, original, changed, changed]
        self.live = [target]
        self.guard.ensure_stopped(grace=0, kill_timeout=0)
        target.kill.assert_not_called()

    def test_kill_failure_or_access_denied_is_not_reported_as_success(self):
        for error in (OSError("kill failed"), MODULE.psutil.AccessDenied(600)):
            with self.subTest(error=error):
                target = self.process()
                target.kill.side_effect = error
                self.live = [target]
                self.logger.reset_mock()
                with self.assertRaises(type(error)):
                    self.guard.ensure_stopped(grace=0, kill_timeout=0)
                target.kill.assert_called_once_with()
                self.logger.info.assert_not_called()

    def test_process_surviving_kill_refuses_another_launch(self):
        target = self.process()
        self.live = [target]
        with self.assertRaisesRegex(RuntimeError, "refusing another launch"):
            self.guard.ensure_stopped(grace=0, kill_timeout=0.5)
        target.kill.assert_called_once_with()
        self.logger.info.assert_not_called()
        self.assertEqual(self.clock, 0.5)

    def test_inaccessible_process_identity_fails_closed(self):
        target = self.process()
        target.cmdline.side_effect = MODULE.psutil.AccessDenied(target.pid)
        self.live = [target]
        with self.assertRaisesRegex(RuntimeError, "Cannot verify MuMu processes"):
            self.guard.ensure_stopped(grace=0)
        target.kill.assert_not_called()
        self.logger.info.assert_not_called()

    def test_access_denied_while_process_exits_is_rechecked(self):
        target = self.process()
        target.cmdline.side_effect = MODULE.psutil.AccessDenied(target.pid)
        target.is_running.return_value = False
        self.live = [target]
        self.guard.ensure_stopped(grace=0)
        target.kill.assert_not_called()
        self.time.sleep.assert_called_once_with(0.25)

    def test_transient_access_denied_retries_same_identity(self):
        target = self.process(vm='MuMuPlayer-12.0-16', index='16')
        original = target.cmdline.return_value
        target.cmdline.side_effect = [MODULE.psutil.AccessDenied(target.pid), original]
        target.is_running.return_value = True
        self.live = [target]
        self.guard.ensure_stopped(grace=0)
        target.kill.assert_not_called()
        self.assertEqual(target.cmdline.call_count, 2)

    def test_disappeared_process_during_inspection_is_ignored(self):
        target = self.process()
        target.name.side_effect = MODULE.psutil.NoSuchProcess(target.pid)
        self.live = [target]
        self.guard.ensure_stopped(grace=0)
        target.kill.assert_not_called()

    def test_disappeared_process_during_kill_is_verified_afterward(self):
        target = self.process()
        self.live = [target]

        def disappear():
            self.live.remove(target)
            raise MODULE.psutil.NoSuchProcess(target.pid)

        target.kill.side_effect = disappear
        self.guard.ensure_stopped(grace=0, kill_timeout=0)
        target.kill.assert_called_once_with()
        self.assertGreaterEqual(self.process_iter.call_count, 3)
        self.assertIn("processes have exited", self.logger.info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
