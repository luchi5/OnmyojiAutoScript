import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import numpy as np

from tasks.DraftDuel.local_draft import Frame, LocalDraft, same_selection, DraftVision, TimedOCR
from tasks.DraftDuel.ratings import Ratings, rating_policy
from tasks.DraftDuel.strategy import Candidate, score_team, rank_draft_choices


class LocalDraftTests(unittest.TestCase):
    def setUp(self):
        self.ratings = Ratings()
        self.card = self.ratings.annotate(Candidate("01:r1:s0", "01", "椒图", confidence=1))
        self.frame = Frame("01", 100, 1, 30, (self.card,), "1:椒图", np.zeros((35, 210, 3)))

    def test_fresh_same_choice_allowed(self):
        self.assertTrue(same_selection(self.frame, replace(self.frame, captured=101), 102))

    def test_changed_round_names_roster_and_late_frames_rejected(self):
        for frame in (None, replace(self.frame, signature="2:椒图"),
                      replace(self.frame, owner="09"),
                      replace(self.frame, roster=np.full((35, 210, 3), 50)),
                      replace(self.frame, seconds=1)):
            self.assertFalse(same_selection(self.frame, frame, 101))
        self.assertFalse(same_selection(self.frame, self.frame, 104))

    def test_source_flags_and_missing_score_preserved(self):
        self.assertIn("composite_formula_mismatch", self.ratings.profiles["茨木童子"]["flags"])
        self.assertIsNone(self.ratings.profiles["雪兔"]["strength"])
        self.assertEqual(self.card.strength_source, "评分总表!E7")

    def test_counterattack_combo_requires_observed_soul(self):
        momiji = Candidate("09:r1:s0", "09", "心狩鬼女红叶", confidence=1)
        plain = self.ratings.annotate(momiji)
        counter = self.ratings.annotate(replace(momiji, soul_tags={"counterattack"}))
        a = score_team((self.card, plain), policy=rating_policy())
        b = score_team((self.card, counter), policy=rating_policy())
        self.assertAlmostEqual(b.total-a.total, 4)

    def test_joint_pair_plan_uses_both_owners(self):
        other = self.ratings.annotate(Candidate("09:r1:s0", "09", "辉夜姬", confidence=1))
        plans = rank_draft_choices((self.card, other), (), ("01", "09"), policy=rating_policy())
        self.assertEqual({c.owner for c in plans[0].choices}, {"01", "09"})

    def test_timeout_tap_is_not_retried(self):
        from unittest.mock import patch
        devices = {"01": Mock(), "09": Mock()}
        devices["01"].tap.side_effect = TimeoutError("tap outcome unknown")
        runner = LocalDraft(devices)
        try:
            with patch("tasks.DraftDuel.local_draft.time.monotonic", return_value=101):
                with self.assertRaises(TimeoutError):
                    runner.commit(self.frame, self.frame, self.card)
                self.assertEqual(runner.commit(self.frame, self.frame, self.card), "already_attempted")
            devices["01"].tap.assert_called_once_with(0)
        finally:
            runner.close()


class RealFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = DraftVision(TimedOCR())
        cls.root = Path(__file__).parent / "fixtures/draft_duel/selection"

    def read(self, filename):
        import cv2
        return self.vision.read(cv2.imread(str(self.root / filename)), filename[:2])

    def test_all_nine_candidate_names(self):
        expected = {
            "01-start.png": ["萤草", "市加美", "寻森小鹿男"],
            "09-start.png": ["龙珏", "辉夜姬", "惠比寿"],
            "09-draft2.png": ["椒图", "风狸", "蝉冰雪女"],
        }
        for filename, names in expected.items():
            with self.subTest(filename=filename):
                frame = self.read(filename)
                self.assertIsNotNone(frame)
                self.assertEqual([c.name for c in frame.candidates], names)

    def test_waiting_and_lobby_are_not_actionable(self):
        for filename in ("01-draft2.png", "01-manual-draft.png"):
            self.assertIsNone(self.read(filename))

    def test_finished_roster_lobby_is_distinct_from_active_selection(self):
        import cv2
        for filename in ('01-start.png', '09-start.png'):
            self.assertFalse(self.vision.finished_lobby(cv2.imread(str(self.root/filename))))
        for filename in ('01-finished.png', '09-finished.png'):
            self.assertTrue(self.vision.finished_lobby(cv2.imread(str(self.root/filename))))

    def test_missing_selection_button_is_not_actionable(self):
        import cv2
        image = cv2.imread(str(self.root / "01-start.png"))
        image[608:710, 340:460] = 0
        self.assertIsNone(self.vision.read(image, "01"))

    def test_changed_page_never_tapped(self):
        devices = {"01": Mock(), "09": Mock()}
        runner = LocalDraft(devices)
        try:
            frame = self.read("01-start.png")
            self.assertEqual(runner.commit(frame, None, frame.candidates[0]), "changed_or_expired")
            devices["01"].tap.assert_not_called()
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
