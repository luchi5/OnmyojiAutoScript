"""Recover a dropped admission input without reopening an observed series."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from tasks.DraftDuel.preflight import AdmissionOffer
from tasks.DraftDuel.workflow import DraftWorkflow


class StartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.worker = object.__new__(DraftWorkflow)
        self.worker.checkpoint_path = Path(self.folder.name)/'state.json'
        self.worker.pair = SimpleNamespace(leader='one', accounts=(SimpleNamespace(name='one'), SimpleNamespace(name='two')))
        self.worker.io, self.worker.session = Mock(), Mock()
        self.worker.peer_guard, self.worker.event = Mock(), Mock()
        self.costs = {'one': 300000, 'two': 100000}
        self.worker.recover_admission = Mock(return_value=(
            {o: AdmissionOffer(v, .99, True) for o, v in self.costs.items()}, (1190, 630)))

    def test_dropped_start_rechecks_price_and_retries_once_on_unopened_pair(self):
        self.worker.io.tap_point.side_effect = [OSError('input timeout'), None]
        self.assertFalse(self.worker._send_admission((1190, 630), self.costs, 1))
        self.assertTrue(self.worker._retry_unopened_admission({}, ['lobby_unopened']*2))
        self.assertEqual(self.worker.io.tap_point.call_count, 2)
        self.worker.recover_admission.assert_called_once()

    def test_one_account_advanced_never_repeats_start(self):
        self.worker._save_draft_checkpoint(self.costs, 1)
        self.assertFalse(self.worker._retry_unopened_admission({}, ['lobby_unopened', 'deploying']))
        self.worker.recover_admission.assert_not_called()
        self.worker.io.tap_point.assert_not_called()

    def test_observed_draft_disables_admission_retry_even_after_returning_lobby(self):
        self.worker._save_draft_checkpoint(self.costs, 1)
        self.worker._mark_draft_observed()
        self.assertFalse(self.worker._retry_unopened_admission({}, ['lobby_unopened']*2))
        self.worker.io.tap_point.assert_not_called()

    def test_retries_are_persisted_across_worker_recreation(self):
        self.worker._save_draft_checkpoint(self.costs, 3)
        self.assertFalse(self.worker._retry_unopened_admission({}, ['lobby_unopened']*2))
        self.worker.recover_admission.assert_not_called()


if __name__ == '__main__': unittest.main()
