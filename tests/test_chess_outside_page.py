"""Regression for account 09 reaching collection after Chess settlement."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import GameStuckError
from tasks.Chess.assets import ChessAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_chess
from tests.test_chess_share_guard import FrameChess, LOBBY, RANK


class ChessOutsidePageTests(unittest.TestCase):
    def setUp(self):
        sleep_patch = patch('tasks.Chess.runtime.result_navigation.time.sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_confirmed_other_pages_override_similar_result_and_lobby_buttons(self):
        for page in (GameUiAssets.I_CHECK_COLLECTION, GameUiAssets.I_CHECK_MAIN):
            with self.subTest(page=page):
                task = FrameChess([(page,) + LOBBY + RANK + (ChessAssets.I_SHARE,)])
                task.screenshot()
                task._chess_share_advancing = True
                self.assertFalse(task.chess_lobby_visible())
                self.assertFalse(task.chess_result_page_visible())
                task._click_chess_result_continue()
                self.assertFalse(task.clicked)
                self.assertFalse(task._chess_share_advancing)

    def test_result_flow_navigates_from_courtyard_without_clicking_collection(self):
        task = FrameChess([(ChessAssets.I_SHARE,),
                           (GameUiAssets.I_CHECK_MAIN,) + RANK,
                           LOBBY, LOBBY])
        task.goto_page = Mock()
        self.assertTrue(task.return_to_chess_lobby())
        task.goto_page.assert_called_once_with(page_chess)
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_collection_recovery_during_start_waits_for_stable_lobby(self):
        task = FrameChess([(GameUiAssets.I_CHECK_COLLECTION,), LOBBY, LOBBY,
                           (ChessAssets.I_OPEN_LINEUP,)])
        task.goto_page = Mock()
        task._wait_until_in_chess_game(timeout=30, retry_start=True)
        task.goto_page.assert_called_once_with(page_chess)
        self.assertEqual([item[0] for item in task.clicked], [ChessAssets.I_CHESS_START])
        self.assertEqual(task.clicked[0][1], 3)

    def test_rank_restart_returns_to_lobby_after_collection_recovery(self):
        task = FrameChess([(GameUiAssets.I_CHECK_COLLECTION,), LOBBY, LOBBY])
        task.goto_page = Mock()
        self.assertFalse(task._restart_chess_game_from_result())
        task.goto_page.assert_called_once_with(page_chess)
        self.assertFalse(task.clicked)

    def test_unknown_loading_frame_does_not_trigger_global_navigation(self):
        task = FrameChess([()])
        task.screenshot()
        task.goto_page = Mock()
        self.assertFalse(task._recover_chess_outside_page())
        task.goto_page.assert_not_called()
        self.assertFalse(task.clicked)

    def test_failed_navigation_propagates_without_result_clicks(self):
        task = FrameChess([(GameUiAssets.I_CHECK_COLLECTION,)])
        task.goto_page = Mock(side_effect=GameStuckError('fixture'))
        with self.assertRaises(GameStuckError):
            task.return_to_chess_lobby()
        self.assertFalse(task.clicked)

    def test_saved_incident_recognizes_collection_and_blocks_result_actions(self):
        files = list(Path('log/error/1791775331979').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.imdecode(np.fromfile(str(files[0]), dtype=np.uint8), cv2.IMREAD_COLOR)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        task = FrameChess([])
        task.appear = lambda marker, **kwargs: marker.match(frame)
        task.goto_page = Mock()
        self.assertTrue(GameUiAssets.I_CHECK_COLLECTION.match(frame))
        self.assertFalse(task.chess_lobby_visible())
        self.assertFalse(task.chess_result_page_visible())
        task._click_chess_result_continue()
        self.assertFalse(task.clicked)
        self.assertTrue(task._advance_chess_result_stage())
        task.goto_page.assert_called_once_with(page_chess)
        self.assertFalse(task.clicked)

    def test_saved_live_battle_does_not_trigger_courtyard_recovery(self):
        path = Path.cwd().parent / 'migration-records/account09-records-check-20261012/live.png'
        if not path.is_file():
            self.skipTest('Local live battle screenshot is not part of the public repository')
        frame = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        task = FrameChess([])
        task.appear = lambda marker, **kwargs: marker.match(frame)
        task.goto_page = Mock()
        self.assertFalse(task._recover_chess_outside_page())
        task.goto_page.assert_not_called()
        self.assertFalse(task.clicked)


if __name__ == '__main__':
    unittest.main()
