"""First-install behavior without starting a backend, device or download."""
import json
import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy import bootstrap
from deploy.config import ExecutionError
from deploy.pip import PipManager
from deploy.utils import poor_yaml_read


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'deploy').mkdir()
        (self.root / 'deploy/template').write_bytes((bootstrap.ROOT / 'deploy/template').read_bytes())
        (self.root / 'toolkit').mkdir()
        (self.root / 'requirements.txt').write_text('fake-package==1.0\n', encoding='utf-8')

    def test_fresh_zip_uses_own_repo_safe_defaults_and_client_port(self):
        self.assertTrue(bootstrap.seed_config(self.root))
        config = poor_yaml_read(str(self.root / 'config/deploy.yaml'))
        self.assertEqual(config['Repository'], 'https://github.com/luchi5/OnmyojiAutoScript.git')
        self.assertEqual(config['Branch'], 'luchi')
        self.assertEqual(config['WebuiPort'], 22289)
        self.assertEqual(config['WebuiHost'], '127.0.0.1')
        self.assertIsNone(config['Run'])
        self.assertIs(config['ReplaceAdb'], False)
        self.assertIs(config['InstallUiautomator2'], False)
        self.assertEqual(config['AdbExecutable'], './toolkit/adb/adb.exe')

    def test_existing_deploy_and_account_files_are_preserved_byte_for_byte(self):
        (self.root / 'config').mkdir()
        deploy = self.root / 'config/deploy.yaml'
        account = self.root / 'config/my-account.json'
        deploy.write_bytes(b'Deploy:\n  Git:\n    Branch: custom\n')
        account.write_bytes(b'{"private_setting":"unchanged"}')
        self.assertFalse(bootstrap.seed_config(self.root))
        self.assertEqual(deploy.read_bytes(), b'Deploy:\n  Git:\n    Branch: custom\n')
        self.assertEqual(account.read_bytes(), b'{"private_setting":"unchanged"}')

    def test_failed_install_does_not_create_completion_marker(self):
        manager = Mock()
        manager.pip_install.side_effect = ExecutionError('failed')
        with patch.object(bootstrap, 'PipManager', return_value=manager):
            with self.assertRaises(ExecutionError):
                bootstrap.install_dependencies(SimpleNamespace(InstallDependencies=True), self.root)
        self.assertFalse((self.root / 'toolkit/.oas-dependencies.json').exists())

    def test_cached_environment_is_checked_and_not_reinstalled(self):
        marker = self.root / 'toolkit/.oas-dependencies.json'
        marker.write_text(json.dumps({'fingerprint': bootstrap.dependency_fingerprint(self.root)}))
        with patch.object(bootstrap, 'requirements_installed', return_value=True), \
             patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
             patch.object(bootstrap, 'PipManager') as manager:
            bootstrap.install_dependencies(SimpleNamespace(), self.root)
        manager.assert_not_called()
        self.assertEqual(run.call_count, 1)

    def test_missing_pinned_dependency_invalidates_cached_environment(self):
        with patch.object(bootstrap.importlib.metadata, 'version',
                          side_effect=bootstrap.importlib.metadata.PackageNotFoundError):
            self.assertFalse(bootstrap.requirements_installed(self.root))
        with patch.object(bootstrap.importlib.metadata, 'version', return_value='2.0'):
            self.assertFalse(bootstrap.requirements_installed(self.root))
        with patch.object(bootstrap.importlib.metadata, 'version', return_value='1.0'):
            self.assertTrue(bootstrap.requirements_installed(self.root))

    def test_busy_backend_port_is_not_killed_or_replaced(self):
        with patch.object(bootstrap, 'port_is_busy', return_value=True), \
             patch.object(bootstrap.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(RuntimeError, '22289'):
                bootstrap.launch(SimpleNamespace(WebuiPort=22289))
        start.assert_not_called()

    def test_source_zip_setup_does_not_reset_or_remove_working_files(self):
        git = self.root / 'toolkit/git.exe'
        git.write_bytes(b'fake')
        config = SimpleNamespace(Branch='luchi', Repository='https://example.invalid/repo',
                                 filepath=lambda key: str(git))
        with patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run:
            bootstrap.connect_source(config, self.root)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any('reset' in command and '--mixed' in command for command in commands))
        self.assertFalse(any('--hard' in command or 'clean' in command for command in commands))

    def test_interrupted_fetch_is_retried_when_git_has_no_commit(self):
        (self.root / '.git').mkdir()
        git = self.root / 'toolkit/git.exe'
        git.write_bytes(b'fake')
        config = SimpleNamespace(Branch='luchi', Repository='https://example.invalid/repo',
                                 filepath=lambda key: str(git))
        with patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run:
            bootstrap.connect_source(config, self.root)
        self.assertTrue(any('fetch' in call.args[0] for call in run.call_args_list))

    def test_existing_git_checkout_is_not_updated_or_reset_by_bootstrap(self):
        (self.root / '.git').mkdir()
        git = self.root / 'toolkit/git.exe'
        git.write_bytes(b'fake')
        config = SimpleNamespace(filepath=lambda key: str(git))
        with patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            bootstrap.connect_source(config, self.root)
        self.assertEqual(run.call_count, 1)
        self.assertIn('rev-parse', run.call_args.args[0])

    def test_client_opens_only_after_this_backend_is_ready(self):
        client = self.root / 'oasx.exe'
        client.write_bytes(b'fake')
        backend = Mock()
        backend.poll.side_effect = [None, 0]
        backend.wait.return_value = 0
        with patch.object(bootstrap, 'CLIENT', client), \
             patch.object(bootstrap, 'port_is_busy', return_value=False), \
             patch.object(bootstrap.urllib.request, 'urlopen',
                          return_value=io.BytesIO(b'{"message":"test"}')) as request, \
             patch.object(bootstrap.subprocess, 'Popen', side_effect=[backend, Mock()]) as start:
            self.assertEqual(bootstrap.launch(SimpleNamespace(WebuiPort=22289), root=self.root), 0)
        self.assertEqual(start.call_count, 2)
        request.assert_called_once()
        self.assertEqual(start.call_args_list[1].args[0], [str(client)])
        backend.terminate.assert_not_called()

    def test_failed_backend_start_does_not_open_client(self):
        backend = Mock()
        backend.poll.return_value = 1
        with patch.object(bootstrap, 'port_is_busy', return_value=False), \
             patch.object(bootstrap.subprocess, 'Popen', return_value=backend) as start:
            with self.assertRaisesRegex(RuntimeError, '后台启动失败'):
                bootstrap.launch(SimpleNamespace(WebuiPort=22289), root=self.root)
        self.assertEqual(start.call_count, 1)
        backend.terminate.assert_not_called()


class PipFallbackTests(unittest.TestCase):
    def manager(self, outcomes, mirror='https://pypi.tuna.tsinghua.edu.cn/simple'):
        manager = object.__new__(PipManager)
        manager.PypiMirror = mirror
        manager.SSLVerify = True
        manager.python = r'C:\directory with spaces\python.exe'
        manager.outcomes = [SimpleNamespace(returncode=0 if success else 1) for success in outcomes]
        return manager

    def test_failed_mirror_retries_official_index_and_ignores_global_pip_config(self):
        manager = self.manager([False, True])
        with patch('deploy.pip.subprocess.run', side_effect=manager.outcomes) as run:
            manager.install_with_fallback(['adbutils==0.11.0'])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn('https://pypi.tuna.tsinghua.edu.cn/simple', commands[0])
        self.assertIn('https://pypi.org/simple', commands[1])
        self.assertTrue(all('--isolated' in command for command in commands))
        self.assertTrue(all('--trusted-host' not in command for command in commands))
        self.assertTrue(all(command[0] == manager.python for command in commands))

    def test_all_failures_propagate_instead_of_claiming_success(self):
        manager = self.manager([False, False])
        with patch('deploy.pip.subprocess.run', side_effect=manager.outcomes):
            with self.assertRaises(ExecutionError):
                manager.install_with_fallback(['adbutils==0.11.0'])


if __name__ == '__main__':
    unittest.main()
