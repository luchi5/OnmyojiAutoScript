from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from module.device.idle_shutdown import stop_idle_emulator

class IdleShutdownTests(unittest.TestCase):
    def config(self):
        return NS(script=NS(device=NS(emulatorinfo_type='MuMuPlayer12',
            emulatorinfo_name='MuMuPlayer-12.0-0',emulatorinfo_path='C:/MuMu/nx_main/MuMuNxMain.exe')))

    @patch('module.device.idle_shutdown.subprocess.run')
    @patch('module.device.idle_shutdown.MuMuProcessGuard')
    def test_stopped_never_launches_any_command(self, factory, run):
        factory.return_value.processes.return_value=[]
        self.assertTrue(stop_idle_emulator(self.config(),Mock()))
        run.assert_not_called()

    @patch('module.device.idle_shutdown.subprocess.run')
    @patch('module.device.idle_shutdown.MuMuProcessGuard')
    def test_running_shuts_down_only_target_and_verifies(self,factory,run):
        guard=factory.return_value;guard.name='MuMuPlayer-12.0-0';guard.index='0'
        guard.processes.side_effect=[[object()],[]]
        self.assertTrue(stop_idle_emulator(self.config(),Mock()))
        args=run.call_args.args[0]
        self.assertEqual(args[1:],['control','-v','0','--version','12','shutdown'])
        guard.ensure_stopped.assert_called_once()

    @patch('module.device.idle_shutdown.subprocess.run')
    @patch('module.device.idle_shutdown.MuMuProcessGuard')
    def test_identity_failure_does_not_claim_stopped(self,factory,run):
        factory.side_effect=RuntimeError('identity mismatch')
        self.assertFalse(stop_idle_emulator(self.config(),Mock()))
        run.assert_not_called()

    @patch('module.device.idle_shutdown.subprocess.run')
    @patch('module.device.idle_shutdown.MuMuProcessGuard')
    def test_shutdown_failure_does_not_claim_stopped(self,factory,run):
        guard=factory.return_value;guard.name='MuMuPlayer-12.0-0';guard.index='0'
        guard.processes.return_value=[object()]
        guard.ensure_stopped.side_effect=RuntimeError('still running')
        self.assertFalse(stop_idle_emulator(self.config(),Mock()))

if __name__=='__main__':unittest.main()
