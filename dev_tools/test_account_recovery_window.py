import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from module.device.account_session_recovery import claim_recovery
from dev_tools.test_account_session_guard import fake_script, fake_device, message_frame

class RecoveryPolicyTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  for account,serial in [('one','a'),('renamed','a'),('two','b')]:
   (self.root/(account+'.json')).write_text(json.dumps({'script':{'device':{'serial':serial}}}))
 def test_window_and_rename(self):
  self.assertTrue(claim_recovery('one',self.root,10000))
  self.assertFalse(claim_recovery('renamed',self.root,11000))
  self.assertFalse(claim_recovery('one',self.root,11800))
  self.assertTrue(claim_recovery('one',self.root,11801))
  self.assertFalse(claim_recovery('one',self.root,11802))
 def test_independent_accounts(self):
  self.assertTrue(claim_recovery('one',self.root,10000))
  self.assertTrue(claim_recovery('two',self.root,10001))
 def test_clock_rollback(self):
  claim_recovery('one',self.root,10000)
  self.assertFalse(claim_recovery('one',self.root,9000))
 def test_corrupt_record(self):
  claim_recovery('one',self.root,10000)
  next((self.root/'account_session_recovery').glob('*.json')).write_text('{}')
  with self.assertRaises(KeyError):claim_recovery('one',self.root,20000)
 def test_disabled_restart_still_allows_authorized_once(self):
  s=fake_script(fake_device(message_frame()))
  with patch('module.device.account_session_recovery.claim_recovery',return_value=True):
   self.assertFalse(s.run('KekkaiUtilize'))
  s._record_task_result('KekkaiUtilize',False)
  self.assertEqual(s.failure_record.get('KekkaiUtilize',0),0)
  self.assertEqual(s._get_next_task_with_recovery(),'Restart')
  self.assertTrue(s.run('Restart'))
  self.assertFalse(s.config.model.restart.scheduler.enable)
 def test_second_takeover_stops(self):
  s=fake_script(fake_device(message_frame()))
  with patch('module.device.account_session_recovery.claim_recovery',return_value=False):
   with self.assertRaises(SystemExit):s.run('KekkaiUtilize')
  self.assertFalse(s._account_session_restart_pending)
 def test_idle_does_not_reclaim(self):
  s=fake_script(fake_device(message_frame()))
  with patch('module.device.account_session_recovery.claim_recovery') as claim:
   with self.assertRaises(SystemExit):s.run('GotoMain')
   claim.assert_not_called()
 def test_ordinary_restart_remains_disabled(self):
  s=fake_script(fake_device())
  with self.assertRaises(SystemExit):s.run('Restart')
if __name__=='__main__':unittest.main()
