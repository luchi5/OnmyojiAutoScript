"""First-entry tutorial regression: offline only, no device inputs."""
from pathlib import Path
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from module.exception import GameStuckError
from tasks.Chess.assets import ChessAssets as A
from tests.test_chess_share_guard import FrameChess, LOBBY

MODAL = LOBBY + (A.I_TUTORIAL_PROMPT, A.I_TUTORIAL_SKIP)

class TutorialTests(unittest.TestCase):
    def run_entry(self, task):
        with patch('tasks.Chess.script_task.time.sleep'):
            task._wait_until_in_chess_game(timeout=30, retry_start=True)

    def test_first_entry_skips_instead_of_clicking_start_under_modal(self):
        task = FrameChess([LOBBY, LOBBY, MODAL, (A.I_OPEN_LINEUP,)])
        self.run_entry(task)
        self.assertEqual([c[0] for c in task.clicked], [A.I_CHESS_START, A.I_TUTORIAL_SKIP])

    def test_modal_already_present_on_restart(self):
        task = FrameChess([MODAL, (A.I_OPEN_LINEUP,)])
        self.run_entry(task)
        self.assertEqual([c[0] for c in task.clicked], [A.I_TUTORIAL_SKIP])

    def test_stuck_modal_has_bounded_retries(self):
        task = FrameChess([MODAL] * 4)
        with self.assertRaises(GameStuckError):
            self.run_entry(task)
        self.assertEqual([c[0] for c in task.clicked], [A.I_TUTORIAL_SKIP] * 3)

    def test_title_without_skip_does_not_click_start_or_confirm(self):
        task = FrameChess([LOBBY + (A.I_TUTORIAL_PROMPT,)])
        with self.assertRaises(GameStuckError):
            self.run_entry(task)
        self.assertFalse(task.clicked)

    def test_normal_lobby_unaffected(self):
        task = FrameChess([LOBBY, LOBBY, (A.I_OPEN_LINEUP,)])
        self.run_entry(task)
        self.assertEqual([c[0] for c in task.clicked], [A.I_CHESS_START])

    def test_actual_account22_modal_and_negative_frames(self):
        path = Path('log/error/1792770167862/2026-10-23_23-42-47-858740.png')
        if not path.exists():
            self.skipTest('Private incident screenshot not distributed')
        rgb = cv2.cvtColor(cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1), cv2.COLOR_BGR2RGB)
        for marker in (A.I_TUTORIAL_PROMPT, A.I_TUTORIAL_SKIP):
            self.assertTrue(marker.match(rgb))
            self.assertFalse(marker.match(np.zeros_like(rgb)))
        without_title = rgb.copy()
        without_title[288:343, 541:741] = 0
        self.assertFalse(A.I_TUTORIAL_PROMPT.match(without_title))
        self.assertTrue(A.I_TUTORIAL_SKIP.match(without_title))

if __name__ == '__main__':
    unittest.main()
