"""Offline installer tests: synthetic binaries, temporary paths, no ADB calls."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = Path(os.environ.get('OAS_ADB_TEST_SOURCE_ROOT', str(ROOT)))
SPEC = importlib.util.spec_from_file_location('official_adb_under_test', ROOT / 'deploy/official_adb.py')
adb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adb)  # Pure standard-library helper, no deploy logger/runtime.


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oas-adb-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'binaries'
        self.directory.mkdir()
        self.exe = self.directory / 'adb.exe'
        self.files = {name: ('new synthetic ' + name).encode() for name in adb.FILE_HASHES}
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}
        self.archive = self.make_archive(self.files)
        for key, value in [('FILE_HASHES', self.hashes), ('ARCHIVE_SIZE', len(self.archive)),
                           ('ARCHIVE_SHA256', hashlib.sha256(self.archive).hexdigest())]:
            patcher = patch.object(adb, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.download = patch.object(adb, '_download', return_value=self.archive).start()
        self.running = patch.object(adb, '_adb_running', return_value=False).start()
        self.addCleanup(patch.stopall)
        # Windows uses the actual Windows guard/rename on synthetic files. On
        # other systems fake only the Windows guard, preserving real file IO.
        if os.name != 'nt':
            @contextlib.contextmanager
            def fake_guards(paths):
                yield {p.name: (p.read_bytes() if p.exists() else None, adb._fingerprint(p)) for p in paths}
            patch.object(adb, '_binary_guards', fake_guards).start()

    def make_archive(self, files, version=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            for name, data in files.items():
                z.writestr('platform-tools/' + name, data)
            z.writestr('platform-tools/source.properties', 'Pkg.Revision=' + (version or adb.VERSION) + '\n')
            z.writestr('platform-tools/NOTICE.txt', 'Synthetic test license notice')
        return output.getvalue()

    def old_bundle(self):
        before = {}
        for name in self.files:
            data = ('old synthetic ' + name).encode()
            (self.directory / name).write_bytes(data)
            before[name] = data
        (self.directory / 'README.md').write_bytes(b'unrelated file preserved')
        return before

    def assert_bundle(self, files):
        for name, data in files.items():
            self.assertEqual((self.directory / name).read_bytes(), data)

    def test_upgrade_old_bundle_and_back_up_exact_preimage(self):
        old = self.old_bundle()
        self.assertEqual(adb.ensure_official_adb(self.exe), 'installed')
        self.assert_bundle(self.files)
        backups = list((self.directory / '.official-adb').glob('backup-*'))
        self.assertEqual(len(backups), 1)
        for name, data in old.items():
            self.assertEqual((backups[0] / name).read_bytes(), data)
        self.assertEqual(json.loads((backups[0] / 'application.json').read_text())['state'], 'applied')
        self.assertEqual((self.directory / 'README.md').read_bytes(), b'unrelated file preserved')
        self.assertFalse((self.directory / '.official-adb-install.lock').exists())

    def test_fresh_install_supplies_all_four_files(self):
        self.assertEqual(adb.ensure_official_adb(self.exe), 'installed')
        self.assert_bundle(self.files)

    def test_unicode_install_directory(self):
        self.directory = self.directory / '阴阳师😀'
        self.directory.mkdir()
        self.exe = self.directory / 'adb.exe'
        self.assertEqual(adb.ensure_official_adb(self.exe), 'installed')
        self.assert_bundle(self.files)

    def test_lock_finally_does_not_remove_replacement_owner(self):
        lock = self.directory / '.official-adb-install.lock'
        with adb._install_lock(self.directory):
            lock.write_text('{"owner_token":"replacement-owner"}')
        self.assertIn('replacement-owner', lock.read_text())

    def test_exact_bundle_needs_no_download_or_process_inventory(self):
        for name, data in self.files.items():
            (self.directory / name).write_bytes(data)
        self.assertEqual(adb.ensure_official_adb(self.exe), 'already_current')
        self.download.assert_not_called()
        self.running.assert_not_called()

    def test_dependency_reinstallation_is_corrected_on_next_install(self):
        adb.ensure_official_adb(self.exe)
        self.old_bundle()  # Mimic pip restoring the dependency's legacy bundle.
        self.download.reset_mock()
        self.assertEqual(adb.ensure_official_adb(self.exe), 'installed')
        self.assert_bundle(self.files)
        self.download.assert_not_called()  # Verified pinned archive is reusable.

    def test_checksum_failure_leaves_original_binaries(self):
        old = self.old_bundle()
        self.download.return_value = self.archive[:-1] + b'!'
        with self.assertRaises(adb.OfficialAdbError):
            adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)

    def test_download_failure_leaves_original_binaries(self):
        old = self.old_bundle()
        self.download.side_effect = OSError('synthetic network failure')
        with self.assertRaises(OSError):
            adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)
        self.assertFalse((self.directory / '.official-adb-install.lock').exists())

    def test_running_adb_blocks_upgrade_without_mutation(self):
        old = self.old_bundle()
        self.running.return_value = True
        with self.assertRaisesRegex(adb.OfficialAdbError, 'ADB is running'):
            adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)
        self.assertFalse(list((self.directory / '.official-adb').glob('backup-*')))

    def test_install_lock_conflict_preserves_other_owner(self):
        old = self.old_bundle()
        lock = self.directory / '.official-adb-install.lock'
        lock.write_text('{"owner_token":"other-owner"}')
        with self.assertRaises(adb.OfficialAdbError):
            adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)
        self.assertIn('other-owner', lock.read_text())
        self.download.assert_not_called()

    def test_dll_write_failure_rolls_back_before_executable_is_changed(self):
        old = self.old_bundle()
        real_write = adb._write
        def failing_write(path, data, **kwargs):
            if Path(path) == self.directory / 'AdbWinUsbApi.dll' and data == self.files['AdbWinUsbApi.dll']:
                raise PermissionError('synthetic locked DLL')
            return real_write(path, data, **kwargs)
        with patch.object(adb, '_write', side_effect=failing_write):
            with self.assertRaises(adb.OfficialAdbError):
                adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)
        journal = next((self.directory / '.official-adb').glob('backup-*/application.json'))
        self.assertEqual(json.loads(journal.read_text())['state'], 'rolled_back')

    def test_wrong_archive_version_or_member_is_rejected(self):
        for files, version in [(self.files, '29.0.6'), ({'adb.exe': b'bad'}, adb.VERSION)]:
            with self.subTest(version=version, names=list(files)):
                data = self.make_archive(files, version)
                with patch.object(adb, 'ARCHIVE_SIZE', len(data)), patch.object(adb, 'ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()):
                    with self.assertRaises(adb.OfficialAdbError):
                        adb._unpack(data)

    def test_member_checksum_rejects_incomplete_binary_set(self):
        files = dict(self.files)
        files['libwinpthread-1.dll'] = b'bad DLL'
        data = self.make_archive(files)
        with patch.object(adb, 'ARCHIVE_SIZE', len(data)), patch.object(adb, 'ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(adb.OfficialAdbError, 'checksum'):
                adb._unpack(data)

    def test_later_target_edit_is_preserved_and_rollback_reported(self):
        self.old_bundle()
        real_write = adb._write
        def intervening_write(path, data, **kwargs):
            result = real_write(path, data, **kwargs)
            if Path(path) == self.directory / 'AdbWinApi.dll' and data == self.files['AdbWinApi.dll']:
                (self.directory / 'AdbWinApi.dll').write_bytes(b'user later change')
                raise OSError('synthetic interruption after edit')
            return result
        with patch.object(adb, '_write', side_effect=intervening_write):
            with self.assertRaises(adb.OfficialAdbError):
                adb.ensure_official_adb(self.exe)
        self.assertEqual((self.directory / 'AdbWinApi.dll').read_bytes(), b'user later change')
        journal = next((self.directory / '.official-adb').glob('backup-*/application.json'))
        self.assertEqual(json.loads(journal.read_text())['state'], 'rollback_incomplete')

    def test_hardlinked_target_is_rejected(self):
        self.old_bundle()
        os.link(self.exe, self.directory / 'shared.exe')
        with self.assertRaises(adb.OfficialAdbError):
            adb.ensure_official_adb(self.exe)
        self.download.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows concurrent rename test')
    def test_concurrent_rename_during_preimage_read_is_preserved(self):
        self.old_bundle()
        target = self.directory / 'AdbWinApi.dll'
        marker = b'operator changed file while the old inode was read'
        real_fingerprint, real_replace = adb._fingerprint, adb._replace
        injected = False
        def fingerprint(path):
            nonlocal injected
            caller = inspect.currentframe().f_back
            while caller is not None and caller.f_code.co_name != '_binary_guards':
                caller = caller.f_back
            if Path(path) == target and caller is not None and 'chunks' in caller.f_locals and not injected:
                injected = True
                user_file = self.directory / 'operator-change.tmp'
                user_file.write_bytes(marker)
                real_replace(user_file, target)
            return real_fingerprint(path)
        with patch.object(adb, '_fingerprint', side_effect=fingerprint):
            with self.assertRaises(adb.OfficialAdbError): adb.ensure_official_adb(self.exe)
        self.assertTrue(injected)
        self.assertEqual(target.read_bytes(), marker)

    @unittest.skipUnless(os.name == 'nt', 'Windows concurrent rename test')
    def test_concurrent_rename_during_staging_is_preserved(self):
        self.old_bundle()
        target = self.directory / 'AdbWinApi.dll'
        marker = b'operator changed file while the candidate was staged'
        real_safe, real_replace = adb._safe, adb._replace
        injected = False
        def safe(path, file=False):
            nonlocal injected
            result = real_safe(path, file=file)
            caller = inspect.currentframe().f_back
            while caller is not None and caller.f_code.co_name != '_write':
                caller = caller.f_back
            temporary = caller.f_locals.get('temporary') if caller is not None else None
            if Path(path) == target and caller is not None and temporary is not None and temporary.exists() and not injected:
                injected = True
                user_file = self.directory / 'operator-stage.tmp'
                user_file.write_bytes(marker)
                real_replace(user_file, target)
            return result
        with patch.object(adb, '_safe', side_effect=safe):
            with self.assertRaises(adb.OfficialAdbError): adb.ensure_official_adb(self.exe)
        self.assertTrue(injected)
        self.assertEqual(target.read_bytes(), marker)

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing test')
    def test_open_target_blocks_all_binary_changes(self):
        old = self.old_bundle()
        with self.exe.open('rb'):
            with self.assertRaisesRegex(adb.OfficialAdbError, 'in use'):
                adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)

    def test_cancelled_or_corrupt_cached_archive_does_not_fall_back(self):
        old = self.old_bundle()
        cache = self.directory / '.official-adb'
        cache.mkdir()
        (cache / ('platform-tools-' + adb.VERSION + '.zip')).write_bytes(b'corrupt')
        with self.assertRaises(adb.OfficialAdbError):
            adb.ensure_official_adb(self.exe)
        self.assert_bundle(old)
        self.download.assert_not_called()


class IntegrationTests(unittest.TestCase):
    def test_installer_keeps_dependency_then_adb_order(self):
        tree = ast.parse((INTEGRATION_ROOT / 'deploy/installer.py').read_text())
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertLess(calls.index('pip_install'), calls.index('adb_install'))

    def test_adb_install_upgrade_precedes_any_device_connections(self):
        # Execute only the actual method AST with fakes, avoiding imports of
        # deploy logger, emulator/device libraries or real configuration.
        tree = ast.parse((INTEGRATION_ROOT / 'deploy/adb.py').read_text())
        method = next(n for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == 'AdbManager'
                      for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'adb_install')
        events = []
        class Logger:
            def hr(self, *args): pass
            def info(self, *args): pass
            def critical(self, *args): pass
        class Emulator:
            def __init__(self, **kwargs): events.append('connect')
            def brute_force_connect(self): events.append('auto_connect')
        class Config:
            root_filepath = 'C:/private-test-root'
            adb = os.path.join(root_filepath, 'toolkit', 'Lib', 'site-packages', 'adbutils', 'binaries', 'adb.exe')
            ReplaceAdb = False
            AutoConnect = True
            InstallUiautomator2 = False
        fake_os = type('FakeOS', (), {'name': 'nt', 'path': os.path})
        namespace = {'os': fake_os, 'logger': Logger(), 'EmulatorConnect': Emulator, 'ExecutionError': RuntimeError}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual adb_install>', 'exec'), namespace)
        with patch.dict('sys.modules', {'deploy.official_adb': adb}), patch.object(adb, 'ensure_official_adb', side_effect=lambda p: events.append('upgrade') or 'installed'):
            namespace['adb_install'](Config())
        self.assertEqual(events, ['upgrade', 'connect', 'auto_connect'])
        events.clear()
        with patch.dict('sys.modules', {'deploy.official_adb': adb}), patch.object(adb, 'ensure_official_adb', side_effect=adb.OfficialAdbError('bad archive')):
            with self.assertRaises(RuntimeError): namespace['adb_install'](Config())
        self.assertEqual(events, [])  # Never use the old executable after failure.
        config = Config()
        config.ReplaceAdb = True
        with patch.dict('sys.modules', {'deploy.official_adb': adb}), patch.object(adb, 'ensure_official_adb') as ensure:
            with self.assertRaises(RuntimeError): namespace['adb_install'](config)
            ensure.assert_not_called()
        self.assertEqual(events, [])  # Never copy a new EXE into an old DLL set.
        events.clear()
        config = Config()
        config.adb = 'C:/operator-selected/adb.exe'
        with patch.dict('sys.modules', {'deploy.official_adb': adb}), patch.object(adb, 'ensure_official_adb') as ensure:
            namespace['adb_install'](config)
            ensure.assert_not_called()
        self.assertEqual(events, ['connect', 'auto_connect'])


if __name__ == '__main__':
    unittest.main()
