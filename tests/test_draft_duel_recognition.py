"""Recognition checks against real screenshots; no clicks or emulator IO."""
from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from tasks.DraftDuel.assets import (
    ADD_TEAMMATE_BUTTON, ADD_TEAMMATE_ICON, CLOSED_LABEL, FREE_LABEL, HELP_BUTTON,
    HELP_ICON, LOBBY_ANCHORS,
)
from tasks.DraftDuel.recognition import LobbyState, observe_lobby


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "draft_duel"


def fixture(name):
    image = cv2.imdecode(np.fromfile(str(FIXTURES / name), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError("real fixture missing: " + name)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def obscure(image, asset):
    image = image.copy()
    x, y, width, height = asset.roi
    margin = asset.search_margin + 2
    image[max(0, y - margin):y + height + margin, max(0, x - margin):x + width + margin] = 0
    return image


class DraftDuelRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lobby = fixture("lobby_closed_20261018.png")
        cls.rules = fixture("rules_top_20261018.png")

    def test_real_closed_free_lobby_requires_combined_evidence(self):
        result = observe_lobby(self.lobby)
        self.assertEqual(result.state, LobbyState.LOBBY_NOT_STARTED)
        self.assertTrue(result.is_lobby)
        self.assertFalse(result.is_closed)
        self.assertTrue(result.round_not_started)
        self.assertTrue(result.is_free)
        self.assertGreater(result.confidence, 0.98)
        self.assertFalse(result.can_start)
        self.assertTrue(result.is_help_visible)
        self.assertTrue(result.is_add_teammate_visible)
        self.assertTrue({asset.name for asset in LOBBY_ANCHORS}.issubset(result.evidence))

    def test_real_rules_overlay_is_unknown_despite_title_and_battle_background(self):
        result = observe_lobby(self.rules)
        self.assertEqual(result.state, LobbyState.UNKNOWN)
        self.assertFalse(result.is_lobby)
        self.assertFalse(result.is_closed)
        self.assertFalse(result.is_free)
        self.assertFalse(result.can_start)

    def test_light_jpeg_compression_still_recognizes_lobby(self):
        for quality in (90, 80):
            with self.subTest(quality=quality):
                success, encoded = cv2.imencode(".jpg", cv2.cvtColor(self.lobby, cv2.COLOR_RGB2BGR),
                                               (cv2.IMWRITE_JPEG_QUALITY, quality))
                self.assertTrue(success)
                decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                result = observe_lobby(decoded, color_order="BGR")
                self.assertEqual(result.state, LobbyState.LOBBY_NOT_STARTED)
                self.assertTrue(result.is_free)

    def test_occluding_any_required_anchor_prevents_lobby_actions(self):
        for asset in LOBBY_ANCHORS:
            with self.subTest(asset=asset.name):
                result = observe_lobby(obscure(self.lobby, asset))
                self.assertEqual(result.state, LobbyState.UNKNOWN)
                self.assertFalse(result.is_lobby)
                self.assertFalse(result.is_free)
                self.assertFalse(result.is_help_visible)
                self.assertFalse(result.is_add_teammate_visible)

    def test_hidden_closed_label_cannot_be_interpreted_as_open(self):
        result = observe_lobby(obscure(self.lobby, CLOSED_LABEL))
        self.assertEqual(result.state, LobbyState.LOBBY_UNKNOWN)
        self.assertTrue(result.is_lobby)
        self.assertFalse(result.is_closed)
        self.assertFalse(result.can_start)
        self.assertIn("round_state_not_observed", result.evidence)

    def test_free_label_must_be_positive_evidence_in_its_own_region(self):
        hidden = obscure(self.lobby, FREE_LABEL)
        result = observe_lobby(hidden)
        self.assertEqual(result.state, LobbyState.LOBBY_NOT_STARTED)
        self.assertFalse(result.is_free)
        x, y, width, height = FREE_LABEL.roi
        hidden[200:200 + height, 500:500 + width] = self.lobby[y:y + height, x:x + width]
        self.assertFalse(observe_lobby(hidden).is_free)

    def test_only_a_battle_glyph_does_not_recognize_this_mode(self):
        battle_only = np.zeros_like(self.lobby)
        battle_only[560:715, 1130:1275] = self.lobby[560:715, 1130:1275]
        result = observe_lobby(battle_only)
        self.assertEqual(result.state, LobbyState.UNKNOWN)
        self.assertFalse(result.is_free)

    def test_uniform_dark_overlay_is_rejected_despite_normalized_correlation(self):
        dark = (self.lobby.astype(np.float32) * 0.45).astype(np.uint8)
        self.assertEqual(observe_lobby(dark).state, LobbyState.UNKNOWN)

    def test_unmatched_and_invalid_frames_fail_closed(self):
        invalid = (None, np.zeros_like(self.lobby), np.full_like(self.lobby, 255),
                   np.flip(self.lobby, axis=1).copy(), self.lobby[:, :1000],
                   cv2.resize(self.lobby, (640, 360)), self.lobby[:, :, 0],
                   self.lobby.astype(np.float32), np.zeros((720, 1280, 4), np.uint8))
        for image in invalid:
            with self.subTest(shape=getattr(image, "shape", None)):
                result = observe_lobby(image)
                self.assertEqual(result.state, LobbyState.UNKNOWN)
                self.assertFalse(result.can_start)
                self.assertFalse(result.is_free)

    def test_missing_assets_fail_closed_instead_of_guessing(self):
        with patch("tasks.DraftDuel.recognition._template", return_value=None):
            self.assertEqual(observe_lobby(self.lobby).state, LobbyState.UNKNOWN)

    def test_non_battle_buttons_are_measured_inside_observed_icons(self):
        for button, template in ((HELP_BUTTON, HELP_ICON), (ADD_TEAMMATE_BUTTON, ADD_TEAMMATE_ICON)):
            with self.subTest(button=button.name):
                bx, by = button.center
                tx, ty, tw, th = template.roi
                self.assertTrue(tx <= bx < tx + tw and ty <= by < ty + th)
        self.assertEqual(HELP_BUTTON.center, (295, 28))
        self.assertEqual(ADD_TEAMMATE_BUTTON.center, (1013, 356))
        result = observe_lobby(obscure(self.lobby, ADD_TEAMMATE_ICON))
        self.assertFalse(result.is_add_teammate_visible)
        self.assertTrue(result.is_lobby)


if __name__ == "__main__":
    unittest.main()
