"""Replay the account 09 soul announcement and verify blocking screenshot flow."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tasks.GlobalGame import battle_reward_popup as popup
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer

ROOT = Path(__file__).resolve().parents[1]


class SoulBonusPopupTests(unittest.TestCase):
    def test_all_three_anchors_are_required(self):
        markers = (popup.TITLE, popup.SUBTITLE, popup.CLOSE)
        for mask in range(8):
            visible = [m for i, m in enumerate(markers) if mask & (1 << i)]
            task = SimpleNamespace(appear=lambda r: any(r is m for m in visible), click=Mock())
            self.assertEqual(popup.close_soul_bonus_popup(task), mask == 7)
            if mask == 7:
                task.click.assert_called_once_with(popup.CLOSE, interval=1.5)
            else:
                task.click.assert_not_called()

    def test_visible_overlay_blocks_even_when_click_is_throttled(self):
        task = SimpleNamespace(appear=lambda r: True, click=Mock(return_value=False))
        self.assertTrue(popup.close_soul_bonus_popup(task))

    def test_saved_incident_and_missing_anchors(self):
        files = list((ROOT / 'log/error/1792578074343').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], np.uint8), cv2.IMREAD_COLOR),
                             cv2.COLOR_BGR2RGB)
        task = SimpleNamespace(appear=lambda r: r.match(frame), click=Mock())
        self.assertTrue(popup.close_soul_bonus_popup(task))
        task.click.assert_called_once_with(popup.CLOSE, interval=1.5)
        for marker in (popup.TITLE, popup.SUBTITLE, popup.CLOSE):
            modified = frame.copy()
            x, y, w, h = marker.roi_back
            modified[y:y+h, x:x+w] = 0
            task.appear = lambda r: r.match(modified)
            task.click.reset_mock()
            self.assertFalse(popup.close_soul_bonus_popup(task))
            task.click.assert_not_called()

    def test_screenshot_waits_for_overlay_to_disappear_and_bounds_failure(self):
        for frames, should_raise in (([True, True, False], False), ([True]*30, True)):
            clock = FakeClock(1)
            method = extract_methods(ROOT / 'tasks/base_task.py', 'BaseTask', ('screenshot',),
                {'Timer': lambda t: FakeTimer(clock, t), 'sleep': lambda _: clock.advance(),
                 'GameStuckError': RuntimeError})['screenshot']
            task = SimpleNamespace(device=SimpleNamespace(screenshot=Mock(), image='ready'), _burst=Mock())
            with patch('tasks.GlobalGame.autumn_popup.close_autumn_popup', return_value=False), \
                    patch.object(popup, 'close_pet_treasure_popup', return_value=False), \
                    patch.object(popup, 'close_soul_bonus_popup', side_effect=frames):
                if should_raise:
                    with self.assertRaises(RuntimeError):
                        method(task)
                    self.assertLess(task.device.screenshot.call_count, 15)
                else:
                    self.assertEqual(method(task), 'ready')
                    self.assertEqual(task.device.screenshot.call_count, 3)

    def test_pet_requires_title_and_ornament(self):
        for markers in ((), (popup.PET_TITLE,), (popup.PET_ORNAMENT,),
                        (popup.PET_TITLE, popup.PET_ORNAMENT)):
            task = SimpleNamespace(appear=lambda r: any(r is m for m in markers), click=Mock())
            self.assertEqual(popup.close_pet_treasure_popup(task), len(markers) == 2)
            if len(markers) == 2:
                task.click.assert_called_once_with(popup.PET_CONTINUE, interval=1.5)
            else:
                task.click.assert_not_called()

    def test_saved_pet_incident_and_stacked_reward_flow(self):
        files = list((ROOT / 'log/error/1792578462735').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], np.uint8), cv2.IMREAD_COLOR),
                             cv2.COLOR_BGR2RGB)
        task = SimpleNamespace(appear=lambda r: r.match(frame), click=Mock())
        self.assertFalse(popup.close_soul_bonus_popup(task))
        self.assertTrue(popup.close_pet_treasure_popup(task))
        task.click.assert_called_once_with(popup.PET_CONTINUE, interval=1.5)
        clock = FakeClock(1)
        method = extract_methods(ROOT / 'tasks/base_task.py', 'BaseTask', ('screenshot',),
            {'Timer': lambda t: FakeTimer(clock, t), 'sleep': lambda _: clock.advance(),
             'GameStuckError': RuntimeError})['screenshot']
        task = SimpleNamespace(device=SimpleNamespace(screenshot=Mock(), image='ready'), _burst=Mock())
        with patch('tasks.GlobalGame.autumn_popup.close_autumn_popup', return_value=False), \
                patch.object(popup, 'close_soul_bonus_popup', side_effect=[True, False, False]), \
                patch.object(popup, 'close_pet_treasure_popup', side_effect=[True, False]):
            self.assertEqual(method(task), 'ready')
        self.assertEqual(task.device.screenshot.call_count, 3)


if __name__ == '__main__':
    unittest.main()
