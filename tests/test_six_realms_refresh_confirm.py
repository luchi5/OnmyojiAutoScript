from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import cv2
import numpy as np
from module.exception import GameStuckError
from tasks.SixRealms.moon_sea.skills import MoonSeaSkills


class RefreshTests(unittest.TestCase):
    def test_checkbox_states_are_distinct(self):
        for state in ('checked', 'unchecked'):
            image = np.zeros((720,1280,3), dtype=np.uint8)
            crop = cv2.imread(str(Path(__file__).parent / 'fixtures/six_realms_refresh' / (state+'.png')))
            image[224:490,404:866] = cv2.cvtColor(crop,cv2.COLOR_BGR2RGB)
            self.assertTrue(MoonSeaSkills.I_REFRESH_DIALOG.match(image))
            self.assertEqual(MoonSeaSkills.I_REFRESH_CHECKED.match(image),state=='checked')
            self.assertEqual(MoonSeaSkills.I_REFRESH_UNCHECKED.match(image),state=='unchecked')
        self.assertFalse(MoonSeaSkills.I_REFRESH_DIALOG.match(np.zeros_like(image)))

    def task(self, checked=False, effective=True, dialog=True):
        task=SimpleNamespace(I_REFRESH_DIALOG='dialog',I_REFRESH_CHECKED='checked',
                             I_REFRESH_UNCHECKED='unchecked',I_UI_CONFIRM='confirm')
        task.checked=checked
        task.appear=lambda rule: dialog if rule=='dialog' else task.checked if rule=='checked' else not task.checked
        task.click=Mock(side_effect=lambda rule:setattr(task,'checked',effective))
        task.screenshot=Mock()
        task.appear_then_click=Mock(return_value=True)
        return task

    @patch('tasks.SixRealms.moon_sea.skills.time.sleep')
    def test_unchecked_is_clicked_once_before_confirmation(self,_):
        task=self.task()
        self.assertTrue(MoonSeaSkills._handle_skill_refresh_confirm(task))
        task.click.assert_called_once_with('unchecked')
        task.appear_then_click.assert_called_once_with('confirm')
        self.assertTrue(task.checked)

    def test_checked_is_never_toggled_off(self):
        task=self.task(checked=True)
        self.assertTrue(MoonSeaSkills._handle_skill_refresh_confirm(task))
        task.click.assert_not_called()

    @patch('tasks.SixRealms.moon_sea.skills.time.sleep')
    def test_failed_check_is_bounded_and_never_confirms(self,_):
        task=self.task(effective=False)
        with self.assertRaises(GameStuckError):
            MoonSeaSkills._handle_skill_refresh_confirm(task)
        task.click.assert_called_once()
        self.assertEqual(task.screenshot.call_count,3)
        task.appear_then_click.assert_not_called()

    def test_no_dialog_does_not_click(self):
        task=self.task(dialog=False)
        self.assertFalse(MoonSeaSkills._handle_skill_refresh_confirm(task))
        task.click.assert_not_called()
        task.appear_then_click.assert_not_called()
