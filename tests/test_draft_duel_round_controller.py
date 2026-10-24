import json
import unittest

from tasks.DraftDuel.round_controller import SeriesController
from tasks.DraftDuel.round_model import RoundView


class Replay:
    def __init__(self, **kwargs):
        self.now = 100.
        self.actions, self.deploys, self.events = [], [], []
        self.controller = SeriesController(
            ("one", "nine"), "one", self.act, self.deploy,
            event=self.events.append, clock=lambda: self.now, **kwargs)

    def act(self, owner, action, point):
        self.actions.append((owner, action, point))

    def deploy(self, owner):
        self.deploys.append(owner)
        return "confirmation_pending"

    def view(self, phase, actions=(), **kwargs):
        return RoundView(phase, self.now, actions={a: (600, 600) for a in actions}, **kwargs)

    def step(self, phase, actions=(), **kwargs):
        self.now += 2
        view = self.view(phase, actions, **kwargs)
        return self.controller.step({"one": view, "nine": view})

    def pair(self, one, nine):
        result = self.controller.step({"one": one, "nine": nine})
        self.now += 2
        return result

    def match(self, outcome):
        self.step("lobby_ready", ("match",))
        self.step("matching", ("cancel_match",))
        self.step("deploying")
        self.step("battle", ("auto",))
        return self.step("result", ("result_continue",), outcome=outcome)

    def finish(self):
        self.step("series_result", ("collect",), wins=self.controller.wins,
                  losses=self.controller.losses)
        return self.step("lobby_unopened")


class FullSeriesControllerTests(unittest.TestCase):
    def test_six_wins_complete_and_never_begin_another_paid_series(self):
        replay = Replay()
        for index in range(6):
            replay.match("win")
            self.assertEqual((replay.controller.wins, replay.controller.match_id), (index + 1, index + 1))
        self.assertFalse(replay.controller.complete)
        self.assertEqual(replay.finish()["state"], "complete")
        actions = len(replay.actions)
        replay.step("lobby_ready", ("match",))
        self.assertEqual(len(replay.actions), actions)
        self.assertEqual(sum(a[1] == "match" for a in replay.actions), 6)

    def test_two_losses_finish_a_series(self):
        replay = Replay()
        replay.match("loss")
        replay.match("loss")
        self.assertTrue(replay.finish()["complete"])
        self.assertEqual((replay.controller.wins, replay.controller.losses), (0, 2))

    def test_mixed_series_stops_at_second_loss(self):
        replay = Replay()
        for outcome in ("win", "loss", "win", "win", "loss"):
            replay.match(outcome)
        self.assertTrue(replay.finish()["complete"])
        self.assertEqual((replay.controller.wins, replay.controller.losses), (3, 2))

    def test_missed_match_click_retries_same_match_with_fresh_screen(self):
        replay = Replay()
        replay.step("lobby_ready", ("match",))
        replay.step("lobby_ready", ("match",))
        self.assertEqual(len(replay.actions), 2)
        self.assertEqual(replay.controller.match_id, 1)
        replay.step("lobby_ready", ("match",))
        self.assertEqual(len(replay.actions), 2)  # second retry uses a longer cadence

    def test_same_frame_never_sends_two_inputs(self):
        replay = Replay()
        view = replay.view("lobby_ready", ("match",))
        replay.pair(view, view)
        replay.pair(view, view)
        self.assertEqual(len(replay.actions), 1)

    def test_matching_timeout_cancels_then_rematches_observed_ready_lobby(self):
        replay = Replay(matchmaking_timeout=5)
        replay.step("lobby_ready", ("match",))
        replay.step("matching", ("cancel_match",))
        replay.now += 5
        self.assertEqual(replay.step("matching", ("cancel_match",))["state"], "cancelling_match")
        self.assertEqual(replay.actions[-1][1], "cancel_match")
        replay.step("lobby_ready", ("match",))
        self.assertEqual(replay.actions[-1][1], "match")
        self.assertEqual(replay.controller.match_id, 2)

    def test_lagging_partner_does_not_create_a_second_queue(self):
        replay = Replay()
        replay.step("lobby_ready", ("match",))
        replay.pair(replay.view("matching", ("cancel_match",)), replay.view("lobby_ready"))
        self.assertEqual(sum(a[1] == "match" for a in replay.actions), 1)

    def test_server_cancelled_queue_rematches_without_operator(self):
        replay = Replay()
        replay.step("lobby_ready", ("match",))
        replay.step("matching", ("cancel_match",))
        replay.step("lobby_ready", ("match",))
        self.assertEqual(replay.controller.match_id, 2)
        self.assertEqual(replay.actions[-1][1], "match")

    def test_missing_result_does_not_stall_next_match_or_invent_outcome(self):
        replay = Replay()
        replay.step("battle")
        replay.step("lobby_ready", ("match",))
        self.assertEqual(replay.controller.match_id, 2)
        self.assertEqual((replay.controller.wins, replay.controller.losses), (0, 0))
        self.assertTrue(any(e["event"] == "returned_ready_without_result" for e in replay.events))

    def test_result_advances_while_waiting_for_partner_count_and_counts_only_once(self):
        replay = Replay()
        replay.step("battle")
        replay.pair(replay.view("result", ("result_continue",), outcome="win"), replay.view("battle"))
        self.assertEqual(replay.controller.wins, 0)
        self.assertEqual(replay.actions, [("one", "result_continue", (600, 600))])
        replay.step("result", ("result_continue",), outcome="win")
        replay.step("result", ("result_continue",), outcome="win")
        self.assertEqual(replay.controller.wins, 1)
        self.assertEqual({a[0] for a in replay.actions}, {"one", "nine"})

    def test_result_can_close_when_peer_already_in_lobby_and_score_unreadable(self):
        replay = Replay()
        replay.step("battle")
        replay.pair(replay.view("result", ("result_continue",), outcome="win"),
                    replay.view("lobby_ready"))
        self.assertEqual(replay.actions[-1][:2], ("one", "result_continue"))
        self.assertEqual(replay.controller.wins, 0)
        replay.step("lobby_ready", ("match",))
        self.assertEqual(replay.controller.match_id, 2)
        self.assertEqual(replay.actions[-1][1], "match")
        self.assertEqual(replay.controller.wins, 0)

    def test_disconnect_reconnects_while_partner_waits_for_result(self):
        replay = Replay()
        replay.step("battle")
        replay.pair(replay.view("result", ("result_continue",), outcome="loss"),
                    replay.view("disconnected", ("reconnect",)))
        self.assertIn(("nine", "reconnect", (600, 600)), replay.actions)
        replay.step("result", ("result_continue",), outcome="loss")
        self.assertEqual(replay.controller.losses, 1)

    def test_popup_is_closed_only_by_its_observed_button(self):
        replay = Replay()
        replay.step("popup")
        self.assertFalse(replay.actions)
        replay.step("popup", ("close",))
        self.assertEqual([a[1] for a in replay.actions], ["close", "close"])

    def test_auto_is_only_clicked_when_manual_button_observed(self):
        replay = Replay()
        replay.step("battle")
        self.assertFalse(replay.actions)
        replay.step("battle", ("auto",))
        self.assertEqual([a[1] for a in replay.actions], ["auto", "auto"])

    def test_battle_after_skipped_deployment_does_not_claim_lineup_verified(self):
        replay = Replay()
        self.assertEqual(replay.step("battle")["state"], "battle")
        records = [r for r in replay.events if r["event"] == "battle_observed"]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(not r["intended_lineup_verified"] for r in records))
        replay.step("result", ("result_continue",), outcome="win")
        self.assertEqual(replay.controller.wins, 1)

    def test_automatic_deployment_can_advance_despite_unverified_selection(self):
        replay = Replay()
        replay.step("deploying")
        self.assertEqual(replay.deploys, ["one", "nine"])
        self.assertEqual(replay.step("battle")["state"], "battle")

    def test_deployment_failure_retries_next_observation(self):
        replay = Replay()
        calls = []
        def deploy(owner):
            calls.append(owner)
            if len(calls) <= 2:
                raise RuntimeError("transient missing portrait")
            return "confirmed"
        replay.controller.deploy = deploy
        replay.step("deploying")
        replay.step("deploying")
        self.assertEqual(len(calls), 4)
        self.assertFalse(replay.controller.complete)

    def test_stale_unknown_frames_cannot_click_or_falsely_finish(self):
        replay = Replay()
        stale = RoundView("series_result", replay.now - 10, {"collect": (1, 1)})
        replay.pair(stale, stale)
        replay.step("unknown", ("match", "collect", "close"))
        self.assertFalse(replay.actions)
        self.assertFalse(replay.controller.complete)

    def test_unknown_timeout_keeps_polling_and_recovers_without_operator(self):
        replay = Replay(phase_timeout=3)
        replay.step("unknown")
        replay.now += 5
        status = replay.step("unknown")
        self.assertEqual(status["state"], "auto_rereading")
        self.assertEqual(set(status["recovery_overdue"]), {"one", "nine"})
        replay.step("lobby_ready", ("match",))
        self.assertEqual(replay.actions[-1][1], "match")

    def test_conflicting_results_reread_without_count_or_dismiss(self):
        replay = Replay()
        replay.step("battle")
        status = replay.pair(replay.view("result", ("result_continue",), outcome="win", wins=1, losses=0),
                             replay.view("result", ("result_continue",), outcome="loss", wins=1, losses=0))
        self.assertEqual(status["state"], "result_conflict")
        self.assertEqual(replay.controller.wins, 0)
        self.assertFalse(replay.actions)
        replay.step("result", ("result_continue",), outcome="win")
        self.assertEqual(replay.controller.wins, 1)

    def test_score_consensus_prevents_incrementing_result_twice(self):
        replay = Replay()
        replay.step("battle")
        replay.step("result", ("result_continue",), outcome="win", wins=1, losses=0)
        replay.step("result", ("result_continue",), outcome="win", wins=1, losses=0)
        self.assertEqual(replay.controller.wins, 1)

    def test_conflicting_score_does_not_overwrite_known_score(self):
        replay = Replay()
        replay.match("win")
        replay.pair(replay.view("lobby_ready", wins=5, losses=0),
                    replay.view("lobby_ready", wins=1, losses=0))
        self.assertEqual(replay.controller.wins, 1)

    def test_duplicate_terminal_collect_retries_but_completion_needs_both_returns(self):
        replay = Replay()
        replay.step("series_result", ("collect",), wins=1, losses=2)
        replay.step("series_result", ("collect",), wins=1, losses=2)
        replay.pair(replay.view("lobby_unopened"), replay.view("series_result", ("collect",), wins=1, losses=2))
        self.assertFalse(replay.controller.complete)
        self.assertTrue(replay.step("lobby_unopened")["complete"])
        self.assertEqual((replay.controller.wins, replay.controller.losses), (1, 2))

    def test_terminal_partner_unblocks_lagging_battle_result_without_inventing_score(self):
        replay = Replay()
        replay.step("battle")
        replay.pair(replay.view("result", ("result_continue",), outcome="loss"),
                    replay.view("series_result", ("collect",), wins=1, losses=2))
        self.assertIn(("one", "result_continue", (600, 600)), replay.actions)
        self.assertEqual((replay.controller.wins, replay.controller.losses), (0, 0))
        self.assertFalse(replay.controller.complete)
        replay.step("series_result", ("collect",), wins=1, losses=2)
        self.assertTrue(replay.step("lobby_unopened")["complete"])
        self.assertEqual((replay.controller.wins, replay.controller.losses), (1, 2))

    def test_unopened_lobby_without_terminal_evidence_is_not_completion(self):
        replay = Replay()
        replay.step("lobby_unopened")
        replay.step("lobby_unopened")
        self.assertFalse(replay.controller.complete)
        self.assertFalse(replay.actions)

    def test_completion_requires_both_accounts_currently_in_returned_lobby(self):
        replay = Replay()
        replay.step("series_result", ("collect",), wins=0, losses=2)
        replay.pair(replay.view("lobby_unopened"), replay.view("series_result", ("collect",)))
        replay.pair(replay.view("disconnected", ("reconnect",)), replay.view("lobby_unopened"))
        self.assertFalse(replay.controller.complete)
        self.assertTrue(replay.step("lobby_unopened")["complete"])

    def test_direct_terminal_score_lobby_requires_followup_screenshot(self):
        replay = Replay()
        self.assertFalse(replay.step("lobby_unopened", wins=6, losses=1)["complete"])
        self.assertTrue(replay.step("lobby_unopened", wins=6, losses=1)["complete"])

    def test_checkpoint_restores_pending_result_without_double_count(self):
        replay = Replay()
        replay.match("win")
        state = json.loads(json.dumps(replay.controller.checkpoint()))
        restored = Replay()
        restored.controller.restore(state)
        restored.step("result", ("result_continue",), outcome="win")
        self.assertEqual(restored.controller.wins, 1)
        restored.match("loss")
        restored.match("loss")
        self.assertTrue(restored.finish()["complete"])
        self.assertEqual((restored.controller.wins, restored.controller.losses), (1, 2))

    def test_checkpoint_preserves_one_peer_result_until_other_arrives(self):
        replay = Replay()
        replay.step("battle")
        replay.pair(replay.view("result", outcome="win"), replay.view("disconnected"))
        restored = Replay()
        restored.controller.restore(json.loads(json.dumps(replay.controller.checkpoint())))
        restored.pair(restored.view("lobby_ready"), restored.view("result", ("result_continue",), outcome="win"))
        self.assertEqual(restored.controller.wins, 1)

    def test_completed_checkpoint_never_reenters_match(self):
        replay = Replay()
        replay.step("series_result", ("collect",), wins=0, losses=2)
        replay.step("lobby_unopened")
        restored = Replay()
        restored.controller.restore(replay.controller.checkpoint())
        self.assertTrue(restored.step("lobby_ready", ("match",))["complete"])
        self.assertFalse(restored.actions)

    def test_checkpoint_written_before_input_contains_current_match(self):
        replay = Replay()
        saved = []
        replay.controller.act = lambda *args: saved.append(replay.controller.checkpoint())
        replay.step("lobby_ready", ("match",))
        self.assertEqual(saved[0]["match_id"], 1)
        self.assertTrue(saved[0]["epoch_open"])

    def test_failed_input_is_retried_with_new_evidence(self):
        replay = Replay()
        attempts = []
        def send(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise OSError("temporary input failure")
        replay.controller.act = send
        replay.step("lobby_ready", ("match",))
        replay.step("lobby_ready", ("match",))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(replay.controller.match_id, 1)

    def test_execute_false_never_invokes_input_or_deployment(self):
        replay = Replay()
        view = replay.view("deploying")
        replay.controller.step({"one": view, "nine": view}, execute=False)
        view = replay.view("battle", ("auto",))
        replay.controller.step({"one": view, "nine": view}, execute=False)
        self.assertFalse(replay.actions)
        self.assertFalse(replay.deploys)

    def test_wrong_pair_checkpoint_is_rejected(self):
        replay = Replay()
        state = replay.controller.checkpoint()
        state["owners"] = ["one", "two"]
        with self.assertRaises(ValueError):
            replay.controller.restore(state)

    def test_unobserved_or_invalid_action_coordinates_never_click(self):
        replay = Replay()
        view = RoundView("battle", replay.now, {"auto": (float("nan"), 1)})
        replay.pair(view, view)
        self.assertFalse(replay.actions)

    def test_out_of_order_recent_screenshot_cannot_reenter_queue(self):
        replay = Replay()
        replay.step("lobby_ready", ("match",))
        old = RoundView("lobby_ready", replay.now + 1, {"match": (1, 1)})
        replay.step("matching", ("cancel_match",))
        replay.pair(old, old)
        self.assertEqual(replay.controller.match_id, 1)
        self.assertEqual(sum(a[1] == "match" for a in replay.actions), 1)


if __name__ == "__main__":
    unittest.main()
