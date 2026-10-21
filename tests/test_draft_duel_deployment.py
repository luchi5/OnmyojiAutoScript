from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock
import cv2

from tasks.DraftDuel.deployment import Deployment, DeploymentVision, DeploymentController
from tasks.DraftDuel.local_draft import TimedOCR


class DeploymentImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = DeploymentVision(TimedOCR())
        cls.folder = Path(__file__).parent / "fixtures/draft_duel/deployment"

    def test_real_right_side_stages_and_reordered_cards(self):
        expected = [(8, "ichikami", 547), (7, "masakado", 661), (9, "momiji", 433)]
        for stage, (seconds, current, masakado_x) in enumerate(expected, 1):
            with self.subTest(stage=stage):
                frame = self.vision.read(cv2.imread(str(self.folder / f"stage{stage}.png")))
                self.assertIsNotNone(frame)
                self.assertEqual((frame.side, frame.stage, frame.seconds, frame.current),
                                 ("right", stage, seconds, current))
                self.assertAlmostEqual(frame.cards["masakado"][0], masakado_x, delta=3)

    def test_synthetic_swapped_top_halves_left_side(self):
        # Synthetic evidence, not a live left-side deployment verification.
        image = cv2.imread(str(self.folder / "stage2.png"))
        left, right = image[24:112, 78:294].copy(), image[24:112, 294:510].copy()
        image[24:112, 78:294], image[24:112, 294:510] = right, left
        frame = self.vision.read(image)
        self.assertEqual((frame.side, frame.stage, frame.current), ("left", 2, "masakado"))
        self.assertEqual(frame.target, (330, 340))

    def test_missing_button_or_teammate_abstains(self):
        for box in [(540, 627, 1100, 1233), (24, 112, 78, 510)]:
            image = cv2.imread(str(self.folder / "stage2.png"))
            y1, y2, x1, x2 = box
            image[y1:y2, x1:x2] = 0
            self.assertIsNone(self.vision.read(image))


class DeploymentActions(unittest.TestCase):
    def setUp(self):
        self.io = Mock()
        self.frame = Deployment(100, "right", 2, 20, "onigiri", (940, 340),
                                {"masakado": (660, 588)})
        self.runner = DeploymentController(self.io, Mock(),
                    ("ichikami", "masakado", "momiji"), clock=lambda: 100.5)

    def test_drag_verify_then_confirm(self):
        self.runner.observe = Mock(side_effect=[
            self.frame, self.frame, replace(self.frame, current="masakado")])
        self.assertEqual(self.runner.step(True), "confirmation_sent")
        self.io.drag.assert_called_once_with((660, 588), (940, 340))
        self.io.confirm.assert_called_once()

    def test_wrong_portrait_after_drag_never_confirms(self):
        self.runner.observe = Mock(return_value=self.frame)
        self.assertEqual(self.runner.step(True), "drag_not_verified_no_confirmation")
        self.io.confirm.assert_not_called()
        self.assertEqual(self.runner.step(True), "needs_manual_selection")
        self.io.drag.assert_called_once()

    def test_correct_default_confirms_without_drag(self):
        self.runner.observe = Mock(return_value=replace(self.frame, current="masakado"))
        self.assertEqual(self.runner.step(True), "confirmation_sent")
        self.assertEqual(self.runner.step(True), "confirmation_already_sent")
        self.io.drag.assert_not_called()
        self.io.confirm.assert_called_once()

    def test_last_five_seconds_and_stale_frame_handoff(self):
        for frame in (replace(self.frame, seconds=5), replace(self.frame, captured=95)):
            self.runner.observe = Mock(return_value=frame)
            self.assertEqual(self.runner.step(True), "handoff_last_five_seconds")
        self.io.drag.assert_not_called()
        self.io.confirm.assert_not_called()

    def test_manual_change_between_reads_abstains(self):
        self.runner.observe = Mock(side_effect=[
            self.frame, replace(self.frame, stage=3, current="momiji")])
        self.assertEqual(self.runner.step(True), "changed_or_expired")
        self.io.drag.assert_not_called()
        self.io.confirm.assert_not_called()

    def test_observe_mode_never_changes_game(self):
        self.runner.observe = Mock(return_value=self.frame)
        self.assertEqual(self.runner.step(), "would_drag:masakado")
        self.io.drag.assert_not_called()
        self.io.confirm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
