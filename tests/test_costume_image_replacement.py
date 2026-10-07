"""Costume cache regression tests with no task/device initialization."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from module.atom.image import RuleImage
from module.exception import RequestHumanTakeover
from tasks.Component.Costume.config import BattleType
from tasks.Component.Costume.costume_base import CostumeBase
from tasks.Component.Costume.image_replacement import replace_image_asset
from tasks.GameUi.matcher import AtomMatcher


ROOT = Path(__file__).resolve().parents[1]


def fresh_rule(relative, name):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == name:
            return RuleImage(**{item.arg: ast.literal_eval(item.value)
                                for item in node.value.keywords})
    raise AssertionError(f'Rule {name} not found')


def default_false():
    return fresh_rule('tasks/Component/GeneralBattle/assets.py', 'I_FALSE')


def theme_false():
    return fresh_rule('tasks/Component/CostumeBattle/assets.py', 'I_FALSE_13')


def frame(name):
    path = ROOT / 'tests/fixtures/battle_result_window4' / f'{name}.png'
    crop = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[30:320, 390:900] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return image


def reload_task_override():
    tree = ast.parse((ROOT / 'tasks/RealmRaid/script_task.py').read_text(encoding='utf-8-sig'))
    task = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ScriptTask')
    method = next(node for node in task.body if isinstance(node, ast.FunctionDef) and node.name == 'replace_img')
    namespace = {'RuleImage': RuleImage, 'replace_image_asset': replace_image_asset}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / 'tasks/RealmRaid/script_task.py'), 'exec'), namespace)
    return namespace['replace_img']


class CostumeImageReplacementTests(unittest.TestCase):
    def test_loaded_default_pixels_are_replaced_without_breaking_page_reference(self):
        rule = default_false()
        self.assertEqual(rule.image.shape[:2], (100, 100))
        holder = SimpleNamespace(I_FALSE=rule, image=frame('failure'))
        holder.appear = lambda target: target.match(holder.image)
        page = AtomMatcher(rule)
        self.assertFalse(page.evaluate(holder))
        replace_image_asset(holder, 'I_FALSE', theme_false())
        self.assertIs(holder.I_FALSE, page.target)
        self.assertIsNone(rule._image)
        self.assertTrue(page.evaluate(holder))
        self.assertEqual(rule.image.shape[:2], (77, 58))

    def test_actual_invitation_overlay_is_not_a_failure_result(self):
        rule = default_false()
        rule.load_image()
        holder = SimpleNamespace(I_FALSE=rule)
        replace_image_asset(holder, 'I_FALSE', theme_false())
        self.assertFalse(rule.match(frame('invitation')))

    def test_changed_file_invalidates_name_features_and_match_flags(self):
        rule = default_false()
        _ = rule.image, rule.name, rule.is_template_match, rule.is_sift_flann
        rule._kp, rule._des = object(), object()
        rule.__dict__['kp'], rule.__dict__['des'] = object(), object()
        rule._match_init = True
        replace_image_asset(SimpleNamespace(I_FALSE=rule), 'I_FALSE', theme_false())
        self.assertIsNone(rule._image)
        self.assertIsNone(rule._kp)
        self.assertIsNone(rule._des)
        for name in ('name', 'kp', 'des', 'is_template_match', 'is_sift_flann'):
            self.assertNotIn(name, rule.__dict__)
        self.assertFalse(rule._match_init)
        self.assertEqual(rule.name, 'BATTLE13_FALSE_13')

    def test_same_file_keeps_loaded_pixels_and_feature_caches(self):
        rule = theme_false()
        pixels = rule.image
        name = rule.name
        rule._kp, rule._des = object(), object()
        rule.__dict__['kp'], rule.__dict__['des'] = rule._kp, rule._des
        features = rule._kp, rule._des
        rule._match_init = True
        replace_image_asset(SimpleNamespace(I_FALSE=rule), 'I_FALSE', theme_false())
        self.assertIs(rule.image, pixels)
        self.assertEqual(rule.name, name)
        self.assertIs(rule._kp, features[0])
        self.assertIs(rule._des, features[1])
        self.assertIs(rule.__dict__['kp'], features[0])
        self.assertIs(rule.__dict__['des'], features[1])
        self.assertTrue(rule._match_init)

    def test_explicit_same_file_refresh_repairs_an_existing_stale_cache(self):
        rule, source = default_false(), theme_false()
        rule.load_image()
        # This is the state left by the old running worker: new filename,
        # old pixels. File equality alone cannot identify it as healthy.
        rule.file = source.file
        rule.roi_front = list(source.roi_front)
        rule.roi_back = source.roi_back
        holder = SimpleNamespace(I_FALSE=rule)
        self.assertFalse(rule.match(frame('failure')))
        replace_image_asset(holder, 'I_FALSE', source, force_reload=True)
        self.assertIs(holder.I_FALSE, rule)
        self.assertTrue(rule.match(frame('failure')))

    def test_matching_coordinates_do_not_mutate_source_asset(self):
        rule, source = default_false(), theme_false()
        source_front = list(source.roi_front)
        holder = SimpleNamespace(I_FALSE=rule)
        replace_image_asset(holder, 'I_FALSE', source)
        self.assertIsNot(rule.roi_front, source.roi_front)
        self.assertTrue(rule.match(frame('failure')))
        self.assertEqual(source.roi_front, source_front)
        rule.roi_front[0] += 1
        self.assertEqual(source.roi_front, source_front)

    def test_source_roi_back_list_is_not_shared(self):
        source = theme_false()
        source.roi_back = list(source.roi_back)
        rule = default_false()
        replace_image_asset(SimpleNamespace(I_FALSE=rule), 'I_FALSE', source)
        original_back = rule.roi_back
        source.roi_back[0] += 1
        self.assertEqual(rule.roi_back, original_back)

    def test_no_roi_back_replacement_preserves_navigation_search_area(self):
        rule, source = default_false(), theme_false()
        original_back = rule.roi_back
        replace_image_asset(SimpleNamespace(I_FALSE=rule), 'I_FALSE', source, rp_roi_back=False)
        self.assertEqual(rule.roi_back, original_back)
        self.assertEqual(rule.roi_front, source.roi_front)
        self.assertEqual(rule.file, source.file)

    def test_method_change_refreshes_flags_while_retaining_same_file_pixels(self):
        rule = default_false()
        pixels = rule.image
        self.assertTrue(rule.is_template_match)
        self.assertFalse(rule.is_sift_flann)
        source = default_false()
        source.method = 'Sift Flann'
        replace_image_asset(SimpleNamespace(I_FALSE=rule), 'I_FALSE', source)
        self.assertIs(rule.image, pixels)
        self.assertFalse(rule.is_template_match)
        self.assertTrue(rule.is_sift_flann)

    def test_missing_asset_is_an_unchanged_noop(self):
        holder = SimpleNamespace(other=object())
        before = dict(holder.__dict__)
        self.assertIsNone(replace_image_asset(holder, 'I_FALSE', theme_false()))
        self.assertEqual(holder.__dict__, before)

    def test_replacement_does_not_swallow_takeover_exception(self):
        class Interrupted:
            def __getattribute__(self, name):
                raise RequestHumanTakeover('owner returned')
        with self.assertRaises(RequestHumanTakeover):
            replace_image_asset(Interrupted(), 'I_FALSE', theme_false())

    def test_global_costume_interface_uses_cache_safe_replacement(self):
        class Holder(CostumeBase):
            pass
        holder = Holder()
        holder.I_FALSE = default_false()
        holder.I_FALSE.load_image()
        holder.check_costume_battle(BattleType.COSTUME_BATTLE_13)
        self.assertTrue(holder.I_FALSE.match(frame('failure')))

    def test_fresh_realm_raid_override_works_with_an_old_cached_costume_base(self):
        class Holder(CostumeBase):
            replace_img = reload_task_override()
        holder = Holder()
        holder.I_FALSE = default_false()
        holder.I_FALSE.load_image()
        source = theme_false()
        holder.I_FALSE.file = source.file
        holder.I_FALSE.roi_front = list(source.roi_front)
        holder.I_FALSE.roi_back = source.roi_back
        self.assertFalse(holder.I_FALSE.match(frame('failure')))
        # Calling the old cached CostumeBase method would reproduce the bug.
        with patch.object(CostumeBase, 'replace_img', side_effect=AssertionError('cached base method used')):
            holder.check_costume_battle(BattleType.COSTUME_BATTLE_13)
        self.assertTrue(holder.I_FALSE.match(frame('failure')))
        self.assertFalse(holder.I_FALSE.match(frame('invitation')))


if __name__ == '__main__':
    unittest.main()
