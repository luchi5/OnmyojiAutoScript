"""Roster OCR/transport failures recover on new frames without live devices."""
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tasks.DraftDuel.config import DraftDuelConfig
from tasks.DraftDuel.round_model import RoundView
from tasks.DraftDuel.round_runtime import SeriesRuntime


class BlankOCR:
    def __call__(self, image):
        return '', 0

    def close(self):
        pass


class RosterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.events = []
        self.devices = {owner: Mock() for owner in ('one', 'two')}
        self.runtime = SeriesRuntime(self.devices, 'one', DraftDuelConfig(),
            clock=lambda: self.now, sleep=lambda seconds: None,
            event=lambda record, image=None: self.events.append(record),
            ocr_factory=BlankOCR, vision_factory=lambda ocr: Mock())
        self.addCleanup(self.runtime.close)
        # Distinct actual pixels allow us to check which frame supplies fallback.
        self.image = np.indices((720, 1280))[1].astype(np.uint8)
        self.image = np.repeat(self.image[:, :, None], 3, axis=2)
        self.runtime.images = {owner: self.image.copy() for owner in self.devices}
        self.runtime.lobbies = {owner: self.image.copy() for owner in self.devices}

    def views(self, phase='roster_detail', owners=('one', 'two'), captured=None):
        action = 'close_roster' if phase == 'roster_detail' else 'open_roster'
        return {owner: RoundView(phase, self.now if captured is None else captured,
                                {action: (502, 60)}) for owner in owners}

    def test_one_ocr_failure_retries_and_other_account_continues(self):
        from tasks.DraftDuel.lineup_runtime import read_roster as actual_read

        def fail_one(detail, lobby, owner, ocr, **kwargs):
            if owner == 'one':
                raise RuntimeError('transient OCR failure')
            return actual_read(detail, lobby, owner, ocr, **kwargs)

        with patch('tasks.DraftDuel.round_runtime.read_roster', side_effect=fail_one):
            self.runtime.prepare(self.views())
        self.assertNotIn('one', self.runtime.rosters)
        self.assertIn('two', self.runtime.rosters)
        self.devices['one'].tap_point.assert_not_called()
        self.devices['two'].tap_point.assert_called_once()
        self.now += 3
        self.runtime.prepare(self.views())
        self.assertEqual(set(self.runtime.lineups), {'one', 'two'})
        self.assertTrue(any(e['phase'] == 'roster_read_retry' for e in self.events))

    def test_four_fresh_ocr_failures_use_observed_portraits(self):
        with patch('tasks.DraftDuel.round_runtime.read_roster', side_effect=RuntimeError('OCR unavailable')) as read:
            for attempt in range(4):
                self.now += 3
                self.runtime.prepare(self.views(owners=('one',)))
                if attempt < 3:
                    self.assertNotIn('one', self.runtime.rosters)
            self.assertEqual(read.call_count, 4)
        roster = self.runtime.rosters['one']
        self.assertEqual(len(roster), 5)
        for index, candidate in enumerate(roster):
            self.assertTrue(candidate.name.startswith('未识别头像:one:'))
            x = 470+86*index
            np.testing.assert_array_equal(self.runtime.portraits['one'][candidate.name],
                                          self.image[39:81, x+5:x+56])
        self.devices['one'].tap_point.assert_called_once()

    def test_same_capture_does_not_exhaust_ocr_retries(self):
        with patch('tasks.DraftDuel.round_runtime.read_roster', side_effect=RuntimeError('OCR unavailable')) as read:
            for _ in range(8):
                self.runtime.prepare(self.views(owners=('one',), captured=100))
            self.assertEqual(read.call_count, 1)
        self.assertNotIn('one', self.runtime.rosters)
        self.assertEqual(self.runtime.preparation_reads['one'], 1)

    def test_missing_lobby_never_fabricates_portrait_fallback(self):
        self.runtime.lobbies.clear()
        with patch('tasks.DraftDuel.round_runtime.read_roster') as read:
            for _ in range(5):
                self.now += 5
                self.runtime.prepare(self.views(owners=('one',)))
            read.assert_not_called()
        self.assertNotIn('one', self.runtime.rosters)
        self.assertGreaterEqual(self.devices['one'].tap_point.call_count, 3)

    def test_failed_open_input_retries_and_then_uses_actual_lobby(self):
        self.devices['one'].tap_point.side_effect = OSError('input temporarily unavailable')
        for _ in range(4):
            self.now += 3
            prepared = self.runtime.prepare(self.views('lobby_ready', owners=('one',)))
        self.assertEqual(self.devices['one'].tap_point.call_count, 3)
        self.assertIn('one', self.runtime.rosters)
        self.assertEqual(prepared['one'].phase, 'lobby_ready')
        self.assertEqual(sum(e['phase'] == 'roster_input_retry' for e in self.events), 3)

    def test_repeated_close_failures_back_off_and_still_retry(self):
        self.devices['one'].tap_point.side_effect = [OSError('one'), OSError('two'), OSError('three'), None]
        for at in (100, 103, 106):
            self.now = at
            self.runtime.prepare(self.views(owners=('one',)))
        self.assertEqual(self.devices['one'].tap_point.call_count, 3)
        self.now = 109
        self.runtime.prepare(self.views(owners=('one',)))
        self.assertEqual(self.devices['one'].tap_point.call_count, 3)
        self.now = 111
        self.runtime.prepare(self.views(owners=('one',)))
        self.assertEqual(self.devices['one'].tap_point.call_count, 4)
        self.assertIn('one', self.runtime.rosters)

    def test_missing_actual_lobby_image_cannot_unlock_matching(self):
        self.runtime.images.clear()
        for _ in range(5):
            self.now += 3
            prepared = self.runtime.prepare(self.views('lobby_ready', owners=('one',)))
        self.assertEqual(prepared['one'].phase, 'unknown')
        self.assertEqual(prepared['one'].actions, {})
        self.assertNotIn('one', self.runtime.rosters)
        self.devices['one'].tap_point.assert_not_called()


if __name__ == '__main__':
    unittest.main()
