"""Offline transaction, lease, recovery and action-barrier regressions."""

import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tasks.DraftDuel.session import (
    DraftDuelSession, DuplicateAction, LeaseBusy, LeaseLost, SessionError,
    SessionUnavailable, StaleState,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds=0.1):
        self.now += seconds


def contend_for_lease(database, start, results, owner):
    """Top-level target deliberately tests separate Windows-spawned processes."""
    worker = DraftDuelSession(database, "process-pair", "process-a", "process-b", owner_id=owner)
    start.wait(10)
    try:
        worker.acquire_lease()
        results.put("acquired")
    except LeaseBusy:
        results.put("busy")


class DraftDuelSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = str(Path(self.directory.name) / "duel.sqlite3")
        self.clock = Clock()
        self.a = self.worker("a", "b")
        self.b = self.worker("b", "a")
        self.a.acquire_lease()
        self.b.acquire_lease()
        self.session_id = self.a.open_session()["session_id"]
        self.b.open_session(self.session_id)
        self.observe_both()

    def tearDown(self):
        self.directory.cleanup()

    def worker(self, account, partner, pair="pair"):
        return DraftDuelSession(self.database, pair, account, partner, clock=self.clock)

    def observe_both(self, series=0, round_no=0, phase="selection"):
        self.clock.advance()
        self.a.observe(series, round_no, phase, {"screen": "a"})
        self.b.observe(series, round_no, phase, {"screen": "b"})

    def decision(self):
        snapshots = self.a.snapshots()
        stage = snapshots["a"]
        return self.a.publish_decision(
            {"a": {"operation": "choose", "slot": 1}, "b": {"operation": "choose", "slot": 2}},
            {key: value["revision"] for key, value in snapshots.items()},
            stage["series"], stage["round_no"], stage["phase"],
        )

    def test_deterministic_leader_and_pair_validation(self):
        self.assertTrue(self.a.is_leader)
        self.assertFalse(self.b.is_leader)
        self.assertEqual(self.a.default_pair_id("a", "b"), self.b.default_pair_id("b", "a"))
        with self.assertRaises(ValueError):
            self.worker("a", "a")
        with self.assertRaises(ValueError):
            self.worker("a", "different")
        with self.assertRaises(SessionError):
            self.b.publish_decision({"a": {}, "b": {}}, {"a": 1, "b": 1}, 0, 0, "selection")

    def test_follower_waits_without_creating_session(self):
        follower = self.worker("z", "y", pair="unstarted")
        follower.acquire_lease()
        with self.assertRaises(SessionUnavailable):
            follower.open_session()

    def test_live_lease_excludes_account_across_pairs(self):
        with self.assertRaises(LeaseBusy):
            self.worker("a", "b").acquire_lease()
        with self.assertRaises(LeaseBusy):
            self.worker("a", "c", pair="other").acquire_lease()

    def test_barrier_requires_both_actions_and_new_visual_confirmation(self):
        decision = self.decision()
        action_a = self.a.claim_action(decision["decision_id"])
        with self.assertRaises(StaleState):
            self.a.ack_action(action_a["action_id"], 1)
        with self.assertRaises(SessionUnavailable):
            self.b.ack_action(action_a["action_id"], 1)
        self.clock.advance()
        snapshot_a = self.a.observe(0, 0, "selected", {"confirmed": True})
        self.assertFalse(self.a.ack_action(action_a["action_id"], snapshot_a["revision"]))
        self.assertFalse(self.a.barrier(decision["decision_id"]))
        # Peer may claim after A has visually advanced to the resulting screen.
        action_b = self.b.claim_action(decision["decision_id"])
        self.clock.advance()
        snapshot_b = self.b.observe(0, 0, "selected", {"confirmed": True})
        self.assertTrue(self.b.ack_action(action_b["action_id"], snapshot_b["revision"]))
        self.assertTrue(self.a.barrier(decision["decision_id"]))
        self.assertIsNone(self.a.pending_decision())
        with self.assertRaises(DuplicateAction):
            self.b.ack_action(action_b["action_id"], snapshot_b["revision"])

    def test_only_latest_visual_revision_can_ack(self):
        decision = self.decision()
        action = self.a.claim_action(decision["decision_id"])
        self.clock.advance()
        old = self.a.observe(0, 0, "selected", {})
        self.clock.advance()
        latest = self.a.observe(0, 0, "selected", {})
        with self.assertRaises(StaleState):
            self.a.ack_action(action["action_id"], old["revision"])
        self.assertFalse(self.a.ack_action(action["action_id"], latest["revision"]))

    def test_concurrent_duplicate_claim_has_one_winner(self):
        decision = self.decision()

        def claim():
            try:
                self.a.claim_action(decision["decision_id"])
                return "claimed"
            except DuplicateAction:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: claim(), range(6)))
        self.assertEqual(results.count("claimed"), 1)
        self.assertEqual(results.count("duplicate"), 5)

    def test_concurrent_publication_has_one_pending_decision(self):
        def publish():
            try:
                self.decision()
                return "published"
            except SessionError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: publish(), range(4)))
        self.assertEqual(results.count("published"), 1)
        self.assertEqual(results.count("blocked"), 3)

    def test_stale_revision_and_mismatched_phase_are_rejected(self):
        decision = self.decision()
        self.clock.advance()
        self.b.observe(0, 0, "different", {})
        with self.assertRaises(StaleState):
            self.a.claim_action(decision["decision_id"])
        self.a.cancel_decision(decision["decision_id"], "screen changed")
        with self.assertRaises(StaleState):
            self.decision()
        self.observe_both()
        with self.assertRaises(StaleState):
            self.a.publish_decision({"a": {}, "b": {}}, {"a": 1, "b": 1}, 0, 0, "selection")
        self.clock.advance()
        self.b.observe(0, 0, "different", {})
        with self.assertRaises(StaleState):
            self.decision()

    def test_expired_snapshot_and_decision_are_rejected(self):
        decision = self.decision()
        self.clock.advance(16)
        with self.assertRaises(StaleState):
            self.a.snapshots()
        with self.assertRaises(StaleState):
            self.a.claim_action(decision["decision_id"])
        self.a.cancel_decision(decision["decision_id"], "expired")
        self.observe_both()
        self.assertIsNotNone(self.decision())

    def test_expiry_is_checked_after_waiting_for_database_lock(self):
        decision = self.decision()
        blocker = sqlite3.connect(self.database, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        connected = threading.Event()
        original_connect = self.a._connect

        def connection_ready():
            connection = original_connect()
            connected.set()
            return connection

        self.a._connect = connection_ready
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.a.claim_action, decision["decision_id"])
                self.assertTrue(connected.wait(5))
                self.clock.advance(16)
                blocker.commit()
                with self.assertRaises(StaleState):
                    result.result(timeout=5)
        finally:
            blocker.close()
            self.a._connect = original_connect

    def test_observation_cannot_go_backwards_or_come_from_future(self):
        self.observe_both(series=1, round_no=2)
        for series, round_no in ((0, 5), (1, 1)):
            with self.assertRaises(StaleState):
                self.a.observe(series, round_no, "selection", {})
        with self.assertRaises(StaleState):
            self.a.observe(1, 2, "selection", {}, observed_at=self.clock.now + 1)
        with self.assertRaises(StaleState):
            self.a.observe(1, 2, "selection", {}, observed_at=self.clock.now - 20)
        with self.assertRaises(ValueError):
            self.a.observe(-1, 0, "selection", {})

    def test_cached_frame_timestamp_cannot_be_republished(self):
        timestamp = self.a.snapshots()["a"]["observed_at"]
        self.clock.advance()
        with self.assertRaises(StaleState):
            self.a.observe(0, 0, "selection", {}, observed_at=timestamp)

    def test_screenshot_from_before_lease_reacquisition_is_rejected(self):
        self.a.release_lease()
        old_capture = self.clock.now
        self.clock.advance()
        replacement = self.worker("a", "b")
        replacement.acquire_lease()
        replacement.open_session(self.session_id)
        with self.assertRaises(StaleState):
            replacement.observe(0, 0, "selection", {}, observed_at=old_capture)

    def test_restart_invalidates_action_and_requires_both_fresh_screens(self):
        decision = self.decision()
        old_action = self.a.claim_action(decision["decision_id"])
        self.clock.advance(31)
        replacement = self.worker("a", "b")
        replacement.acquire_lease()
        replacement.open_session(self.session_id)
        self.b.acquire_lease()
        with self.assertRaises(LeaseLost):
            self.a.heartbeat()
        with self.assertRaises(StaleState):
            replacement.snapshots()
        with self.assertRaises(StaleState):
            replacement.claim_action(decision["decision_id"])
        self.clock.advance()
        fresh = replacement.observe(0, 0, "selection", {"from": "new capture"})
        self.b.observe(0, 0, "selection", {"from": "new capture"})
        with self.assertRaises(StaleState):
            replacement.ack_action(old_action["action_id"], fresh["revision"])
        self.assertGreater(fresh["revision"], decision["revisions"]["a"])
        self.a.release_lease()  # Old process cannot release the replacement's lease.
        replacement.heartbeat()

    def test_session_and_pair_isolation(self):
        decision = self.decision()
        other_a = self.worker("c", "d", pair="second")
        other_b = self.worker("d", "c", pair="second")
        other_a.acquire_lease()
        other_b.acquire_lease()
        other_a.open_session()
        other_b.open_session()
        with self.assertRaises(SessionUnavailable):
            other_a.claim_action(decision["decision_id"])
        with self.assertRaises(SessionUnavailable):
            other_b.open_session(self.session_id)
        with self.assertRaises(StaleState):
            other_a.snapshots()
        self.assertEqual(len(self.a.snapshots()), 2)

    def test_stop_is_persistent_blocks_actions_and_requires_explicit_new_session(self):
        decision = self.decision()
        self.b.request_stop("budget exhausted")
        self.assertEqual(self.a.status()["stop_reason"], "budget exhausted")
        self.assertEqual(self.a.status()["stopped_by"], "b")
        with self.assertRaises(SessionUnavailable):
            self.a.claim_action(decision["decision_id"])
        with self.assertRaises(SessionUnavailable):
            self.a.open_session(new=True)
        self.a.finish_session()
        self.assertEqual(self.a.open_session()["status"], "stopped")
        new_session = self.a.open_session(new=True)
        self.assertNotEqual(new_session["session_id"], self.session_id)
        with self.assertRaises(SessionUnavailable):
            self.b.status()
        self.b.open_session(new_session["session_id"])
        with self.assertRaises(SessionUnavailable):
            self.a.claim_action(decision["decision_id"])
        with self.assertRaises(StaleState):
            self.a.snapshots()

    def test_two_processes_cannot_lease_same_account(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [context.Process(target=contend_for_lease,
                                     args=(self.database, start, results, "owner-{}".format(index)))
                     for index in range(2)]
        for process in processes:
            process.start()
        start.set()
        try:
            outcomes = [results.get(timeout=15) for _ in processes]
            self.assertEqual(sorted(outcomes), ["acquired", "busy"])
        finally:
            for process in processes:
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                self.assertEqual(process.exitcode, 0)
            results.close()


if __name__ == "__main__":
    unittest.main()
