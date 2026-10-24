"""Real SQLite ownership with simulated clocks; no game or device calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tasks.DraftDuel.config import DraftDuelConfig
from tasks.DraftDuel.preflight import AccountConfig, PairConfig
from tasks.DraftDuel.session import LeaseBusy, SessionError, StaleState
from tasks.DraftDuel.workflow import DraftWorkflow


class PeerRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1000.
        self.on_sleep = lambda: None
        self.sleeps = []
        settings = DraftDuelConfig(user_status='leader', partner_config='nine',
                                   teammate_name='member', partner_wait_seconds=10)
        partner = DraftDuelConfig(user_status='member', partner_config='one',
                                  teammate_name='leader', partner_wait_seconds=10)
        self.pair = PairConfig((AccountConfig('one', 'port1', 'emu1', settings),
                               AccountConfig('nine', 'port9', 'emu9', partner)))
        self.events = []
        for target, replacement in [('time', self.wall), ('monotonic', lambda: self.now),
                                    ('sleep', self.sleep)]:
            mocked = patch('tasks.DraftDuel.workflow.time.'+target, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.leader, self.member = self.worker('one'), self.worker('nine')
        self.leader.session.acquire_lease()
        self.leader.session.open_session()
        self.leader.session.set_workflow('series')
        self.leader.publish('series_control')
        self.member.session.acquire_lease()
        self.member.session.open_session()
        self.member.publish('member_waiting')

    def wall(self):
        self.now += .001
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()

    def worker(self, name):
        worker = DraftWorkflow(self.pair, name, Mock(), Mock(), self.root,
                               event=lambda r, image=None: self.events.append(r))
        worker.session.clock = self.wall
        return worker

    def test_leader_restart_reuses_live_member_session_for_pending_series(self):
        prior = self.leader.session.session_id
        self.leader.session.release_lease()
        restored = self.worker('one')
        with patch.object(restored, 'has_pending_series', return_value=True):
            restored.join()
        self.assertEqual(restored.session.session_id, prior)
        self.member.publish('member_waiting')
        self.assertEqual(self.member.session.workflow()['phase'], 'series')
        restored.peer_guard()
        self.assertEqual(self.member.session.status()['status'], 'running')

    def test_transient_stale_peer_republishes_then_recovers(self):
        self.now += 16
        self.on_sleep = lambda: self.member.publish('member_waiting')
        self.leader.peer_guard()
        self.assertTrue(self.sleeps)
        self.assertTrue(hasattr(self.leader, 'last_peer_recovery'))
        self.assertIn('pair_connection_recovered', [r['phase'] for r in self.events])
        self.assertEqual(self.leader.session.status()['status'], 'running')

    def test_recovering_own_expired_lease_does_not_need_manual_restart(self):
        self.now += 31
        self.member.session.acquire_lease()
        self.member.publish('member_waiting')
        self.leader.peer_guard()
        self.assertEqual(self.leader.session.status()['status'], 'running')
        self.assertTrue(hasattr(self.leader, 'last_peer_recovery'))

    def test_different_live_worker_ownership_is_not_stolen(self):
        self.now += 31
        replacement = self.worker('one')
        replacement.session.acquire_lease()
        with self.assertRaises(LeaseBusy):
            self.leader.peer_guard()

    def test_explicit_stop_exits_without_waiting_for_recovery(self):
        self.member.session.request_stop('user stopped task')
        with self.assertRaisesRegex(SessionError, '停止'):
            self.leader.peer_guard()
        self.assertFalse(self.sleeps)

    def test_permanent_missing_peer_has_bounded_connection_wait(self):
        self.now += 16
        started = self.now
        with self.assertRaises(StaleState):
            self.leader.peer_guard()
        self.assertGreaterEqual(self.now-started, 10)
        self.assertLess(self.now-started, 12)
        self.leader.io.tap_point.assert_not_called()

    def test_follow_remains_alive_beyond_an_hour_until_leader_completes(self):
        def later():
            self.now += 3701
            self.leader.session.acquire_lease()
            self.leader.publish('series_control')
            self.leader.session.set_workflow('complete', {'wins': 6, 'losses': 1})
        self.on_sleep = later
        self.assertTrue(self.member.follow())
        self.assertGreater(self.now, 4700)
        self.member.io.capture.assert_not_called()
        observation = self.leader.session.snapshots(require_fresh=False)['nine']
        self.assertEqual(observation['payload']['stage'], 'complete_seen')

    def test_follow_can_rejoin_replaced_session_and_acknowledge_completion(self):
        prior = self.member.session.session_id
        self.leader.session.request_stop('replace stale session')
        self.leader.session.finish_session()
        self.leader.session.open_session(new=True)
        self.leader.session.set_workflow('series')
        self.leader.publish('series_control')
        self.on_sleep = lambda: self.leader.session.set_workflow('complete')
        self.assertTrue(self.member.follow())
        self.assertNotEqual(self.member.session.session_id, prior)
        self.assertEqual(self.member.session.session_id, self.leader.session.session_id)
        self.assertIn('member_session_rejoined', [r['phase'] for r in self.events])

    def test_follow_exits_on_explicit_leader_stop(self):
        self.leader.session.request_stop('user stopped task')
        with self.assertRaisesRegex(SessionError, '停止'):
            self.member.follow()
        self.assertFalse(self.sleeps)


if __name__ == '__main__':
    unittest.main()
