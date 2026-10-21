"""Joint draft / lineup contract tests; standard library only, no game IO."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from tasks.DraftDuel.strategy import (
    Candidate, Matchup, RoleCatalog, Stats, StrategyPolicy, Synergy,
    rank_draft_choices, rank_lineups, score_team,
)


def card(owner, index, roles=(), **kwargs):
    return Candidate("{}:{}".format(owner, index), owner, "unit-{}".format(index),
                     roles=roles, confidence=kwargs.pop("confidence", 1), **kwargs)


class DraftDuelStrategyTests(unittest.TestCase):
    def setUp(self):
        self.pool = tuple(card(owner, index) for owner in ("A", "B") for index in range(5))

    def test_all_lineups_contain_exactly_three_per_owner(self):
        plans = rank_lineups(self.pool, ("A", "B"))
        self.assertEqual(len(plans), 100)  # C(5,3) * C(5,3)
        for plan in plans:
            self.assertEqual(Counter(item.owner for item in plan.choices), {"A": 3, "B": 3})
            self.assertEqual(len({item.candidate_id for item in plan.choices}), 6)

    def test_locks_on_both_accounts_are_never_replaced(self):
        plans = rank_lineups(self.pool, ("A", "B"), locked_ids=("A:4", "B:3", "B:4"))
        self.assertEqual(len(plans), 18)
        for plan in plans:
            self.assertTrue({"A:4", "B:3", "B:4"}.issubset(
                {item.candidate_id for item in plan.choices}))

    def test_complete_locks_return_exact_plan(self):
        locks = ("A:1", "A:2", "A:3", "B:1", "B:2", "B:3")
        plans = rank_lineups(self.pool, ("A", "B"), locked_ids=locks)
        self.assertEqual(len(plans), 1)
        self.assertEqual({item.candidate_id for item in plans[0].choices}, set(locks))

    def test_incomplete_owner_cannot_be_filled_from_other_owner(self):
        short_pool = tuple(item for item in self.pool if item.owner == "A" or item.candidate_id == "B:0")
        self.assertEqual(rank_lineups(short_pool, ("A", "B")), ())
        self.assertEqual(len(rank_lineups(short_pool, ("A", "B"), per_owner=1)), 5)

    def test_invalid_owner_or_id_or_lock_rejected(self):
        cases = (
            lambda: rank_lineups(self.pool, ("A", "A")),
            lambda: rank_lineups(self.pool, ("A", "C")),
            lambda: rank_lineups(self.pool + (self.pool[0],), ("A", "B")),
            lambda: rank_lineups(self.pool, ("A", "B"), locked_ids=("missing",)),
            lambda: rank_lineups(self.pool, ("A", "B"), locked_ids=("A:0", "A:0")),
            lambda: rank_lineups(self.pool, ("A", "B"), locked_ids=("A:0", "A:1", "A:2", "A:3")),
            lambda: rank_lineups(self.pool, ("A", "B"), per_owner=True),
            lambda: rank_lineups(self.pool, ("A", "B"), per_owner=0),
        )
        for attempt in cases:
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                attempt()

    def test_missing_stat_is_not_a_zero_measurement(self):
        missing = card("A", 0)
        zero = replace(missing, stats=Stats(speed=0))
        observed = replace(missing, stats=Stats(speed=140))
        unknown_score, zero_score = score_team((missing,)), score_team((zero,))
        self.assertIsNone(missing.stats.speed)
        self.assertFalse(any(term.key == "stat:A:0:speed" for term in unknown_score.terms))
        zero_term = next(term for term in zero_score.terms if term.key == "stat:A:0:speed")
        self.assertEqual(zero_term.value, 0)
        self.assertTrue(any("speed" in warning for warning in unknown_score.warnings))
        self.assertFalse(any("speed" in warning for warning in zero_score.warnings))
        self.assertGreater(score_team((observed,)).total, unknown_score.total)

    def test_joint_current_picks_repair_missing_role(self):
        selected = (card("A", "old", ("damage",)), card("B", "old", ("heal",)))
        visible = (card("A", "fire", ("fire",)), card("A", "dmg", ("damage",)),
                   card("B", "shield", ("shield",)), card("B", "dmg", ("damage",)))
        plans = rank_draft_choices(visible, selected, ("A", "B"))
        self.assertEqual(len(plans), 4)
        self.assertEqual({item.candidate_id for item in plans[0].choices}, {"A:fire", "B:shield"})
        for plan in plans:
            self.assertTrue(set(plan.choices).issubset(set(visible)))
            self.assertEqual(Counter(item.owner for item in plan.choices), {"A": 1, "B": 1})

    def test_only_unconfirmed_owner_selects_when_other_finished_round(self):
        selected = (card("A", "confirmed", ("fire",)),)
        visible = (card("B", 1, ("fire",)), card("B", 2, ("damage",)))
        plans = rank_draft_choices(visible, selected, ("A", "B"), active_owners=("B",))
        self.assertEqual(plans[0].choices[0].candidate_id, "B:2")
        self.assertEqual(len(plans[0].choices), 1)
        self.assertEqual(rank_draft_choices(visible, selected, ("A", "B")), ())

    def test_draft_duplicate_id_between_selected_and_visible_rejected(self):
        with self.assertRaises(ValueError):
            rank_draft_choices((self.pool[0],), (self.pool[0],), ("A", "B"))
        with self.assertRaises(ValueError):
            rank_draft_choices(self.pool, (), ("A", "B"), active_owners=("A", "A"))

    def test_heavy_fire_overlap_penalty_is_explicit_and_reduced_by_supply(self):
        heavy = (card("A", 0, ("heavy_fire",)), card("B", 0, ("heavy_fire",)))
        no_fire = score_team(heavy)
        supplied = score_team(heavy + (card("A", 1, ("fire",)),))
        term = lambda result, key: next(item.value for item in result.terms if item.key == key)
        self.assertLess(term(no_fire, "heavy_fire"), 0)
        self.assertEqual(term(supplied, "heavy_fire"), 0)
        self.assertLess(term(no_fire, "missing_fire"), term(supplied, "missing_fire"))

    def test_synergy_requires_distinct_candidates_and_can_use_soul_tags(self):
        policy = StrategyPolicy(synergies=(Synergy("followup", "basic_attack", 2, "reviewed combo"),))
        both = card("A", 0, ("basic_attack",), soul_tags=frozenset(("followup",)))
        single = score_team((both,), policy=policy)
        pair = score_team((both, card("B", 0, ("basic_attack",))), policy=policy)
        self.assertFalse(any(term.key.startswith("synergy:") for term in single.terms))
        self.assertEqual(next(term.value for term in pair.terms if term.key == "synergy:0"), 2)

    def test_matchup_uses_only_supplied_observed_opponent_tags(self):
        policy = StrategyPolicy(matchups=(Matchup("control", "dispel", 2, "reviewed response"),))
        friendly = (card("A", 0, ("dispel",)),)
        absent = score_team(friendly, policy=policy)
        unrelated = score_team(friendly, policy=policy, opponent_tags=("damage",))
        seen = score_team(friendly, policy=policy, opponent_tags=("control",))
        self.assertEqual(absent.total, unrelated.total)
        self.assertEqual(seen.total, absent.total + 2)

    def test_confidence_affects_score_and_warns_without_inventing_stats(self):
        known = card("A", 0, ("fire",), stats=Stats(speed=140))
        uncertain = replace(known, confidence=0.2)
        self.assertGreater(score_team((known,)).total, score_team((uncertain,)).total)
        self.assertTrue(any("低置信" in warning for warning in score_team((uncertain,)).warnings))
        self.assertIsNone(uncertain.stats.attack)

    def test_order_is_deterministic_including_tied_scores(self):
        forward = rank_lineups(self.pool, ("A", "B"))
        backward = rank_lineups(tuple(reversed(self.pool)), ("B", "A"))
        self.assertEqual(forward, backward)
        self.assertEqual(rank_draft_choices(self.pool, (), ("A", "B")),
                         rank_draft_choices(tuple(reversed(self.pool)), (), ("B", "A")))

    def test_role_catalog_is_versioned_and_does_not_add_stats_or_soul_bonuses(self):
        data = {"schema_version": 1, "version": "reviewed-1", "profiles": {
            "unit-0": {"roles": ["fire"], "confidence": 0.7, "source": "manual sample"}}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "roles.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            catalog = RoleCatalog.from_json(path)
            annotated = catalog.annotate(card("A", 0))
            self.assertEqual(catalog.version, "reviewed-1")
            self.assertEqual(annotated.roles, frozenset(("fire",)))
            self.assertEqual(annotated.confidence, 0.7)
            self.assertEqual(annotated.stats, Stats())
            self.assertEqual(annotated.soul_tags, frozenset())
            unknown = catalog.annotate(card("A", 99))
            self.assertEqual(unknown.roles, frozenset())
            self.assertEqual(unknown.confidence, 0.25)
            data["schema_version"] = 2
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                RoleCatalog.from_json(path)

    def test_invalid_measurements_confidence_and_tag_shapes_rejected(self):
        for create in (lambda: Stats(speed=float("nan")), lambda: Stats(attack=-1),
                       lambda: card("A", 0, confidence=1.1), lambda: card("A", 0, "fire")):
            with self.subTest(create=create), self.assertRaises(ValueError):
                create()


if __name__ == "__main__":
    unittest.main()
