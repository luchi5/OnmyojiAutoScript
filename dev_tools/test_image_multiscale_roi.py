"""Offline multi-scale click-region regressions with real synthetic pixels.

Only the production matching method is AST-loaded; no project module, logger,
device, account configuration or service is imported.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_match_multi_scale():
    path = ROOT / 'module/atom/image.py'
    source = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    rule = next(node for node in source.body
                if isinstance(node, ast.ClassDef) and node.name == 'RuleImage')
    method = next(node for node in rule.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'match_multi_scale')
    isolated = ast.Module(body=[method], type_ignores=[])
    namespace = {'np': np, 'cv2': cv2}
    exec(compile(isolated, str(path), 'exec'), namespace)
    return namespace['match_multi_scale']


MATCH_MULTI_SCALE = load_match_multi_scale()


class MultiScaleRoiTests(unittest.TestCase):
    def make_scene(self, scale, search_size=(160, 100)):
        # Random texture makes the inserted template's scale/location unique.
        rng = np.random.default_rng(742)
        template = rng.integers(0, 256, (50, 100, 3), dtype=np.uint8)
        width, height = search_size
        image = np.zeros((height + 20, width + 10, 3), dtype=np.uint8)
        search = image[20:20 + height, 10:10 + width]
        if scale is not None:
            scaled = cv2.resize(template, (int(100 * scale), int(50 * scale)))
            search[9:9 + scaled.shape[0], 7:7 + scaled.shape[1]] = scaled
        rule = SimpleNamespace(
            image=template, roi_back=(10, 20, width, height),
            roi_front=[1, 2, 100, 50], threshold=0.95, debug_mode=False,
            corp=lambda frame: frame[20:20 + height, 10:10 + width],
        )
        return rule, image

    def test_best_scale_before_last_sets_click_region_to_matching_size(self):
        rule, image = self.make_scene(0.8)
        self.assertTrue(MATCH_MULTI_SCALE(rule, image, scales=[0.8, 1.2]))
        self.assertEqual(rule.roi_front, [17, 29, 80, 40])

    def test_oversized_last_scale_does_not_replace_matching_click_region(self):
        rule, image = self.make_scene(0.8, search_size=(90, 60))
        self.assertTrue(MATCH_MULTI_SCALE(rule, image, scales=[0.8, 1.2]))
        self.assertEqual(rule.roi_front, [17, 29, 80, 40])

    def test_best_scale_at_end_still_sets_correct_click_region(self):
        rule, image = self.make_scene(1.2)
        self.assertTrue(MATCH_MULTI_SCALE(rule, image, scales=[0.8, 1.2]))
        self.assertEqual(rule.roi_front, [17, 29, 120, 60])

    def test_no_match_preserves_previous_click_region(self):
        rule, image = self.make_scene(None)
        original_roi = rule.roi_front.copy()
        self.assertFalse(MATCH_MULTI_SCALE(rule, image, scales=[0.8, 1.2]))
        self.assertEqual(rule.roi_front, original_roi)


if __name__ == '__main__':
    unittest.main()
