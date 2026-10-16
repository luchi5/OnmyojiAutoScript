"""Real ticket-number crops against the bundled OCR model, without an OCR server.

Run with toolkit/python.exe -m unittest dev_tools.test_activity_ticket_ocr.
Only the CPU recognition model is loaded; no device, scheduler or logger starts.
"""

import ast
from pathlib import Path
import re
import unittest

import cv2
import numpy as np
from ppocronnx.rec import TextRecognizer


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'dev_tools/fixtures/activity_202610'


class TicketOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / 'tasks/ActivityShikigami/current_event.py').read_text(encoding='utf-8'))
        assets = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CurrentEventAssets')
        balance = next(node.value for node in assets.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'O_TICKET_BALANCE'
                               for target in node.targets))
        cls.roi = ast.literal_eval(next(keyword.value for keyword in balance.keywords if keyword.arg == 'roi'))
        parser = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'parse_ticket_budget')
        ns = {'re': re}
        exec(compile(ast.Module(body=[parser], type_ignores=[]), '<ticket_parser>', 'exec'), ns)
        cls.parse = staticmethod(ns['parse_ticket_budget'])
        cls.recognizer = TextRecognizer(ort_providers=['CPUExecutionProvider'])

    def read_budget(self, frame, cost='x5'):
        x, y, w, h = self.roi
        results, _ = self.recognizer([frame[y:y+h, x:x+w]])
        text, score = results[0]
        # Match Single OCR's confidence gate; a blank is never an explicit zero.
        return self.parse(text if score >= 0.6 else '', cost), text, score

    def balance_frame(self, value):
        crop = cv2.imread(str(FIXTURES / f'ticket_balance_{value}.png'))
        self.assertIsNotNone(crop)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        # This fixture preserves the header around the failing crop at (1095, 16).
        height, width = crop.shape[:2]
        frame[16:16+height, 1095:1095+width] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return frame

    def test_reported_255_is_recognized_without_stripping_punctuation(self):
        budget, text, score = self.read_budget(self.balance_frame(255))
        self.assertEqual(budget, (255, 5), (text, score))
        self.assertIsNone(self.parse(':255', 'x5'))

    def test_reported_385_is_recognized_without_stripping_punctuation(self):
        budget, text, score = self.read_budget(self.balance_frame(385), 'x1')
        self.assertEqual(budget, (385, 1), (text, score))
        for text in ('：385', '_385'):
            self.assertIsNone(self.parse(text, 'x1'))

    def test_existing_ticket_pages_still_recognize_balance(self):
        for name, expected, cost in [('ticket_before', 235, 'x5'), ('ticket_after', 230, 'x5'),
                                     ('hard_current', 230, 'x5'), ('simple', 230, 'x1'),
                                     ('ticket_unlock', 230, 'x5')]:
            with self.subTest(name=name):
                frame = cv2.cvtColor(cv2.imread(str(FIXTURES / (name + '.png'))), cv2.COLOR_BGR2RGB)
                budget, text, score = self.read_budget(frame, cost)
                self.assertEqual(budget, (expected, int(cost[-1])), (text, score))

    def test_blank_crop_is_unknown_instead_of_zero(self):
        budget, _, _ = self.read_budget(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertIsNone(budget)


if __name__ == '__main__':
    unittest.main()
