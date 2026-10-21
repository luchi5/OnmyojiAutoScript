import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import cv2
from module.atom.image import RuleImage
from dev_tools.test_bondling_battle_entry import extract_methods

ROOT = Path(__file__).resolve().parents[1]


class ExitRoomTests(unittest.TestCase):
    def run_exit(self, closes=True, show_dialog=True):
        frame = cv2.cvtColor(cv2.imread(str(
            ROOT / 'dev_tools/fixtures/bondling_exit_room.png')), cv2.COLOR_BGR2RGB)
        elapsed = [0]
        clicks = []
        def sleep(seconds):
            elapsed[0] += seconds
        def closed():
            return closes and bool(clicks)
        def appear(rule):
            if closed():
                return False
            if isinstance(rule, str):
                return rule == 'back'
            return show_dialog and rule.match(frame)
        task = NS(is_in_room=lambda: not closed(), screenshot=Mock(),
                  appear=appear, click=lambda rule: clicks.append(str(rule)),
                  I_GI_SURE='legacy', I_BACK_YELLOW='back')
        method = extract_methods(
            ROOT / 'tasks/BondlingFairyland/general_invite.py',
            'GeneralInvite', ('exit_room',),
            dict(RuleImage=RuleImage, monotonic=lambda: elapsed[0], sleep=sleep,
                 logger=Mock(), GameStuckError=RuntimeError))['exit_room']
        self.clicks = clicks
        return method(task)

    def test_actual_dialog_confirms_without_back_click(self):
        self.assertTrue(self.run_exit())
        self.assertEqual(self.clicks, ['EXIT_ROOM_CONFIRM'])

    def test_stuck_dialog_has_bounded_confirm_and_no_back_click(self):
        with self.assertRaises(RuntimeError):
            self.run_exit(closes=False)
        self.assertEqual(self.clicks, ['EXIT_ROOM_CONFIRM'] * 3)

    def test_missing_dialog_has_bounded_back_clicks(self):
        with self.assertRaises(RuntimeError):
            self.run_exit(closes=False, show_dialog=False)
        self.assertEqual(self.clicks, ['back'] * 3)


if __name__ == '__main__':
    unittest.main()
