"""Offline pixel and dropped-click regressions for preset selection."""
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import unittest
import numpy as np
from tasks.Component.GeneralBattle.preset_selection import selected_team
from dev_tools.test_demon_preset_transition import extract, Logger, Timer

ROOT = Path(__file__).resolve().parents[1]


def pixels(team=None):
    image = np.full((720, 1280, 3), 140, dtype=np.uint8)
    if team:
        y = (235, 355, 475, 595)[team - 1]
        for x in (188, 665):
            image[y:min(y + 100, 630), x:x + 6] = (180, 50, 240)
    return image


class SelectionTests(unittest.TestCase):
    def test_every_row_and_channel_order(self):
        for team in range(1, 5):
            self.assertEqual(selected_team(pixels(team)), team)
            self.assertEqual(selected_team(pixels(team)[:, :, ::-1]), team)

    def test_unknown_ambiguous_and_portraits_are_not_selection(self):
        self.assertIsNone(selected_team(None))
        self.assertIsNone(selected_team(pixels()))
        image = pixels(1)
        other = pixels(2)
        image[355:455] = other[355:455]
        self.assertIsNone(selected_team(image))
        image = pixels()
        image[370:440, 220:640] = (180, 50, 240)
        self.assertIsNone(selected_team(image))

    def run_selection(self, frames, interrupted=False):
        clock = SimpleNamespace(now=0)
        task = SimpleNamespace(device=SimpleNamespace(image=None), I_PRESET_ENSURE='ensure')
        task.clicks = []
        task.captures = 0
        def screenshot():
            task.device.image = frames[min(task.captures, len(frames)-1)]
            task.captures += 1
            clock.now += .5
        task.screenshot = screenshot
        task.appear = lambda marker: True
        task.click = lambda target, **kw: task.clicks.append(target.roi_front) or True
        method = extract(ROOT/'tasks/Component/GeneralBattle/general_battle.py', 'GeneralBattle',
                         ('_confirm_preset_team',), {'Timer': lambda seconds: Timer(clock, seconds),
                          'logger': Logger(), 'GameStuckError': RuntimeError})['_confirm_preset_team']
        fake = ModuleType('module.atom.click')
        fake.RuleClick = lambda **kw: SimpleNamespace(**kw)
        target = SimpleNamespace(roi_front=(195, 355, 465, 110))
        with patch.dict('sys.modules', {'module.atom.click': fake}):
            try:
                task.result = method(task, target, 2, lambda: interrupted)
            except RuntimeError as error:
                task.error = error
        return task

    def test_dropped_click_retries_until_two_target_frames(self):
        task = self.run_selection([pixels(1), pixels(1), pixels(2), pixels(2)])
        self.assertTrue(task.result)
        self.assertEqual(len(task.clicks), 2)
        self.assertEqual(task.captures, 4)
        self.assertTrue(all(y < 390 for x, y, w, h in task.clicks))

    def test_wrong_team_never_passes_and_attempts_are_bounded(self):
        task = self.run_selection([pixels(1)])
        self.assertIn('refusing deployment', str(task.error))
        self.assertEqual(len(task.clicks), 3)

    def test_transient_highlight_is_not_enough(self):
        task = self.run_selection([pixels(2), pixels(1), pixels(2), pixels(2)])
        self.assertTrue(task.result)
        self.assertEqual(task.captures, 4)

    def test_interrupted_battle_does_not_click(self):
        task = self.run_selection([pixels()], interrupted=True)
        self.assertFalse(task.result)
        self.assertEqual(task.clicks, [])


if __name__ == '__main__':
    unittest.main()
