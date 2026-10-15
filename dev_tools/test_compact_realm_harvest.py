"""Replay real chest/reward crops and check collection decisions without a device."""
import ast
from functools import cached_property
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import cv2
import numpy as np
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer

ROOT = Path(__file__).resolve().parents[1]

# Use the real matcher and image declarations without importing OCR, its
# models, or the production logger. CI only needs OpenCV and NumPy here.
matching = extract_methods(ROOT/'module/atom/image.py', 'RuleImage', (
    '__init__', '__str__', '__eq__', '__hash__', '__bool__', 'name',
    'load_image', 'image', 'is_template_match', 'corp', 'match', 'match_multi_scale',
), {'cv2': cv2, 'np': np, 'Path': Path, 'cached_property': cached_property,
    'fromfile': np.fromfile, 'uint8': np.uint8, 'logger': Mock()})
RuleImage = type('RuleImage', (), dict(matching, debug_mode=False))
assets_tree = ast.parse((ROOT/'tasks/KekkaiActivation/assets.py').read_text(encoding='utf-8'))
assets_class = next(node for node in assets_tree.body if isinstance(node, ast.ClassDef))
assets_class.body = [node for node in assets_class.body if isinstance(node, ast.Assign)
    and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
    and node.value.func.id == 'RuleImage']
asset_namespace = {'RuleImage': RuleImage}
exec(compile(ast.fix_missing_locations(ast.Module(body=[assets_class], type_ignores=[])),
             'image_assets', 'exec'), asset_namespace)
KekkaiActivationAssets = asset_namespace['KekkaiActivationAssets']


class CompactHarvestTests(unittest.TestCase):
    def setUp(self):
        crop = cv2.imdecode(np.frombuffer(
            (ROOT/'dev_tools/fixtures/compact_realm_reward.png').read_bytes(), np.uint8), 1)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.image[120:405, 780:1020] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    def task(self):
        clock = FakeClock(1)
        task = KekkaiActivationAssets()
        task.screenshot = clock.advance
        task.appear = lambda _: False
        task.appear_multi_scale = lambda rule: rule.match_multi_scale(self.image)
        task.ui_reward_appear_click = lambda: False
        task.clicks = []
        def click(rule, **kwargs):
            task.clicks.append(tuple(rule.roi_front))
            self.image[:] = 0  # collected icon disappears
            return True
        task.click = click
        methods = extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py',
            'ScriptTask', ('realm_card_markers', 'realm_card_marker', 'harvest_card'), {
                'cached_property': lambda fn: fn, 'RuleImage': RuleImage,
                'Timer': lambda t: FakeTimer(clock, t),
                'time': SimpleNamespace(sleep=lambda _: None), 'logger': Mock(),
            })
        task.realm_card_markers = methods['realm_card_markers'](task)
        for name in ('realm_card_marker', 'harvest_card'):
            setattr(task, name, methods[name].__get__(task))
        return task

    def test_real_reward_is_outside_old_region_and_collected_by_new_loop(self):
        old = KekkaiActivationAssets.I_A_HARVEST_KAIKO_6
        original_region = old.roi_back
        self.assertFalse(old.match_multi_scale(self.image, threshold=.7))
        task = self.task()
        self.assertEqual(task.realm_card_marker()[0], 'expired')
        task.harvest_card()
        self.assertEqual(len(task.clicks), 1)
        x, y, w, h = task.clicks[0]
        self.assertTrue(800 <= x < 860 and 310 <= y < 380, task.clicks)
        self.assertEqual(old.roi_back, original_region)

    def test_normal_skin_reward_still_matches(self):
        old = KekkaiActivationAssets.I_A_HARVEST_KAIKO_6
        image = np.zeros_like(self.image)
        h, w = old.image.shape[:2]
        image[160:160+h, 898:898+w] = old.image
        rule = RuleImage(tuple(old.roi_front), (780, 120, 240, 285),
                         old.method, old.threshold, old.file)
        self.assertTrue(rule.match_multi_scale(image, threshold=.7))

    def test_user_chest_crop_is_active_and_never_clicked(self):
        # User's cropped screenshot, normalized to game scale and positioned
        # above the compact plaque. This is not a full emulator capture.
        crop = cv2.imread(str(ROOT/'dev_tools/fixtures/realm_active_chest.png'))
        self.image[:] = 0
        self.image[298:643,740:925] = cv2.cvtColor(crop,cv2.COLOR_BGR2RGB)
        task = self.task()
        self.assertEqual(task.realm_card_marker(), ('active', None))
        self.assertFalse(task.harvest_card())
        self.assertEqual(task.clicks, [])

    def test_chest_wins_over_simultaneously_visible_reward(self):
        chest = KekkaiActivationAssets.I_A_HARVEST_EXP.image
        h,w = chest.shape[:2]
        self.image[153:153+h,891:891+w] = chest
        task = self.task()
        self.assertEqual(task.realm_card_marker(), ('active', None))
        self.assertFalse(task.harvest_card())
        self.assertEqual(task.clicks, [])

    def test_empty_and_growth_exp_outside_card_region_are_unknown(self):
        self.image[:] = 0
        task = self.task()
        self.assertEqual(task.realm_card_marker(), ('unknown',None))
        chest = KekkaiActivationAssets.I_A_HARVEST_EXP.image
        h,w = chest.shape[:2]
        self.image[150:150+h,600:600+w] = chest
        self.assertEqual(task.realm_card_marker(), ('unknown',None))
        task.harvest_card()
        self.assertEqual(task.clicks, [])

    def test_card_panel_background_cannot_trigger_collection(self):
        task = self.task()
        task.appear = lambda rule: rule == task.I_A_CHECK_CARD
        self.assertEqual(task.realm_card_marker(), ('unknown',None))

    def test_all_existing_card_icons_match_on_normal_and_compact_layouts(self):
        task = self.task()
        for card in task.realm_card_markers[1]:
            for x,y in ((898,160),(815,328)):
                with self.subTest(card=card.file, position=(x,y)):
                    self.image[:] = 0
                    h,w = card.image.shape[:2]
                    self.image[y:y+h,x:x+w] = card.image
                    self.assertEqual(task.realm_card_marker()[0], 'expired')

    def test_chest_is_excluded_from_collection_targets(self):
        task = self.task()
        chest,cards = task.realm_card_markers
        self.assertNotIn(chest.file, [card.file for card in cards])


if __name__ == '__main__':
    unittest.main()
