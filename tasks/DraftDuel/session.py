"""Local two-process coordination for Draft Duel, independent of game/UI code.

Each worker acquires an account lease, opens the same session, and publishes a
fresh visual observation. The configured leader alone publishes decisions;
legacy callers without leader_id retain the lexical default.
Every action must be claimed before clicking and acknowledged using a *new*
visual observation after clicking. Claimed actions are never replayed following
a restart. The runner must reconcile the actual screens and plan afresh.

This module never opens a game, purchases entry, or advances a game series.
All timestamps are UTC Unix seconds, shared by processes on the same machine.
"""

import hashlib
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class SessionError(RuntimeError):
    """The requested transition is unsafe or unavailable."""


class LeaseBusy(SessionError):
    pass


class LeaseLost(SessionError):
    pass


class SessionUnavailable(SessionError):
    pass


class StaleState(SessionError):
    pass


class DuplicateAction(SessionError):
    pass


class DraftDuelSession:
    """SQLite-backed coordination; keep its database outside account configs.

    Account identities must be stable and identical in both workers' settings.
    An owner_id identifies one process incarnation and normally is generated.
    Reusing owner_id across independent processes defeats lease exclusion.
    """

    def __init__(self, db_path, pair_id, account_id, partner_id, owner_id=None,
                 lease_seconds=30, snapshot_ttl=15, clock=None, leader_id=None):
        for name, value in (("pair_id", pair_id), ("account_id", account_id),
                            ("partner_id", partner_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("{} must be a nonempty string".format(name))
        if account_id == partner_id:
            raise ValueError("The two accounts must be different")
        if (not math.isfinite(lease_seconds) or not math.isfinite(snapshot_ttl)
                or lease_seconds <= 0 or snapshot_ttl <= 0):
            raise ValueError("Lease and snapshot lifetimes must be positive")
        self.db_path = str(Path(db_path).resolve())
        self.pair_id = pair_id
        self.account_id = account_id
        self.partner_id = partner_id
        self.accounts = tuple(sorted((account_id, partner_id)))
        self.leader_id = self.accounts[0] if leader_id is None else leader_id
        if self.leader_id not in self.accounts:
            raise ValueError('Leader must be one of the paired accounts')
        self.owner_id = owner_id or str(uuid.uuid4())
        self.lease_seconds = float(lease_seconds)
        self.snapshot_ttl = float(snapshot_ttl)
        self.clock = clock or time.time
        self.session_id = None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def is_leader(self):
        return self.account_id == self.leader_id

    @staticmethod
    def default_pair_id(account_a, account_b):
        """A filesystem-safe identity if the caller has no named pair."""
        payload = json.dumps(sorted((account_a, account_b)), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._transaction() as db:
            statements = (
                "CREATE TABLE IF NOT EXISTS dd_pairs (pair_id TEXT PRIMARY KEY, "
                "account_a TEXT NOT NULL, account_b TEXT NOT NULL, active_session TEXT)",
                "CREATE TABLE IF NOT EXISTS dd_leases (account_id TEXT PRIMARY KEY, "
                "pair_id TEXT NOT NULL, owner_id TEXT NOT NULL, heartbeat_at REAL NOT NULL, "
                "expires_at REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS dd_sessions (session_id TEXT PRIMARY KEY, "
                "pair_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, "
                "stop_reason TEXT, stopped_by TEXT)",
                "CREATE TABLE IF NOT EXISTS dd_snapshots (session_id TEXT NOT NULL, "
                "account_id TEXT NOT NULL, owner_id TEXT NOT NULL, series INTEGER NOT NULL, "
                "round_no INTEGER NOT NULL, phase TEXT NOT NULL, revision INTEGER NOT NULL, "
                "observed_at REAL NOT NULL, payload TEXT NOT NULL, "
                "PRIMARY KEY (session_id, account_id))",
                "CREATE TABLE IF NOT EXISTS dd_decisions (decision_id TEXT PRIMARY KEY, "
                "session_id TEXT NOT NULL, series INTEGER NOT NULL, round_no INTEGER NOT NULL, "
                "phase TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, "
                "expires_at REAL NOT NULL, revisions TEXT NOT NULL, cancel_reason TEXT)",
                "CREATE TABLE IF NOT EXISTS dd_actions (action_id TEXT PRIMARY KEY, "
                "decision_id TEXT NOT NULL, account_id TEXT NOT NULL, payload TEXT NOT NULL, "
                "status TEXT NOT NULL, claimed_owner TEXT, claimed_at REAL, "
                "observed_revision INTEGER, acked_at REAL, UNIQUE (decision_id, account_id))",
                "CREATE INDEX IF NOT EXISTS dd_decisions_session "
                "ON dd_decisions(session_id, status)",
            )
            for statement in statements:
                db.execute(statement)
            db.execute('CREATE TABLE IF NOT EXISTS dd_leaders '
                       '(pair_id TEXT PRIMARY KEY, leader_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS dd_workflows '
                       '(session_id TEXT PRIMARY KEY, phase TEXT NOT NULL, payload TEXT NOT NULL)')
            role = db.execute('SELECT leader_id FROM dd_leaders WHERE pair_id=?', (self.pair_id,)).fetchone()
            if role and role['leader_id'] != self.leader_id:
                live = db.execute('SELECT 1 FROM dd_leases WHERE pair_id=? AND expires_at>?',
                                  (self.pair_id, self.clock())).fetchone()
                if live:
                    raise SessionError('Cannot change leader while either worker holds a lease')
                db.execute('UPDATE dd_pairs SET active_session=NULL WHERE pair_id=?', (self.pair_id,))
            db.execute('INSERT OR REPLACE INTO dd_leaders VALUES (?, ?)', (self.pair_id, self.leader_id))
            row = db.execute("SELECT * FROM dd_pairs WHERE pair_id=?", (self.pair_id,)).fetchone()
            if row and (row["account_a"], row["account_b"]) != self.accounts:
                raise ValueError("pair_id already belongs to different accounts")
            if not row:
                db.execute("INSERT INTO dd_pairs VALUES (?, ?, ?, NULL)",
                           (self.pair_id,) + self.accounts)

    def _cancel_pending(self, db, session_id, reason):
        db.execute("UPDATE dd_actions SET status='cancelled' WHERE status!='acked' "
                   "AND decision_id IN (SELECT decision_id FROM dd_decisions "
                   "WHERE session_id=? AND status='pending')", (session_id,))
        db.execute("UPDATE dd_decisions SET status='cancelled', cancel_reason=? "
                   "WHERE session_id=? AND status='pending'", (reason, session_id))

    def _invalidate_account(self, db, account_id, reason):
        rows = db.execute("SELECT DISTINCT session_id FROM dd_snapshots WHERE account_id=?",
                          (account_id,)).fetchall()
        for row in rows:
            self._cancel_pending(db, row["session_id"], reason)
        # Retain revision and stage history, but zero its time so it cannot be used.
        db.execute("UPDATE dd_snapshots SET observed_at=0 WHERE account_id=?", (account_id,))

    def acquire_lease(self):
        """Acquire/renew ownership; recovery invalidates old visual observations."""
        with self._transaction() as db:
            now = self.clock()
            row = db.execute("SELECT * FROM dd_leases WHERE account_id=?",
                             (self.account_id,)).fetchone()
            same = row and row["owner_id"] == self.owner_id and row["pair_id"] == self.pair_id
            if row and row["expires_at"] > now and not same:
                raise LeaseBusy("Account already has an active worker")
            if not same or row["expires_at"] <= now:
                self._invalidate_account(db, self.account_id, "account worker reconnected")
            db.execute("INSERT OR REPLACE INTO dd_leases VALUES (?, ?, ?, ?, ?)",
                       (self.account_id, self.pair_id, self.owner_id, now, now + self.lease_seconds))
        return {"owner_id": self.owner_id, "expires_at": now + self.lease_seconds}

    def _require_lease(self, db, now):
        row = db.execute("SELECT * FROM dd_leases WHERE account_id=?",
                         (self.account_id,)).fetchone()
        if (not row or row["owner_id"] != self.owner_id or row["pair_id"] != self.pair_id
                or row["expires_at"] <= now):
            raise LeaseLost("Acquire a live account lease before continuing")
        return row

    def heartbeat(self):
        with self._transaction() as db:
            now = self.clock()
            self._require_lease(db, now)
            db.execute("UPDATE dd_leases SET heartbeat_at=?, expires_at=? WHERE account_id=?",
                       (now, now + self.lease_seconds, self.account_id))
        return now + self.lease_seconds

    def release_lease(self):
        """Release only this process's lease; a replacement worker is untouched."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM dd_leases WHERE account_id=?",
                             (self.account_id,)).fetchone()
            if row and row["owner_id"] == self.owner_id and row["pair_id"] == self.pair_id:
                self._invalidate_account(db, self.account_id, "account worker released")
                db.execute("DELETE FROM dd_leases WHERE account_id=?", (self.account_id,))

    def open_session(self, session_id=None, new=False):
        """Join active session; only leader may create one or replace a stopped one.

        Creation is coordination metadata, never permission to buy game entry.
        new=True explicitly replaces a stopped session; pending sessions cannot
        be silently abandoned. Supplying session_id requires an exact match.
        """
        with self._transaction() as db:
            now = self.clock()
            self._require_lease(db, now)
            row = db.execute("SELECT s.* FROM dd_pairs p JOIN dd_sessions s "
                             "ON s.session_id=p.active_session WHERE p.pair_id=?",
                             (self.pair_id,)).fetchone()
            if session_id and (not row or row["session_id"] != session_id):
                raise SessionUnavailable("Requested session is not active for this pair")
            if new and row and row["status"] != "stopped":
                raise SessionUnavailable("Stop the active session before replacing it")
            if not row or new:
                if not self.is_leader:
                    raise SessionUnavailable("Waiting for leader to create the session")
                active_id = str(uuid.uuid4())
                db.execute("INSERT INTO dd_sessions VALUES (?, ?, 'running', ?, NULL, NULL)",
                           (active_id, self.pair_id, now))
                db.execute("UPDATE dd_pairs SET active_session=? WHERE pair_id=?",
                           (active_id, self.pair_id))
            else:
                active_id = row["session_id"]
            self.session_id = active_id
            return dict(db.execute("SELECT * FROM dd_sessions WHERE session_id=?",
                                   (active_id,)).fetchone())

    def _session(self, db, running=False):
        row = db.execute("SELECT s.* FROM dd_pairs p JOIN dd_sessions s "
                         "ON p.active_session=s.session_id WHERE p.pair_id=? AND s.session_id=?",
                         (self.pair_id, self.session_id)).fetchone()
        if not row:
            raise SessionUnavailable("Open the current pair session first")
        if running and row["status"] != "running":
            raise SessionUnavailable("Session has a stop request or is stopped")
        return row

    @staticmethod
    def _stage(series, round_no, phase):
        if (not isinstance(series, int) or isinstance(series, bool) or series < 0
                or not isinstance(round_no, int) or isinstance(round_no, bool) or round_no < 0
                or not isinstance(phase, str) or not phase.strip()):
            raise ValueError("series/round_no must be nonnegative integers and phase nonempty")

    @staticmethod
    def _snapshot(row):
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def observe(self, series, round_no, phase, payload, observed_at=None):
        """Store a newly captured visual state; never reuse a cached frame's time."""
        self._stage(series, round_no, phase)
        now = self.clock()
        stamp = now if observed_at is None else float(observed_at)
        if not math.isfinite(stamp) or stamp > now or now - stamp >= self.snapshot_ttl:
            raise StaleState("Observation is expired or in the future")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self._transaction() as db:
            now = self.clock()
            lease = self._require_lease(db, now)
            if stamp > now or now - stamp >= self.snapshot_ttl:
                raise StaleState("Observation expired while waiting for coordination lock")
            self._session(db)
            old = db.execute("SELECT * FROM dd_snapshots WHERE session_id=? AND account_id=?",
                             (self.session_id, self.account_id)).fetchone()
            if old and ((series, round_no) < (old["series"], old["round_no"])
                        or stamp <= old["observed_at"]):
                raise StaleState("Observation goes backwards in game progress or capture time")
            if ((not old or old["observed_at"] <= 0 or old["owner_id"] != self.owner_id)
                    and stamp < lease["heartbeat_at"]):
                raise StaleState("Reconnection requires a screenshot captured after acquiring the lease")
            revision = old["revision"] + 1 if old else 1
            db.execute("INSERT OR REPLACE INTO dd_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (self.session_id, self.account_id, self.owner_id, series, round_no,
                        phase, revision, stamp, encoded))
            return self._snapshot(db.execute("SELECT * FROM dd_snapshots "
                                            "WHERE session_id=? AND account_id=?",
                                            (self.session_id, self.account_id)).fetchone())

    def _snapshots(self, db, now, require_fresh=True):
        rows = db.execute("SELECT * FROM dd_snapshots WHERE session_id=?", (self.session_id,)).fetchall()
        result = {row["account_id"]: self._snapshot(row) for row in rows}
        if require_fresh:
            if set(result) != set(self.accounts):
                raise StaleState("Waiting for both accounts to publish fresh screenshots")
            for account_id, snapshot in result.items():
                lease = db.execute("SELECT * FROM dd_leases WHERE account_id=?", (account_id,)).fetchone()
                if (not lease or lease["expires_at"] <= now or lease["pair_id"] != self.pair_id
                        or lease["owner_id"] != snapshot["owner_id"]
                        or snapshot["observed_at"] <= 0 or snapshot["observed_at"] > now
                        or now - snapshot["observed_at"] >= self.snapshot_ttl):
                    raise StaleState("Account observation or heartbeat is stale")
        return result

    def snapshots(self, require_fresh=True):
        with self._transaction() as db:
            self._session(db)
            return self._snapshots(db, self.clock(), require_fresh)

    def _decision(self, db, decision_id):
        row = db.execute("SELECT * FROM dd_decisions WHERE decision_id=? AND session_id=?",
                         (decision_id, self.session_id)).fetchone()
        if not row:
            raise SessionUnavailable("Decision belongs to another session or does not exist")
        result = dict(row)
        result["revisions"] = json.loads(result["revisions"])
        result["actions"] = {}
        for action in db.execute("SELECT * FROM dd_actions WHERE decision_id=?", (decision_id,)):
            item = dict(action)
            item["payload"] = json.loads(item["payload"])
            result["actions"][item["account_id"]] = item
        return result

    def publish_decision(self, actions, expected_revisions, series, round_no, phase):
        """Publish exactly one action per account from the same observed stage.

        actions is {account_id: JSON payload}; expected_revisions is taken from
        snapshots(). Both accounts need an action, even an explicit no-op.
        """
        self._stage(series, round_no, phase)
        if not self.is_leader:
            raise SessionError("Only the deterministic leader may publish decisions")
        if set(actions) != set(self.accounts) or set(expected_revisions) != set(self.accounts):
            raise ValueError("Actions and revisions must name exactly the paired accounts")
        with self._transaction() as db:
            now = self.clock()
            self._require_lease(db, now)
            self._session(db, running=True)
            snapshots = self._snapshots(db, now)
            for account_id, snapshot in snapshots.items():
                if (snapshot["revision"] != expected_revisions[account_id]
                        or (snapshot["series"], snapshot["round_no"], snapshot["phase"])
                        != (series, round_no, phase)):
                    raise StaleState("Decision is not based on both current observations")
            pending = db.execute("SELECT decision_id FROM dd_decisions "
                                 "WHERE session_id=? AND status='pending'", (self.session_id,)).fetchone()
            if pending:
                raise SessionError("A previous decision still awaits both acknowledgements")
            decision_id = str(uuid.uuid4())
            expires_at = min(snapshot["observed_at"] + self.snapshot_ttl for snapshot in snapshots.values())
            db.execute("INSERT INTO dd_decisions VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL)",
                       (decision_id, self.session_id, series, round_no, phase, now, expires_at,
                        json.dumps(expected_revisions, sort_keys=True)))
            for account_id in self.accounts:
                payload = json.dumps(actions[account_id], ensure_ascii=False, allow_nan=False)
                db.execute("INSERT INTO dd_actions VALUES (?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL)",
                           (str(uuid.uuid4()), decision_id, account_id, payload))
            return self._decision(db, decision_id)

    def pending_decision(self):
        """Read the current decision, if any; this does not authorize a click."""
        with self._transaction() as db:
            self._session(db)
            row = db.execute("SELECT decision_id FROM dd_decisions WHERE session_id=? "
                             "AND status='pending'", (self.session_id,)).fetchone()
            return self._decision(db, row["decision_id"]) if row else None

    def claim_action(self, decision_id):
        """Atomically grant this worker one execution; never call a click twice."""
        with self._transaction() as db:
            now = self.clock()
            self._require_lease(db, now)
            self._session(db, running=True)
            decision = self._decision(db, decision_id)
            action = decision["actions"][self.account_id]
            if action["status"] in ("claimed", "acked"):
                raise DuplicateAction("Action has already been claimed or acknowledged")
            if decision["status"] != "pending" or now >= decision["expires_at"]:
                raise StaleState("Decision is cancelled, complete, or expired")
            snapshots = self._snapshots(db, now)
            for account_id in self.accounts:
                snapshot = snapshots[account_id]
                # A peer which already claimed may have observed the resulting
                # screen. An unclaimed action still requires the exact input.
                if decision["actions"][account_id]["status"] == "pending":
                    if (snapshot["revision"] != decision["revisions"][account_id]
                            or (snapshot["series"], snapshot["round_no"], snapshot["phase"])
                            != (decision["series"], decision["round_no"], decision["phase"])):
                        raise StaleState("Unclaimed action's visual input has changed")
            db.execute("UPDATE dd_actions SET status='claimed', claimed_owner=?, claimed_at=? "
                       "WHERE action_id=?", (self.owner_id, now, action["action_id"]))
            return self._decision(db, decision_id)["actions"][self.account_id]

    def ack_action(self, action_id, observed_revision):
        """Confirm action only after the runner visually verifies its result.

        This checks freshness/order; interpreting the image is the runner's job.
        Returns True once both accounts have acknowledged the same decision.
        """
        with self._transaction() as db:
            now = self.clock()
            self._require_lease(db, now)
            self._session(db)
            row = db.execute("SELECT a.* FROM dd_actions a JOIN dd_decisions d "
                             "ON a.decision_id=d.decision_id WHERE a.action_id=? "
                             "AND a.account_id=? AND d.session_id=?",
                             (action_id, self.account_id, self.session_id)).fetchone()
            if not row:
                raise SessionUnavailable("Action does not belong to this account/session")
            if row["status"] == "acked":
                raise DuplicateAction("Action was already acknowledged")
            if row["status"] != "claimed" or row["claimed_owner"] != self.owner_id:
                raise StaleState("Only the worker which claimed an action can acknowledge it")
            decision = self._decision(db, row["decision_id"])
            if decision["status"] != "pending":
                raise StaleState("Decision is no longer pending")
            snapshot = db.execute("SELECT * FROM dd_snapshots WHERE session_id=? AND account_id=?",
                                  (self.session_id, self.account_id)).fetchone()
            if (not snapshot or snapshot["revision"] != observed_revision
                    or snapshot["revision"] <= decision["revisions"][self.account_id]
                    or snapshot["owner_id"] != self.owner_id
                    or snapshot["observed_at"] <= row["claimed_at"]
                    or snapshot["observed_at"] <= 0 or snapshot["observed_at"] > now
                    or now - snapshot["observed_at"] >= self.snapshot_ttl):
                raise StaleState("Acknowledgement requires a fresh observation after execution")
            db.execute("UPDATE dd_actions SET status='acked', observed_revision=?, acked_at=? "
                       "WHERE action_id=?", (observed_revision, now, action_id))
            remaining = db.execute("SELECT COUNT(*) FROM dd_actions WHERE decision_id=? "
                                   "AND status!='acked'", (row["decision_id"],)).fetchone()[0]
            if not remaining:
                db.execute("UPDATE dd_decisions SET status='complete' WHERE decision_id=?",
                           (row["decision_id"],))
            return not remaining

    def barrier(self, decision_id):
        with self._transaction() as db:
            self._session(db)
            return self._decision(db, decision_id)["status"] == "complete"

    def cancel_decision(self, decision_id, reason):
        """Invalidate an uncertain decision; take fresh screenshots to replan."""
        with self._transaction() as db:
            self._require_lease(db, self.clock())
            self._session(db)
            decision = self._decision(db, decision_id)
            if decision["status"] == "pending":
                self._cancel_pending(db, self.session_id, str(reason))
                db.execute("UPDATE dd_snapshots SET observed_at=0 WHERE session_id=?", (self.session_id,))

    def request_stop(self, reason):
        with self._transaction() as db:
            self._require_lease(db, self.clock())
            session = self._session(db)
            if session["status"] == "running":
                db.execute("UPDATE dd_sessions SET status='stopping', stop_reason=?, stopped_by=? "
                           "WHERE session_id=?", (str(reason), self.account_id, self.session_id))
                self._cancel_pending(db, self.session_id, "stop requested")
            return dict(self._session(db))

    def finish_session(self):
        if not self.is_leader:
            raise SessionError("Only leader may finish the session")
        with self._transaction() as db:
            self._require_lease(db, self.clock())
            session = self._session(db)
            if session["status"] == "running":
                raise SessionError("Request stop before finishing the session")
            db.execute("UPDATE dd_sessions SET status='stopped' WHERE session_id=?", (self.session_id,))
            return dict(self._session(db))

    def status(self):
        with self._transaction() as db:
            result = dict(self._session(db))
            result["leader_id"] = self.leader_id
            result["accounts"] = list(self.accounts)
            result["heartbeats"] = {row["account_id"]: dict(row) for row in db.execute(
                "SELECT * FROM dd_leases WHERE pair_id=?", (self.pair_id,))}
            return result

    def set_workflow(self, phase, payload=None):
        """Leader's durable task phase; write before any start click."""
        if not self.is_leader:
            raise SessionError('Only the configured leader controls the task workflow')
        with self._transaction() as db:
            self._require_lease(db, self.clock())
            self._session(db, running=True)
            db.execute('INSERT OR REPLACE INTO dd_workflows VALUES (?, ?, ?)',
                       (self.session_id, phase, json.dumps(payload or {}, ensure_ascii=False)))

    def workflow(self):
        with self._transaction() as db:
            self._session(db)
            row = db.execute('SELECT * FROM dd_workflows WHERE session_id=?', (self.session_id,)).fetchone()
            return {'phase': row['phase'], 'payload': json.loads(row['payload'])} if row else {'phase': 'team', 'payload': {}}
