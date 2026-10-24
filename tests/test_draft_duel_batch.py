"""Configured limits count settled series and survive restart boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tasks.DraftDuel.batch import SeriesBatch
from tasks.DraftDuel.workflow import read_admission_page


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def batch(self, target=3):
        return SeriesBatch(self.root/'batch.json', target, self.root/'checkpoint.json')

    def test_three_series_not_three_battles_and_no_fourth_admission(self):
        batch = self.batch()
        paths = []
        for i in range(3):
            self.assertTrue(batch.begin())
            paths.append(batch.checkpoint)
            self.assertEqual(batch.completed, i)
            batch.settle(dict(wins=0, losses=2, matches=2))
        self.assertEqual(len(set(paths)), 3)
        self.assertFalse(batch.begin())
        self.assertEqual(batch.completed, 3)
        self.assertEqual(sum(r['matches'] for r in batch.state['results']), 6)
        batch.finish()
        self.assertFalse(batch.pending)

    def test_restart_reuses_active_checkpoint_and_does_not_recount_previous_round(self):
        batch = self.batch()
        batch.begin(); batch.settle(dict(wins=6, losses=0))
        batch.begin()
        path = batch.checkpoint
        restored = self.batch()
        self.assertTrue(restored.pending)
        self.assertTrue(restored.begin())
        self.assertEqual(restored.checkpoint, path)
        self.assertEqual(restored.completed, 1)

    def test_restart_between_rounds_does_not_restore_completed_round(self):
        batch = self.batch()
        batch.begin()
        finished_path = batch.checkpoint
        batch.settle(dict(wins=2, losses=2))
        restored = self.batch()
        restored.begin()
        self.assertNotEqual(restored.checkpoint, finished_path)
        self.assertEqual(restored.completed, 1)

    def test_restart_after_last_settlement_only_waits_for_final_ack(self):
        batch = self.batch(1)
        batch.begin(); batch.settle(dict(wins=0, losses=2))
        restored = self.batch(1)
        self.assertTrue(restored.pending)
        self.assertFalse(restored.begin())
        restored.finish()
        self.assertFalse(self.batch(1).pending)

    def test_legacy_unfinished_round_is_counted_as_first_configured_series(self):
        batch = self.batch()
        batch.begin(adopt=True)
        self.assertEqual(batch.checkpoint, self.root/'checkpoint.json')
        batch.settle(dict(wins=1, losses=2))
        batch.begin(adopt=True)
        self.assertNotEqual(batch.checkpoint, self.root/'checkpoint.json')

    def test_unreadable_price_is_irrelevant_but_wrong_team_cannot_start(self):
        vision = Mock()
        vision.read.return_value = SimpleNamespace(kind='team_ready')
        vision.match.side_effect = [(200, 130), (1190, 630)]
        vision.ocr.side_effect = AssertionError('Price OCR must not gate admission')
        self.assertEqual(read_admission_page(vision, None, 'peer', 'self'), (True, (1190, 630)))
        vision.read.return_value = SimpleNamespace(kind='solo')
        self.assertEqual(read_admission_page(vision, None, 'peer', 'self'), (False, None))


if __name__ == '__main__': unittest.main()
