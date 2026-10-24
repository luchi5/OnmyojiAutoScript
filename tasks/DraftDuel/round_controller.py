"""Cooperate through one admitted series using observed screen evidence only.

There is deliberately no admission/start-paid-round action here. A completed
series ends after both accounts acknowledge its terminal screen and return to
the unopened lobby. Input, deployment and time are injected for offline replay.
"""
import math
import time


class SeriesController:
    def __init__(self, owners, leader, act, deploy, event=None,
                 clock=time.monotonic, matchmaking_timeout=45, phase_timeout=180):
        self.owners = tuple(owners)
        if len(self.owners) < 2 or len(set(self.owners)) != len(self.owners):
            raise ValueError("A series requires distinct cooperating accounts")
        if leader not in self.owners:
            raise ValueError("Leader must be one of the accounts")
        self.leader = leader
        self.act, self.deploy, self.event, self.clock = act, deploy, event, clock
        self.matchmaking_timeout = matchmaking_timeout
        self.phase_timeout = phase_timeout
        self.wins = self.losses = self.match_id = 0
        self.complete = False
        self._epoch_open = self._match_progress = self._result_counted = False
        self._result_outcome = None
        self._outcomes = {}
        self._terminal = False
        self._terminal_at = {}
        self._returned = set()
        self._queue_started = None
        self._cancelling = False
        self._last_action = {}
        self._phases = {}
        self._phase_since = {}
        self._timeout_notified = set()
        self._last_observed = {}
        self._battle_seen = set()
        self._deploy_seen = set()
        self._step_actions = []
        self._step_events = []

    def _emit(self, kind, **details):
        record = dict(event=kind, match_id=self.match_id, **details)
        self._step_events.append(record)
        if self.event:
            self.event(record)

    @staticmethod
    def _score(view):
        w, loss = view.wins, view.losses
        if (type(w) is int and type(loss) is int and 0 <= w <= 6
                and 0 <= loss <= 2 and not (w == 6 and loss == 2)):
            return w, loss
        return None

    def _begin_match(self, observed=False):
        self.match_id += 1
        self._epoch_open = True
        self._match_progress = observed
        self._result_counted = False
        self._result_outcome = None
        self._outcomes = {}
        self._battle_seen.clear()
        self._deploy_seen.clear()
        self._last_action.clear()
        self._emit("match_started", observed=observed)

    def _perform(self, owner, action, view, now, execute):
        point = view.actions.get(action)
        if (not isinstance(point, (tuple, list)) or len(point) != 2
                or not all(isinstance(p, (int, float)) and not isinstance(p, bool)
                           and math.isfinite(p) and p >= 0 for p in point)):
            return False
        key = (owner, action)
        previous = self._last_action.get(key)
        if previous:
            delay = min(15, 1.5 * 2 ** min(previous["attempts"] - 1, 4))
            if (view.captured <= previous["captured"]
                    or now - previous["at"] < delay):
                return False
        attempt = previous["attempts"] + 1 if previous else 1
        record = dict(owner=owner, action=action, point=list(point), attempt=attempt,
                      executed=False)
        self._step_actions.append(record)
        if not execute:
            return False
        self._last_action[key] = dict(at=now, captured=view.captured, attempts=attempt)
        try:
            accepted = self.act(owner, action, tuple(point))
            if accepted is False:
                self._emit("action_retry_pending", owner=owner, action=action,
                           attempt=attempt, error="input_not_sent")
                return False
        except Exception as error:
            self._emit("action_retry_pending", owner=owner, action=action,
                       attempt=attempt, error=str(error))
            return False
        record["executed"] = True
        self._emit("action_sent", owner=owner, action=action, attempt=attempt)
        return True

    def _reconcile_score(self, views):
        if len(views) != len(self.owners):
            return False
        if len({view.outcome for view in views.values()
                if view.phase == "result" and view.outcome in ("win", "loss")}) > 1:
            return False
        pairs = [self._score(views[owner]) for owner in self.owners]
        if None in pairs or len(set(pairs)) != 1:
            return False
        wins, losses = pairs[0]
        if wins < self.wins or losses < self.losses:
            return False
        if (wins, losses) != (self.wins, self.losses):
            delta = wins - self.wins, losses - self.losses
            self.wins, self.losses = wins, losses
            if self._epoch_open:
                self._result_counted = True
                self._result_outcome = ({(1, 0): "win", (0, 1): "loss"}.get(delta))
            self._emit("score_confirmed", wins=wins, losses=losses,
                       source="both_accounts", delta=list(delta))
        return True

    def _observe_results(self, views):
        if not self._epoch_open or not self._match_progress:
            return False
        for owner, view in views.items():
            if view.phase == "result" and view.outcome in ("win", "loss"):
                self._outcomes[owner] = view.outcome
        outcomes = set(self._outcomes.values())
        conflict = (len(outcomes) > 1 or (self._result_outcome is not None
                    and any(v != self._result_outcome for v in outcomes)))
        if conflict:
            return True
        if (len(self._outcomes) == len(self.owners) and not self._result_counted
                and not self._terminal):
            outcome = next(iter(outcomes))
            if self.wins < 6 and self.losses < 2:
                self.wins += outcome == "win"
                self.losses += outcome == "loss"
                self._result_counted = True
                self._result_outcome = outcome
                self._emit("battle_result_confirmed", outcome=outcome,
                           wins=self.wins, losses=self.losses)
        return False

    def _summary(self, state, now):
        stalled = {owner: max(0, now - self._phase_since.get(owner, now))
                   for owner in self.owners}
        return dict(state=state, match_id=self.match_id, wins=self.wins,
                    losses=self.losses, complete=self.complete,
                    terminal=self._terminal, result_confirmed=self._result_counted,
                    terminal_acknowledged=list(self._terminal_at),
                    returned=sorted(self._returned), phases=dict(self._phases),
                    stalled_seconds=stalled, actions=list(self._step_actions),
                    events=list(self._step_events),
                    recovery_overdue=[o for o, elapsed in stalled.items()
                                      if elapsed >= self.phase_timeout],
                    next_poll=2 if state in ("auto_rereading", "result_conflict") else .5)

    def step(self, views, execute=True):
        now = self.clock()
        self._step_actions, self._step_events = [], []
        if self.complete:
            return self._summary("complete", now)
        fresh = {owner: views[owner] for owner in self.owners
                 if (owner in views and views[owner] is not None and views[owner].fresh(now)
                     and views[owner].captured >= self._last_observed.get(owner, float("-inf")))}
        self._last_observed.update({owner: view.captured for owner, view in fresh.items()})
        for owner in self.owners:
            phase = fresh[owner].phase if owner in fresh else "unknown"
            if self._phases.get(owner) != phase:
                self._phase_since[owner] = now
                self._phases[owner] = phase
                self._timeout_notified.discard(owner)
                # Returning to a known screen is a new recovery episode; old
                # successful reconnect/close attempts must not delay this one.
                for action in ("reconnect", "close"):
                    self._last_action.pop((owner, action), None)
            elif (now - self._phase_since[owner] >= self.phase_timeout
                  and owner not in self._timeout_notified):
                self._timeout_notified.add(owner)
                self._emit("recovery_continues", owner=owner, phase=phase,
                           elapsed=now - self._phase_since[owner])
        for owner, view in fresh.items():
            if view.phase == "disconnected":
                self._perform(owner, "reconnect", view, now, execute)
            elif view.phase == "popup":
                self._perform(owner, "close", view, now, execute)

        progression = any(v.phase in ("deploying", "battle", "result")
                          for v in fresh.values())
        if progression and not self._epoch_open and not self._terminal:
            self._begin_match(observed=True)
        if any(v.phase in ("matching", "deploying", "battle") for v in fresh.values()):
            self._match_progress = True
        self._reconcile_score(fresh)
        conflict = self._observe_results(fresh)
        if self.wins >= 6 or self.losses >= 2:
            self._terminal = True

        for owner, view in fresh.items():
            terminal_counter = self._score(view)
            terminal_counter = terminal_counter and (
                terminal_counter[0] >= 6 or terminal_counter[1] >= 2)
            if (view.phase == "series_result" or
                    (view.phase == "lobby_unopened" and terminal_counter)):
                self._terminal = True
                if owner not in self._terminal_at:
                    self._terminal_at[owner] = view.captured
                    self._emit("terminal_observed", owner=owner, phase=view.phase)
            if (view.phase == "lobby_unopened" and owner in self._terminal_at
                    and view.captured > self._terminal_at[owner]):
                self._returned.add(owner)
            if view.phase == "series_result":
                self._perform(owner, "collect", view, now, execute)
        if (self._terminal and len(fresh) == len(self.owners)
                and all(owner in self._returned and fresh[owner].phase == "lobby_unopened"
                        for owner in self.owners)):
            self.complete = True
            self._emit("series_completed", wins=self.wins, losses=self.losses)
            return self._summary("complete", now)

        # Resolve each account separately. A slow partner does not cause the
        # fast account's result to be counted twice, nor does it block reconnect.
        for owner, view in fresh.items():
            if (view.phase == "result" and ((not conflict)
                    or (self._terminal and self._terminal_at))):
                # Dismissing an observed result is independent of counting it.
                # Keep its outcome while a slow partner catches up; a partner
                # already back in the lobby may have skipped its result screen.
                # A partner may already have passed this battle's result into
                # the series settlement. Its positive terminal evidence permits
                # closing the lagging result without inventing an outcome.
                self._perform(owner, "result_continue", view, now, execute)
            elif view.phase == "deploying" and not self._terminal:
                self._deploy_seen.add(owner)
                if execute:
                    try:
                        status = self.deploy(owner)
                        self._emit("deployment_progress", owner=owner, status=status)
                    except Exception as error:
                        self._emit("deployment_retry_pending", owner=owner, error=str(error))
            elif view.phase == "battle" and not self._terminal:
                if owner not in self._battle_seen:
                    self._battle_seen.add(owner)
                    self._emit("battle_observed", owner=owner,
                               deployment_seen=owner in self._deploy_seen,
                               intended_lineup_verified=False)
                # The recognizer supplies this button only for observed manual
                # mode. Never toggle an already automatic battle.
                self._perform(owner, "auto", view, now, execute)

        phases = {v.phase for v in fresh.values()}
        both_ready = (len(fresh) == len(self.owners)
                      and all(v.phase == "lobby_ready" for v in fresh.values()))
        if not self._terminal and not conflict:
            if "matching" in phases:
                if self._queue_started is None:
                    self._queue_started = now
                if now - self._queue_started >= self.matchmaking_timeout:
                    self._cancelling = True
                if self._cancelling:
                    for owner in (self.leader,) + tuple(o for o in self.owners if o != self.leader):
                        if owner in fresh and "cancel_match" in fresh[owner].actions:
                            self._perform(owner, "cancel_match", fresh[owner], now, execute)
                            break
            elif both_ready:
                if self._cancelling:
                    self._cancelling = False
                    self._epoch_open = False
                    self._queue_started = None
                    self._emit("matchmaking_recovered")
                elif self._epoch_open and self._match_progress and not self._result_counted:
                    # The game can leave matchmaking itself, or its result can
                    # disappear before polling catches it. Both observed ready
                    # lobbies permit another match within this admitted series.
                    # The missing outcome remains unknown until shared counters
                    # or terminal evidence recover it; it is never guessed.
                    self._emit("returned_ready_without_result",
                               battle_observed=bool(self._battle_seen),
                               outcome="unknown")
                    self._epoch_open = False
                    self._queue_started = None
                # A completed battle can start the next queue; a missed initial
                # click retries the existing queue without inventing a battle.
                if not self._epoch_open or self._result_counted:
                    if "match" in fresh[self.leader].actions and execute:
                        self._begin_match()
                        self._queue_started = now
                if self._epoch_open and not self._match_progress:
                    self._perform(self.leader, "match", fresh[self.leader], now, execute)
            elif "deploying" in phases or "battle" in phases or "result" in phases:
                self._queue_started = None
                self._cancelling = False

        if conflict:
            state = "result_conflict"
        elif "disconnected" in phases or "popup" in phases:
            state = "recovering"
        elif self._terminal:
            state = "settling"
        elif "result" in phases:
            state = "result_continue"
        elif "battle" in phases:
            state = "battle"
        elif "deploying" in phases:
            state = "deploying"
        elif "matching" in phases:
            state = "cancelling_match" if self._cancelling else "matching"
        elif both_ready:
            state = "match_requested"
        else:
            state = "auto_rereading"
        return self._summary(state, now)

    def checkpoint(self):
        """JSON-compatible state; callbacks and screen coordinates are excluded."""
        now = self.clock()
        return dict(version=1, owners=list(self.owners), leader=self.leader,
                    wins=self.wins, losses=self.losses, match_id=self.match_id,
                    complete=self.complete, epoch_open=self._epoch_open,
                    match_progress=self._match_progress, result_counted=self._result_counted,
                    result_outcome=self._result_outcome, outcomes=dict(self._outcomes),
                    terminal=self._terminal, terminal_seen=list(self._terminal_at),
                    returned=list(self._returned), cancelling=self._cancelling,
                    queue_elapsed=(None if self._queue_started is None else
                                   max(0, now - self._queue_started)),
                    battle_seen=list(self._battle_seen), deploy_seen=list(self._deploy_seen))

    def restore(self, state):
        if (state.get("version") != 1 or tuple(state.get("owners", ())) != self.owners
                or state.get("leader") != self.leader):
            raise ValueError("Checkpoint does not belong to this cooperating pair")
        wins, losses = state.get("wins"), state.get("losses")
        if (type(wins) is not int or type(losses) is not int or
                not 0 <= wins <= 6 or not 0 <= losses <= 2 or (wins == 6 and losses == 2)):
            raise ValueError("Invalid checkpoint score")
        now = self.clock()
        self.wins, self.losses = wins, losses
        self.match_id = int(state.get("match_id", 0))
        self._epoch_open = bool(state.get("epoch_open"))
        self._match_progress = bool(state.get("match_progress"))
        self._result_counted = bool(state.get("result_counted"))
        self._result_outcome = state.get("result_outcome")
        self._outcomes = {o: result for o, result in state.get("outcomes", {}).items()
                          if o in self.owners and result in ("win", "loss")}
        self._terminal = bool(state.get("terminal"))
        self._terminal_at = {o: now for o in state.get("terminal_seen", ()) if o in self.owners}
        self._returned = set(state.get("returned", ())).intersection(self.owners)
        self.complete = bool(state.get("complete")) and self._terminal and all(
            o in self._returned and o in self._terminal_at for o in self.owners)
        self._cancelling = bool(state.get("cancelling"))
        elapsed = state.get("queue_elapsed")
        self._queue_started = None if elapsed is None else now - max(0, float(elapsed))
        self._battle_seen = set(state.get("battle_seen", ())).intersection(self.owners)
        self._deploy_seen = set(state.get("deploy_seen", ())).intersection(self.owners)
        self._last_action = {}
        self._phases, self._phase_since = {}, {}
        self._timeout_notified = set()
        self._last_observed = {}
        return self
