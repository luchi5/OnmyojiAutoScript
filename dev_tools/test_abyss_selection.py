"""Real Easy screenshots plus the actual selection/switch methods; no emulator IO."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.AbyssShadows.assets import AbyssShadowsAssets
from tasks.AbyssShadows.selection_assets import AbyssSelectionAssets
from module.atom.image import RuleImage
from dev_tools.test_abyss_closeout_events import AREAS


class Timer:
    def __init__(self, *args):
        self.checks = 0

    def start(self):
        return self

    def reached(self):
        self.checks += 1
        return self.checks > 8


def load_task():
    tree = ast.parse((ROOT / 'tasks/AbyssShadows/script_task.py').read_text(encoding='utf-8'))
    node = copy.deepcopy(next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask'))
    node.bases = [ast.Name(id='AbyssShadowsAssets', ctx=ast.Load()),
                  ast.Name(id='AbyssSelectionAssets', ctx=ast.Load())]
    keep = {'is_area_selection', 'is_area_sealed', 'check_current_area', 'change_area', 'select_boss'}
    node.body = [n for n in node.body if (isinstance(n, ast.FunctionDef) and n.name in keep)
                 or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                     and n.targets[0].id in ('I_ABYSS_ATTACK_XY', 'I_ABYSS_FINISHED_XY'))]
    env = dict(AreaType=AREAS, Timer=Timer, logger=Mock(), GamePageUnknownError=RuntimeError,
               AbyssShadowsAssets=AbyssShadowsAssets, AbyssSelectionAssets=AbyssSelectionAssets,
               RuleImage=RuleImage)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<task>', 'exec'), env)
    return env['ScriptTask']


TASK = load_task()
FIXTURES = ROOT / 'tests/fixtures/abyss_easy'


def shot(name):
    return cv2.cvtColor(cv2.imread(str(FIXTURES / (name + '.png'))), cv2.COLOR_BGR2RGB)


def runtime(name):
    task = TASK()
    task.image = shot(name)
    def screenshot():
        task.frames += 1
        if task.frames > 40:
            raise AssertionError('selection loop did not finish')
    task.frames = 0
    task.screenshot = Mock(side_effect=screenshot)
    task.appear = lambda marker, **kw: marker.match(task.image, kw.get('threshold'))
    task.appear_then_click = Mock(return_value=False)
    task.click = Mock(return_value=True)
    return task


class AbyssSelectionTests(unittest.TestCase):
    def test_real_attack_header_rejected_by_old_template_is_accepted_by_xy(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[40:110, 550:780] = shot('attack_header')
        task = TASK()
        self.assertFalse(task.I_IS_ATTACK.match(frame))
        self.assertTrue(task.I_ABYSS_ATTACK_XY.match(frame))
        self.assertFalse(task.I_ABYSS_FINISHED_XY.match(frame))

    def test_easy_main_selection_recognized_with_first_two_cards_sealed(self):
        task = runtime('selection')
        self.assertTrue(task.is_area_selection())
        self.assertTrue(task.is_area_sealed(AREAS.DRAGON))
        self.assertTrue(task.is_area_sealed(AREAS.PEACOCK))
        self.assertFalse(task.is_area_sealed(AREAS.FOX))
        self.assertFalse(task.is_area_sealed(AREAS.LEOPARD))

    def test_easy_modal_recognized_despite_visible_old_area_title(self):
        task = runtime('modal')
        self.assertTrue(task.appear(task.I_FOX_AREA))
        self.assertTrue(task.is_area_selection())
        self.assertIsNone(task.check_current_area())
        self.assertTrue(task.is_area_sealed(AREAS.DRAGON))
        for area in (AREAS.PEACOCK, AREAS.FOX, AREAS.LEOPARD):
            self.assertFalse(task.is_area_sealed(area))

    def test_sealed_dragon_skipped_without_clicking_or_waiting(self):
        for name in ('selection', 'modal'):
            with self.subTest(name=name):
                task = runtime(name)
                self.assertFalse(task.select_boss(AREAS.DRAGON))
                task.click.assert_not_called()
                self.assertEqual(task.frames, 1)

    def test_modal_switch_selects_target_instead_of_clicking_behind_modal(self):
        task = runtime('modal')
        task.select_boss = Mock(return_value=True)
        self.assertTrue(task.change_area(AREAS.LEOPARD))
        task.select_boss.assert_called_once_with(AREAS.LEOPARD)
        task.appear_then_click.assert_not_called()

    def test_available_card_is_clicked_and_rechecks_transition_after_third_click(self):
        task = runtime('selection')
        real_appear = task.appear
        entered = False
        def click(marker, **kw):
            nonlocal entered
            entered = task.click.call_count == 3
            return True
        task.click.side_effect = click
        task.is_area_selection = lambda: not entered
        task.appear = lambda marker, **kw: (
            entered if marker == task.I_ABYSS_NAVIGATION else real_appear(marker, **kw))
        self.assertTrue(task.select_boss(AREAS.FOX))
        self.assertEqual(task.click.call_count, 3)
        self.assertEqual(task.frames, 4)

    def test_battle_and_blank_are_not_selection_or_sealed(self):
        import numpy as np
        for img in (shot('battle'), np.zeros((720, 1280, 3), dtype=np.uint8)):
            task = runtime('selection')
            task.image = img
            self.assertFalse(task.is_area_selection())
            for area in (AREAS.DRAGON, AREAS.PEACOCK, AREAS.FOX, AREAS.LEOPARD):
                self.assertFalse(task.is_area_sealed(area))

    def test_unknown_page_raises_bounded_error(self):
        task = runtime('battle')
        task.appear = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, 'Cannot identify'):
            task.check_current_area()
        self.assertLess(task.frames, 12)


if __name__ == '__main__':
    unittest.main()
