import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import cv2
from module.atom.image import RuleImage

TREE = ast.parse(Path('tasks/AbyssShadows/script_task.py').read_text(encoding='utf-8-sig'))
CLS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')


def harness():
    now = [0]
    def tick():
        now[0] += 1
        return now[0]
    ns = {'RuleImage': RuleImage, 'logger': Mock(), 'time': SimpleNamespace(monotonic=tick)}
    nodes = [n for n in CLS.body if
             isinstance(n, ast.Assign) and any(getattr(t, 'id', '').startswith('I_REWARD_EXHAUSTED') for t in n.targets)
             or isinstance(n, ast.FunctionDef) and n.name in ('wait_target_prepare', 'recover_target_map')]
    c = ast.ClassDef(name='Harness', bases=[], keywords=[], body=deepcopy(nodes), decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[c], type_ignores=[])), '<test>', 'exec'), ns)
    h = ns['Harness']()
    for key in ('I_ABYSS_FINISHED_XY', 'I_PREPARE_HIGHLIGHT', 'I_ENSURE_BUTTON', 'I_ABYSS_FIRE',
                'I_UI_CANCEL', 'I_UI_CANCEL_SAMLL', 'I_TARGET_DETAIL_CLOSE', 'I_ABYSS_MAP', 'I_ABYSS_NAVIGATION'):
        setattr(h, key, key)
    h.screenshot = Mock()
    return h


class RewardDialogTests(unittest.TestCase):
    def test_real_error_screenshot_matches_text_and_cancel(self):
        h = harness()
        im = cv2.cvtColor(cv2.imread('dev_tools/fixtures/abyss_reward_exhausted.png'), cv2.COLOR_BGR2RGB)
        self.assertTrue(h.I_REWARD_EXHAUSTED.match(im))
        self.assertTrue(h.I_REWARD_EXHAUSTED_CANCEL.match(im))
        # Buttons alone do not establish reward exhaustion.
        im[245:310, 420:860] = 200
        self.assertFalse(h.I_REWARD_EXHAUSTED.match(im))

    def test_existing_dialog_never_clicks_challenge(self):
        h = harness()
        h.appear = lambda rule: rule is h.I_REWARD_EXHAUSTED
        h.appear_then_click = Mock()
        self.assertEqual(h.wait_target_prepare(), 'reward_exhausted')
        h.appear_then_click.assert_not_called()

    def test_unknown_overlay_bounds_challenge_clicks_and_recovers(self):
        h = harness()
        h.appear = Mock(return_value=False)
        h.appear_then_click = Mock(return_value=True)
        h.recover_target_map = Mock()
        self.assertIsNone(h.wait_target_prepare())
        self.assertEqual(h.appear_then_click.call_count, 3)
        h.recover_target_map.assert_called_once()

    def test_cancel_dialog_before_navigating(self):
        h = harness()
        active = [True]
        h.appear = lambda r: (active[0] if r is h.I_REWARD_EXHAUSTED else r == h.I_ABYSS_MAP and not active[0])
        def click(r, **kw):
            if r is h.I_REWARD_EXHAUSTED_CANCEL:
                active[0] = False
                return True
            return False
        h.appear_then_click = Mock(side_effect=click)
        h.defer_abyss = Mock()
        h.recover_target_map()
        self.assertIs(h.appear_then_click.call_args_list[0].args[0], h.I_REWARD_EXHAUSTED_CANCEL)
        h.defer_abyss.assert_not_called()

    def test_unresponsive_cancel_is_bounded(self):
        h = harness()
        h.appear = lambda r: r is h.I_REWARD_EXHAUSTED
        h.appear_then_click = Mock(return_value=False)
        h.defer_abyss = Mock()
        h.recover_target_map()
        h.defer_abyss.assert_called_once()
        self.assertLessEqual(h.appear_then_click.call_count, 15)

    def test_clickable_cancel_without_transition_stops_after_three(self):
        h = harness()
        h.appear = lambda r: r is h.I_REWARD_EXHAUSTED
        h.appear_then_click = Mock(return_value=True)
        h.defer_abyss = Mock()
        h.recover_target_map()
        self.assertEqual(h.appear_then_click.call_count, 3)
        h.defer_abyss.assert_called_once()


if __name__ == '__main__':
    unittest.main()
