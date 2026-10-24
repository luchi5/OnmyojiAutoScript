"""Real archived DraftDuel pixels + explicitly synthetic unobserved scenes.

Synthetic text detector boxes exercise routing/context, not OCR accuracy.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from tasks.DraftDuel.round_vision import RoundVision, ROOT


class DetectedText:
    def __init__(self, labels=()):
        self.model = self
        self.labels = labels

    def detect_and_ocr(self, image):
        return [SimpleNamespace(ocr_text=text, box=box, score=confidence)
                for text, box, confidence in self.labels]


def label(text, box=(450, 300, 800, 345), confidence=.99):
    return text, box, confidence


def read_image(path):
    return cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)


class RoundVisionTest(unittest.TestCase):
    def setUp(self):
        self.ocr = DetectedText()
        self.vision = RoundVision(self.ocr)
        self.blank = np.zeros((720, 1280, 3), np.uint8)

    def paint(self, name, xy):
        template = self.vision.templates[name]
        x, y = xy
        h, w = template.shape[:2]
        self.blank[y:y+h, x:x+w] = template

    def test_single_loss_overlay_is_not_terminal(self):
        self.paint('series_fan', (600, 280))
        self.paint('continue', (550, 680))
        self.paint('loss_red', (610, 368))
        self.paint('central_loss_gray', (655, 366))
        self.ocr.labels = [label('0番胜', (540, 275, 775, 365))]
        result = self.vision.read(self.blank)
        self.assertEqual(result.phase, 'result')
        self.assertEqual((result.wins, result.losses), (0, 1))
        self.assertIsNone(result.outcome)
        self.assertEqual(set(result.actions), {'result_continue'})

