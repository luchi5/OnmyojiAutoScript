"""Friend fostering completion, including account05's saved successful placement."""
from pathlib import Path
from types import SimpleNamespace
import re
import unittest
from unittest.mock import Mock

from dev_tools.test_bondling_battle_entry import extract_methods

ROOT = Path(__file__).resolve().parents[1]


class FosteringConfirmationTests(unittest.TestCase):
    def check_time(self, values, records=True):
        method = extract_methods(ROOT / 'tasks/KekkaiUtilize/script_task.py',
                                 'ScriptTask', ('_friend_fostering_active',), {'re': re})['_friend_fostering_active']
        rules = [SimpleNamespace(ocr=Mock(return_value=value)) for value in values]
        task = SimpleNamespace(I_RS_RECORDS_SHIKI='records',
                               appear=Mock(return_value=records),
                               device=SimpleNamespace(image=None),
                               _friend_fostering_timers=rules)
        return method(task), rules

    def test_valid_countdown_in_either_slot_confirms(self):
        for values in [('05:59:42', ''), ('', '00:00:01'), ('05：59：42', '')]:
            self.assertTrue(self.check_time(values)[0])

    def test_empty_expired_and_invalid_times_do_not_confirm(self):
        for value in ('', '99999999', '00:00:00', '05:99:00', '25:00:00', '式神育成'):
            self.assertFalse(self.check_time([value, ''])[0])

    def test_selection_panel_or_other_page_never_reads_countdown(self):
        result, rules = self.check_time(['05:59:42'], records=False)
        self.assertFalse(result)
        rules[0].ocr.assert_not_called()

    def test_saved_success_with_friend_button_still_visible_never_clicks_again(self):
        import cv2
        import numpy as np
        from tasks.KekkaiUtilize.script_task import ScriptTask
        frame = ROOT / 'log/error/1792571855121/2026-10-21_16-37-35-103527.png'
        if not frame.exists():
            self.skipTest('Local account05 replay frame unavailable')
        image = cv2.imdecode(np.frombuffer(frame.read_bytes(), dtype=np.uint8), 1)
        task = ScriptTask.__new__(ScriptTask)
        task.device = SimpleNamespace(image=image)
        task.screenshot = Mock()
        task.appear = lambda rule: rule.match(image)
        task.click = Mock(side_effect=AssertionError('Must not select after fostering succeeded'))
        task.appear_then_click = Mock(return_value=True)
        self.assertTrue(task.appear(task.I_U_ADD_1))
        self.assertTrue(task._friend_fostering_active())
        task.set_shikigami(1, task.I_U_ADD_1)
        self.assertEqual(task.screenshot.call_count, 3)
        task.appear_then_click.assert_called_once_with(task.I_U_CONFIRM_SMALL, interval=.5)
        task.click.assert_not_called()

    def test_existing_countdown_before_our_confirmation_does_not_skip_selection(self):
        from tasks.KekkaiUtilize.script_task import ScriptTask
        task = ScriptTask.__new__(ScriptTask)
        task.screenshot = Mock()
        task.appear = Mock(side_effect=[True, False])
        task.appear_then_click = Mock(return_value=False)
        task.click = Mock(return_value=True)
        task._friend_fostering_active = Mock(return_value=True)
        task.set_shikigami(1, task.I_U_ADD_1)
        task.click.assert_called_once_with(task.C_SHIKIGAMI_LEFT_1, interval=1.5)
        task._friend_fostering_active.assert_not_called()


if __name__ == '__main__':
    unittest.main()
