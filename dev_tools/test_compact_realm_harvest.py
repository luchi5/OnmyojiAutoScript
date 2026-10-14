"""Replay the shifted expired taiko reward from account 11's realm skin."""
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch
import unittest
import cv2
import numpy as np
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer

ROOT = Path(__file__).resolve().parents[1]
stub = ModuleType('module.logger')
stub.logger = Mock()
with patch.dict('sys.modules', {'module.logger': stub}):
    from module.atom.image import RuleImage
    from tasks.KekkaiActivation.assets import KekkaiActivationAssets


class CompactHarvestTests(unittest.TestCase):
    def setUp(self):
        crop = cv2.imdecode(np.frombuffer(
            (ROOT/'dev_tools/fixtures/compact_realm_reward.png').read_bytes(), np.uint8), 1)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.image[120:405, 780:1020] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    def test_real_reward_is_outside_old_region_and_collected_by_new_loop(self):
        old = KekkaiActivationAssets.I_A_HARVEST_KAIKO_6
        original_region = old.roi_back
        self.assertFalse(old.match_multi_scale(self.image, threshold=.7))
        clock = FakeClock(1)
        task = KekkaiActivationAssets()
        clicks = []
        task.screenshot = clock.advance
        task.ui_reward_appear_click = lambda: False
        def match_click(rule, **kwargs):
            if rule.match_multi_scale(self.image, threshold=kwargs['threshold']):
                clicks.append(tuple(rule.roi_front))
                self.image[:] = 0  # reward disappears after collection
                return True
            return False
        task.appear_then_click_multi_scale = match_click
        method = extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py',
            'ScriptTask', ('harvest_card',), {
                'RuleImage': RuleImage, 'Timer': lambda t: FakeTimer(clock, t),
                'time': SimpleNamespace(sleep=lambda _: None), 'logger': Mock(),
            })['harvest_card']
        method(task)
        self.assertEqual(len(clicks), 1)
        x, y, w, h = clicks[0]
        self.assertTrue(800 <= x < 860 and 310 <= y < 380, clicks)
        self.assertEqual(old.roi_back, original_region)

    def test_normal_skin_reward_still_matches(self):
        old = KekkaiActivationAssets.I_A_HARVEST_KAIKO_6
        image = np.zeros_like(self.image)
        h, w = old.image.shape[:2]
        image[160:160+h, 898:898+w] = old.image
        rule = RuleImage(tuple(old.roi_front), (780, 120, 240, 285),
                         old.method, old.threshold, old.file)
        self.assertTrue(rule.match_multi_scale(image, threshold=.7))


if __name__ == '__main__':
    unittest.main()
