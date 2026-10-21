from pathlib import Path
import unittest
import cv2
from tasks.DraftDuel.local_draft import TimedOCR
from tasks.DraftDuel.lineup_runtime import read_roster, compute_lineup, RoundDeploymentVision


ROOT = Path(__file__).parents[1]


class RoundLineupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ocr = TimedOCR()

    def test_observed_rosters_and_111_speed(self):
        source = ROOT / "tests/fixtures/draft_duel/round2"
        all_cards = []
        for owner in ("01", "09"):
            cards, portraits = read_roster(cv2.imread(str(source/(owner+"-tool-result.png"))),
                    cv2.imread(str(source/(owner+"-tool-selection.png"))), owner, self.ocr)
            self.assertEqual(len(portraits), 5)
            all_cards += cards
        self.assertEqual(next(c.stats.speed for c in all_cards if c.name == "流光追月神"), 111)
        lineups, evaluation = compute_lineup(all_cards)
        self.assertEqual(evaluation["combinations"], 100)
        for owner, lineup in lineups.items():
            self.assertEqual(len(set(lineup)), 3)
            self.assertTrue(set(lineup).issubset({c.name for c in all_cards if c.owner == owner}))

    def test_portraits_from_lobby_find_reordered_full_cards(self):
        names = ("ichikami", "onigiri", "momiji", "masakado", "tamamo")
        portraits = {n: cv2.imread(str(ROOT/"tasks/DraftDuel/res/deployment"/(n+"_top.png")))
                     for n in names}
        vision = RoundDeploymentVision(self.ocr, portraits)
        for stage, x in ((1, 550), (2, 665), (3, 436)):
            image = cv2.imread(str(ROOT/"tests/fixtures/draft_duel/deployment"/f"stage{stage}.png"))
            frame = vision.read(image)
            self.assertIsNotNone(frame)
            self.assertAlmostEqual(frame.cards["masakado"][0], x, delta=3)
            self.assertEqual(len(frame.cards), 5)

    def test_wrong_round_portraits_abstain(self):
        source = ROOT/"tests/fixtures/draft_duel/round2"
        _, portraits = read_roster(cv2.imread(str(source/"01-tool-result.png")),
                                  cv2.imread(str(source/"01-tool-selection.png")), "01", self.ocr)
        vision = RoundDeploymentVision(self.ocr, portraits)
        old = cv2.imread(str(ROOT/"tests/fixtures/draft_duel/deployment/stage2.png"))
        self.assertIsNone(vision.read(old))

    def test_missing_marker_never_infers_stage_from_confirmation_count(self):
        names = ("ichikami", "onigiri", "momiji", "masakado", "tamamo")
        portraits = {n: cv2.imread(str(ROOT/"tasks/DraftDuel/res/deployment"/(n+"_top.png")))
                     for n in names}
        vision = RoundDeploymentVision(self.ocr, portraits)
        image = cv2.imread(str(ROOT/"tests/fixtures/draft_duel/deployment/stage3.png"))
        self.assertEqual(vision.read(image).stage, 3)
        vision.completed_stage = 1  # A previous phase was completed by the user.
        image[24:112, 222:294] = 0
        self.assertIsNone(vision.read(image))
        earlier = cv2.imread(str(ROOT/"tests/fixtures/draft_duel/deployment/stage2.png"))
        self.assertIsNone(vision.read(earlier))


if __name__ == "__main__":
    unittest.main()
