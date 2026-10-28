"""Replay public navigation crops and verify bounded, fresh-frame retries."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import cv2
import numpy as np

from tasks.DemonEncounter.script_task import ScriptTask


class BossSearchTests(unittest.TestCase):
    def test_real_navigation_crop_and_negative_frames(self):
        for name in ('boss_navigation.png', 'boss_navigation_white.png'):
            self.check_navigation(name)

    def check_navigation(self, name):
        crop = cv2.imread(str(Path(__file__).parent / 'fixtures/demon_encounter' / name))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[620:720, 860:1060] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        self.assertFalse(ScriptTask.I_DE_BOSS_BEST.match(image))
        self.assertTrue(ScriptTask.I_DE_BOSS_BEST_CORE.match(image))
        self.assertTrue(ScriptTask.I_DE_BOSS.match(image))
        # Normal boss only is not an extreme entrance.
        image[620:720, 860:980] = 0
        self.assertFalse(ScriptTask.I_DE_BOSS_BEST_CORE.match(image))
        # Put the normal boss in the extreme slot to test actual confusion.
        image[640:695, 900:950] = image[640:695, 1000:1050].copy()
        self.assertFalse(ScriptTask.I_DE_BOSS_BEST_CORE.match(image))
        image[:] = 0
        self.assertFalse(ScriptTask.I_DE_BOSS_BEST_CORE.match(image))

    def task(self, extreme=True):
        return SimpleNamespace(
            screenshot=Mock(), appear_then_click=Mock(return_value=False),
            appear=Mock(return_value=False), best_demon_enable=extreme,
            I_DE_BOSS='normal', I_DE_BOSS_BEST_CORE='alternate',
        )

    @patch('tasks.DemonEncounter.script_task.time.sleep')
    def test_delayed_original_button_retries_fresh_frames(self, sleep):
        task = self.task()
        task.appear_then_click.side_effect = [False, False, True]
        self.assertTrue(ScriptTask._click_boss_search(task, 'original'))
        self.assertEqual(task.screenshot.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch('tasks.DemonEncounter.script_task.time.sleep')
    def test_alternate_requires_map_and_extreme_selection(self, sleep):
        for extreme, on_map, expected in ((True, True, True), (False, True, False), (True, False, False)):
            task = self.task(extreme)
            task.appear.return_value = on_map
            task.appear_then_click.side_effect = lambda rule, **kw: rule == 'alternate'
            self.assertEqual(ScriptTask._click_boss_search(task, 'original'), expected)
            self.assertEqual(task.screenshot.call_count, 1 if expected else 3)
            if not expected:
                self.assertTrue(all(call.args[0] == 'original' for call in task.appear_then_click.call_args_list))


if __name__ == '__main__':
    unittest.main()
