"""Offline ordinary-summon recognition using a cropped real skin screenshot."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class SummonSkinTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((ROOT / 'module/atom/image.py').read_text('utf-8-sig'))
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'RuleImage')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'match')
        ns = {'np': np, 'cv2': cv2}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<match>', 'exec'), ns)
        self.match = ns['match']
        metadata = json.loads((ROOT / 'tasks/WeeklyTrifles/broken_amulet/image.json').read_text('utf-8'))[0]
        self.roi = tuple(map(int, metadata['roiBack'].split(',')))
        template = cv2.imread(str(ROOT / 'tasks/WeeklyTrifles/broken_amulet' / metadata['imageName']))
        x, y, w, h = self.roi
        self.rule = SimpleNamespace(image=template, roi_back=self.roi,
            roi_front=[437, 600, 62, 68], threshold=metadata['threshold'],
            is_template_match=True, debug_mode=False,
            corp=lambda frame: frame[y:y+h, x:x+w])
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.frame[550:] = cv2.imread(str(ROOT / 'dev_tools/fixtures/summon_skin_02/buttons.png'))

    def test_real_skin_detects_shifted_ordinary_button(self):
        self.assertTrue(self.match(self.rule, self.frame))
        self.assertEqual(self.rule.roi_front, [476, 600, 62, 68])

    def test_original_button_position_still_matches(self):
        frame = np.zeros_like(self.frame)
        frame[600:668, 437:499] = self.rule.image
        self.assertTrue(self.match(self.rule, frame))
        self.assertEqual(self.rule.roi_front, [437, 600, 62, 68])

    def test_other_buttons_do_not_match_when_ordinary_is_missing(self):
        self.frame[580:690, 425:575] = 0
        self.assertFalse(self.match(self.rule, self.frame))

    def test_mystery_and_jade_art_cannot_substitute_for_ordinary(self):
        for x in (630, 800):
            with self.subTest(button_x=x):
                frame = np.zeros_like(self.frame)
                frame[580:690, 425:575] = self.frame[580:690, x:x+150]
                self.assertFalse(self.match(self.rule, frame))

    def test_generated_asset_matches_metadata(self):
        tree = ast.parse((ROOT / 'tasks/WeeklyTrifles/assets.py').read_text('utf-8'))
        assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'I_BM_ENTER' for t in n.targets))
        args = {k.arg: ast.literal_eval(k.value) for k in assignment.value.keywords}
        self.assertEqual(args['roi_back'], self.roi)
        self.assertEqual(args['threshold'], self.rule.threshold)


if __name__ == '__main__':
    unittest.main()
