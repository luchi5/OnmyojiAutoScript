"""Regression frames: recognize the moving exploration lantern, not red ropes."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from module.atom.image import RuleImage


ROOT = Path(__file__).resolve().parents[1]


def rule():
    # Read the actual asset declaration so this exercises the production rule.
    import ast
    tree = ast.parse((ROOT / "tasks/Component/Costume/assets.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "I_MAIN_GOTO_EXPLORATION_9"
            for target in node.targets
        ):
            return eval(compile(ast.Expression(node.value), "<asset>", "eval"), {"RuleImage": RuleImage})
    raise AssertionError("Missing exploration asset")


class Main9ExplorationTests(unittest.TestCase):
    def test_two_real_frames_follow_lantern_position(self):
        for name, expected_x in (("1792493135601", 673), ("1792493375749", 612)):
            with self.subTest(frame=name):
                frame = cv2.imdecode(np.fromfile(str(ROOT / f"dev_tools/fixtures/main9/{name}.png"), np.uint8), cv2.IMREAD_COLOR)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                asset = rule()
                self.assertTrue(asset.match(frame))
                x, y, w, h = asset.roi_front
                self.assertLess(abs(x + w / 2 - expected_x), 5)
                self.assertTrue(110 < y + h / 2 < 170)
                # Hide only the real entrance: decorations must not become a target.
                frame[98:180, expected_x - 45:expected_x + 45] = 0
                self.assertFalse(rule().match(frame))


if __name__ == "__main__":
    unittest.main()
