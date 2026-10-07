"""Mainline courtyard compatibility tests without ADB or game actions."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError
from module.config.config_model import ConfigModel
from module.atom.image import RuleImage
from tasks.base_task import BaseTask
from tasks.GameUi.assets import GameUiAssets
from tasks.Component.Costume.assets import CostumeAssets
from tasks.Component.Costume.config import CostumeConfig, MainType


class Harness(GameUiAssets, BaseTask):
    def get_task_name(self):
        return 'CourtyardTest'


class MultiCourtyardTests(unittest.TestCase):
    def task(self, selected):
        model = ConfigModel()
        model.global_game.costume_config.costume_main_type = selected
        return Harness(SimpleNamespace(model=model, global_game=model.global_game),
                       SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))

    def frame(self, rule):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        template = rule.image
        x, y = rule.roi_back[:2]
        h, w = template.shape[:2]
        image[y:y+h, x:x+w] = template
        return image

    def test_legacy_scalar_and_json_list_validate_on_load_and_assignment(self):
        config = CostumeConfig(costume_main_type='costume_main_17')
        self.assertEqual(config.costume_main_type, [MainType.COSTUME_MAIN_17])
        config.costume_main_type = '["costume_main", "costume_main_13", "costume_main"]'
        self.assertEqual(config.costume_main_type, [MainType.COSTUME_MAIN, MainType.COSTUME_MAIN_13])
        for value in ([], None, '[broken', ['unknown'], {'costume_main': True}):
            with self.subTest(value=value), self.assertRaises((ValidationError, ValueError)):
                config.costume_main_type = value
        self.assertEqual(config.costume_main_type, [MainType.COSTUME_MAIN, MainType.COSTUME_MAIN_13])

    def test_args_schema_and_query_save_use_mainline_enum_numbers(self):
        model = ConfigModel()
        field = next(f for f in model.script_task('GlobalGame')['costume_config'] if f['name'] == 'costume_main_type')
        self.assertEqual(field['type'], 'multi_enum')
        self.assertEqual(field['minItems'], 1)
        self.assertEqual(len(field['enumEnum']), 18)
        with patch.object(ConfigModel, 'save') as save:
            self.assertTrue(model.script_set_arg('GlobalGame', 'costume_config', 'costume_main_type', '["costume_main_16","costume_main_17"]'))
            save.assert_called_once()
            self.assertFalse(model.script_set_arg('GlobalGame', 'costume_config', 'costume_main_type', '[]'))
        self.assertEqual(model.global_game.costume_config.costume_main_type, [MainType.COSTUME_MAIN_16, MainType.COSTUME_MAIN_17])

    def test_real_templates_detect_static_and_animated_candidates(self):
        task = self.task(['costume_main', 'costume_main_1', 'costume_main_13', 'costume_main_17'])
        reference = task.I_CHECK_MAIN
        original_hash = hash(reference)
        for skin, rule in ((MainType.COSTUME_MAIN_1, CostumeAssets.I_CHECK_MAIN_1),
                           (MainType.COSTUME_MAIN_13, CostumeAssets.I_CHECK_MAIN_13),
                           (MainType.COSTUME_MAIN_17, CostumeAssets.I_CHECK_MAIN_17_B)):
            with self.subTest(skin=skin):
                task.device.image = self.frame(rule)
                self.assertTrue(task.appear(task.I_CHECK_MAIN, threshold=.98))
                self.assertEqual(task.current_main_type, skin)
                self.assertIs(task.I_CHECK_MAIN, reference)
                self.assertEqual(hash(reference), original_hash)
        task._activate_main_costume(MainType.COSTUME_MAIN)
        self.assertNotIn('targets', reference.__dict__)
        self.assertNotIn('match', reference.__dict__)
        self.assertIs(reference.match.__func__, RuleImage.match)
        self.assertIsNone(reference._image)
        task.device.image = self.frame(reference)
        self.assertTrue(task.appear(reference, threshold=.98))

    def test_detection_does_not_select_an_unconfigured_skin(self):
        task = self.task(['costume_main', 'costume_main_13'])
        task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_17_A)
        self.assertFalse(task.appear(task.I_CHECK_MAIN, threshold=.99))
        self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN)

    def test_animation_to_static_restores_methods_and_new_task_default(self):
        task = self.task(['costume_main_17', 'costume_main_1'])
        task.I_CHECK_MAIN._image = object()
        task._activate_main_costume(MainType.COSTUME_MAIN_1)
        self.assertNotIn('targets', task.I_CHECK_MAIN.__dict__)
        self.assertIs(task.I_CHECK_MAIN.match.__func__, RuleImage.match)
        self.assertIsNone(task.I_CHECK_MAIN._image)
        default = self.task('costume_main')
        self.assertTrue(default.I_CHECK_MAIN.file.endswith('page_check_main.png'))

    def test_interval_guard_still_limits_detection(self):
        task = self.task(['costume_main', 'costume_main_17'])
        task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_17_C)
        self.assertTrue(task.appear(task.I_CHECK_MAIN, interval=10))
        with patch.object(task, 'detect_random_main_costume') as detect:
            self.assertFalse(task.appear(task.I_CHECK_MAIN, interval=10))
            detect.assert_not_called()

    def test_similar_fox_courtyards_choose_best_match_in_either_order(self):
        for selected in (['costume_main', 'costume_main_17', 'costume_main_16'],
                         ['costume_main', 'costume_main_16', 'costume_main_17']):
            with self.subTest(selected=selected):
                task = self.task(selected)
                task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_16)
                self.assertTrue(CostumeAssets.I_CHECK_MAIN_17_A.match(task.device.image))
                self.assertTrue(task.appear(task.I_CHECK_MAIN))
                self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN_16)

    def test_current_similar_skin_can_be_corrected_while_still_matching(self):
        task = self.task(['costume_main_17', 'costume_main_16'])
        reference = task.I_CHECK_MAIN
        task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_16)
        self.assertTrue(reference.match(task.device.image))
        self.assertTrue(task.appear(reference))
        self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN_16)
        self.assertIs(task.I_CHECK_MAIN, reference)
        self.assertEqual(task.I_MAIN_GOTO_EXPLORATION.file,
                         CostumeAssets.I_MAIN_GOTO_EXPLORATION_16.file)

    def test_all_candidates_still_select_best_fox_courtyard(self):
        selected = [skin.value for skin in MainType]
        selected.remove('costume_main_17')
        selected.insert(1, 'costume_main_17')
        task = self.task(selected)
        task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_16)
        self.assertTrue(task.appear(task.I_CHECK_MAIN))
        self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN_16)

    def test_reuses_same_frame_scores_but_checks_new_frames_and_thresholds(self):
        task = self.task(['costume_main_17', 'costume_main_16'])
        task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_16)
        with patch.object(task, '_main_candidate_score', wraps=task._main_candidate_score) as score:
            self.assertTrue(task.appear(task.I_CHECK_MAIN))
            self.assertEqual(score.call_count, 2)
            self.assertTrue(task.appear(task.I_CHECK_MAIN))
            self.assertEqual(score.call_count, 2)
            self.assertFalse(task.appear(task.I_CHECK_MAIN, threshold=1.0))
            self.assertEqual(score.call_count, 4)
            task.device.image = self.frame(CostumeAssets.I_CHECK_MAIN_17_B)
            self.assertTrue(task.appear(task.I_CHECK_MAIN, threshold=.98))
            self.assertEqual(score.call_count, 6)
            self.assertEqual(task.current_main_type, MainType.COSTUME_MAIN_17)


if __name__ == '__main__':
    unittest.main()
