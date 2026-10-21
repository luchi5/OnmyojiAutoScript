import hashlib
import json
from pathlib import Path
import unittest

from tasks.DraftDuel.ratings import Ratings, compiled_policy, rating_policy
from tasks.DraftDuel.strategy import Candidate, Stats, score_team, rank_draft_choices


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.ratings = Ratings()

    def card(self, name, owner="A", **kwargs):
        return self.ratings.annotate(Candidate(owner+":"+name, owner, name, confidence=1, **kwargs))

    def terms(self, *cards):
        return {t.key: t.value for t in score_team(cards, policy=rating_policy()).terms}

    def test_complete_source_coverage_and_hash(self):
        path = Path(__file__).parents[1] / "tasks/DraftDuel/ratings.json"
        source = json.loads(path.read_text(encoding="utf-8"))
        compiled = compiled_policy()
        self.assertEqual(set(source["profiles"]), set(compiled["profiles"]))
        self.assertEqual(len(compiled["profiles"]), 243)
        self.assertEqual(compiled["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        for name, profile in compiled["profiles"].items():
            card = self.card(name)
            self.assertEqual(card.strength, profile["strength"])
            self.assertIn("name:"+name, card.roles)
            self.assertTrue(profile["source"])

    def test_unknowns_do_not_get_fake_strength(self):
        for name in ("大道寺芝士", "雪兔", "资料中不存在的式神"):
            self.assertIsNone(self.card(name).strength)

    def test_alias_does_not_mix_sp(self):
        self.assertEqual(self.card("萤草").strength, self.card("莹草").strength)
        self.assertNotEqual(self.card("萤草").roles, self.card("渺念萤草").roles)

    def test_numeric_condition_needs_teammate(self):
        unit = self.card("烬天玉藻前")
        self.assertEqual(unit.strength, 70)
        self.assertNotIn("group:numeric_烬天玉藻前", self.terms(unit))
        self.assertEqual(self.terms(unit, self.card("云间不见岳", "B"))["group:numeric_烬天玉藻前"], 2)

    def test_full_combo_not_just_two_members(self):
        a, b = self.card("浮世青行灯"), self.card("心狩鬼女红叶", "B")
        self.assertNotIn("group:combo_4", self.terms(a, b))
        self.assertGreater(self.terms(a, b, self.card("古笼火"))["group:combo_4"], 0)

    def test_named_ally_conflict_once(self):
        terms = self.terms(self.card("帝释天"), self.card("纺愿缘结神", "B"))
        self.assertEqual(terms["group:avoid_帝释天_纺愿缘结神"], -3)

    def test_summon_conflict(self):
        terms = self.terms(self.card("千姬"), self.card("月读", "B"))
        self.assertTrue(any(k.startswith("group:summon_") and v < 0 for k, v in terms.items()))

    def test_soul_recommendation_is_not_observation(self):
        c = self.card("大天狗")
        self.assertNotIn("observed:aoe_haiyue", c.roles)
        self.assertIn("observed:aoe_haiyue", self.card("大天狗", soul_tags={"海月火玉"}).roles)
        self.assertNotIn("observed:fast_aoe", c.roles)
        self.assertIn("observed:fast_aoe", self.card("大天狗", stats=Stats(speed=221)).roles)

    def test_composite_formula_not_silently_repaired(self):
        p = compiled_policy()["profiles"]["茨木童子"]
        self.assertIn("composite_formula_mismatch", p["flags"])

    def test_missing_fire_favors_supply(self):
        selected = (self.card("心狩鬼女红叶"), self.card("平将门", "B"))
        candidates = (self.card("辉夜姬"), self.card("天剑韧心鬼切"))
        plans = rank_draft_choices(candidates, selected, ("A", "B"),
                                  active_owners=("A",), policy=rating_policy())
        self.assertEqual(plans[0].choices[0].name, "辉夜姬")

    def test_single_candidate_cannot_fill_multiple_combo_groups(self):
        from tasks.DraftDuel.strategy import GroupRule, StrategyPolicy
        candidate = self.card("辉夜姬")
        policy = StrategyPolicy(groups=(GroupRule("test", (frozenset({"fire"}),
                                                           frozenset({"fire"})), 100, "test"),))
        self.assertFalse(any(t.key == "group:test" for t in score_team((candidate,), policy=policy).terms))


if __name__ == "__main__":
    unittest.main()
