"""Normal task pipeline with simulated draft boundaries and real SQLite peers.

Image recognition and five-pick confirmation are tested separately using real
screenshots. These tests exercise task ownership, fees and end synchronization.
"""
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tasks.DraftDuel.config import DraftDuelConfig
from tasks.DraftDuel.preflight import AccountConfig, PairConfig
from tasks.DraftDuel.session import DraftDuelSession, SessionError
from tasks.DraftDuel.strategy import Candidate
from tasks.DraftDuel.team import TeamView
from tasks.DraftDuel.workflow import DraftWorkflow, StartOffer


class PipelineTests(unittest.TestCase):
    def simulate(self, budget):
        records, clicks = [], []
        image = np.zeros((720, 1280, 3), np.uint8)
        io = Mock(); io.capture.return_value = (image, time.monotonic())
        io.tap_point.side_effect = lambda *p, **k: clicks.append((p, k))
        settings = DraftDuelConfig(user_status='leader', partner_config='two', teammate_name='member', free_only=False, coin_budget=budget)
        partner = DraftDuelConfig(user_status='member', partner_config='one', teammate_name='leader', free_only=False, coin_budget=budget)
        pair = PairConfig((AccountConfig('one', 'port1', 'emu1', settings), AccountConfig('two', 'port2', 'emu2', partner)))
        vision = Mock(); vision.read.return_value = TeamView('team_ready')

        class DraftBoundary:
            def __init__(self, devices, event=None):
                self.devices, self.event, self.pick = devices, event, 0
                self.attempted, self.confirmed = set(), ()
            def observe_pair(self): return []
            def step(self, execute=False):
                self.pick += 1
                for owner, device in self.devices.items():
                    device.tap(1)
                    self.attempted.add((owner, self.pick))
                    self.confirmed += (Candidate(f'{owner}:r{self.pick}:s1', owner, '椒图'),)
                return {'state': 'simulated_confirmed', 'actions': []}
            @property
            def complete(self): return len(self.confirmed) == 10
            def close(self): pass

        with tempfile.TemporaryDirectory() as root:
            workflow = DraftWorkflow(pair, 'one', io, vision, root, event=lambda r, image=None: records.append(r))
            peer = DraftDuelSession(Path(root)/'log/draft_duel_runtime.sqlite3', workflow.session.pair_id,
                                   'two', 'one', leader_id='one')
            stop = threading.Event()
            def follow():
                peer.acquire_lease()
                try:
                    while not stop.is_set():
                        peer.heartbeat()
                        try:
                            if peer.open_session()['status'] == 'running':
                                done = peer.workflow()['phase'] == 'complete'
                                peer.observe(0, 0, 'workflow', {'stage': 'complete_seen' if done else 'member_waiting'})
                                if done: return
                        except SessionError: pass
                        time.sleep(.05)
                finally: peer.release_lease()
            worker = threading.Thread(target=follow)
            worker.start()
            peer_io = Mock(); peer_io.capture.return_value = (image, time.monotonic())
            try:
                with patch('tasks.DraftDuel.workflow.LocalDraft', DraftBoundary), \
                     patch('tasks.DraftDuel.workflow.PartnerDraftIO', return_value=peer_io), \
                     patch('tasks.DraftDuel.workflow.read_offer', return_value=StartOffer(300000, 1, (1193, 616))), \
                     patch('tasks.DraftDuel.workflow.opening_window', return_value=SimpleNamespace(is_scheduled_open=True)):
                    if budget:
                        self.assertTrue(workflow.run())
                        self.assertEqual(len([c for c in clicks if c[1].get('control_name') == 'DRAFT_DUEL_START']), 1)
                        self.assertEqual(len([c for c in clicks if c[1].get('control_name') == 'DRAFT_DUEL_PICK_1']), 5)
                        self.assertEqual(peer_io.tap.call_count, 5)
                        self.assertEqual(records[-1]['phase'], 'complete')
                    else:
                        with self.assertRaises(SessionError): workflow.run()
                        self.assertFalse(clicks)
            finally:
                stop.set(); worker.join(5)

    def test_one_start_both_five_picks_and_member_completion_ack(self): self.simulate(300000)
    def test_zero_budget_never_starts_or_selects(self): self.simulate(0)


if __name__ == '__main__': unittest.main()
