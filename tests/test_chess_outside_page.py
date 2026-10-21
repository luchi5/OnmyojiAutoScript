"""Regression for account 09 reaching collection after Chess settlement."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import GameStuckError
from tasks.Chess.assets import ChessAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GlobalGame.assets import GlobalGameAssets
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
        task.goto_page = Mock(side_effect=lambda _: setattr(task, "visible", LOBBY))
        self.assertTrue(task.return_to_chess_lobby())
        task.goto_page.assert_called_once_with(page_chess)
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_collection_recovery_during_start_waits_for_stable_lobby(self):
        task = FrameChess([(GameUiAssets.I_CHECK_COLLECTION,), LOBBY, LOBBY,
                           (ChessAssets.I_OPEN_LINEUP,)])
        task.goto_page = Mock(side_effect=lambda _: setattr(task, "visible", LOBBY))
        task._wait_until_in_chess_game(timeout=30, retry_start=True)
        task.goto_page.assert_called_once_with(page_chess)
        self.assertEqual([item[0] for item in task.clicked], [ChessAssets.I_CHESS_START])
        self.assertEqual(task.clicked[0][1], 3)

    def test_rank_restart_returns_to_lobby_after_collection_recovery(self):
        task = FrameChess([(GameUiAssets.I_CHECK_COLLECTION,), LOBBY, LOBBY])
        task.goto_page = Mock(side_effect=lambda _: setattr(task, "visible", LOBBY))
        self.assertFalse(task._restart_chess_game_from_result())
        task.goto_page.assert_called_once_with(page_chess)
        self.assertFalse(task.clicked)

    def test_rank_progress_overrides_false_courtyard_match(self):
        task = FrameChess([])
        task.visible = (task.I_CHESS_RANK_PROGRESS, GameUiAssets.I_CHECK_MAIN)
        task.goto_page = Mock()
        self.assertFalse(task._chess_outside_page_visible())
        self.assertTrue(task.chess_result_page_visible())
        self.assertFalse(task.chess_lobby_visible())
        self.assertTrue(task._advance_chess_result_stage())
        task.goto_page.assert_not_called()
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_result_continue_hint_and_share_override_false_courtyard_match(self):
        task = FrameChess([])
        task.visible = (task.I_CHESS_RESULT_CONTINUE_HINT, ChessAssets.I_SHARE,
                        GameUiAssets.I_CHECK_MAIN)
        task.goto_page = Mock()
        self.assertFalse(task._chess_outside_page_visible())
        self.assertTrue(task.chess_result_page_visible())
        self.assertFalse(task.chess_lobby_visible())
        self.assertTrue(task._advance_chess_result_stage())
        task.goto_page.assert_not_called()
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_continue_hint_alone_does_not_override_confirmed_outside_page(self):
        for markers in ((ChessAssets.I_SHARE,), (FrameChess.I_CHESS_RESULT_CONTINUE_HINT,)):
            task = FrameChess([])
            task.visible = markers + (GameUiAssets.I_CHECK_MAIN,)
            self.assertTrue(task._chess_outside_page_visible())
            task._click_chess_result_continue()
            self.assertFalse(task.clicked)

    def test_recovery_navigation_can_advance_rank_page_with_false_courtyard_match(self):
        task = FrameChess([])
        result = (task.I_CHESS_RESULT_CONTINUE_HINT, ChessAssets.I_SHARE, GameUiAssets.I_CHECK_MAIN)
        task.frames = [result, LOBBY, LOBBY]
        # Reproduce the old failure's nested navigation while recovery is active.
        task._chess_outside_recovering = True
        task.goto_page = Mock(side_effect=AssertionError('Unexpected nested navigation'))
        self.assertTrue(task.return_to_chess_lobby())
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_saved_third_place_incident_continues_despite_false_main_match(self):
        files = list(Path('log/error/1792559037422').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], dtype=np.uint8), cv2.IMREAD_COLOR),
                             cv2.COLOR_BGR2RGB)
        task = FrameChess([])
        task.appear = lambda marker, **kwargs: (marker is GameUiAssets.I_CHECK_MAIN or marker.match(frame))
        task.goto_page = Mock()
        self.assertTrue(task.chess_result_continue_visible())
        self.assertFalse(task._chess_outside_page_visible())
        self.assertTrue(task.chess_result_page_visible())
        self.assertTrue(task._advance_chess_result_stage())
        task.goto_page.assert_not_called()
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_saved_account24_second_place_continues_during_recovery(self):
        files = list(Path('log/error/1792575062072').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], np.uint8), cv2.IMREAD_COLOR),
                             cv2.COLOR_BGR2RGB)
        task = FrameChess([])
        task._chess_outside_recovering = True
        task.appear = lambda marker, **kwargs: (marker is GameUiAssets.I_CHECK_MAIN
                                               or marker.match(frame))
        task.goto_page = Mock(side_effect=AssertionError('Unexpected nested navigation'))
        self.assertTrue(task.chess_result_continue_visible())
        self.assertFalse(task._chess_outside_page_visible())
        self.assertTrue(task._advance_chess_result_stage())
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_new_reward_overlay_advances_despite_false_courtyard_match(self):
        task = FrameChess([])
        task.visible = (GlobalGameAssets.I_UI_REWARD, GameUiAssets.I_CHECK_MAIN)
        task.goto_page = Mock()
        self.assertTrue(task._advance_chess_result_stage())
        self.assertTrue(task.chess_result_page_visible())
        task.goto_page.assert_not_called()
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_both_rank_track_ornaments_are_required(self):
        task = FrameChess([])
        for marker in (task.I_CHESS_RANK_TRACK_LEFT, task.I_CHESS_RANK_TRACK_RIGHT):
            task.visible = (marker, GameUiAssets.I_CHECK_MAIN)
            self.assertFalse(task._chess_rank_progress_visible())
            self.assertTrue(task._chess_outside_page_visible())
        task.visible = (task.I_CHESS_RANK_TRACK_LEFT, task.I_CHESS_RANK_TRACK_RIGHT,
                        GameUiAssets.I_CHECK_MAIN)
        self.assertTrue(task._chess_rank_progress_visible())
        self.assertFalse(task._chess_outside_page_visible())

    def test_new_result_stages_return_to_lobby_without_recursive_navigation(self):
        task = FrameChess([])
        false_main = (GameUiAssets.I_CHECK_MAIN,)
        task.frames = [false_main + (task.I_CHESS_RESULT_CONTINUE_HINT, ChessAssets.I_SHARE),
                       false_main + (task.I_CHESS_RANK_TRACK_LEFT, task.I_CHESS_RANK_TRACK_RIGHT),
                       false_main + (GlobalGameAssets.I_UI_REWARD,),
                       LOBBY, LOBBY]
        task.goto_page = Mock(side_effect=AssertionError('Unexpected outside navigation'))
        self.assertTrue(task.return_to_chess_lobby())
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE] * 3)

    def test_saved_new_rank_and_reward_frames_advance(self):
        directory = Path.cwd().parent / 'migration-records/chess-third-place-20261021-1303'
        for name, stage in (('live-rank-progress.png', 'rank'), ('live-after-user-click.png', 'reward')):
            with self.subTest(stage=stage):
                path = directory / name
                if not path.exists():
                    self.skipTest('Local live screenshots are not part of the public repository')
                frame = cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR),
                                     cv2.COLOR_BGR2RGB)
                task = FrameChess([])
                task.appear = lambda marker, **kwargs: (marker is GameUiAssets.I_CHECK_MAIN
                                                       or marker.match(frame))
                task.goto_page = Mock()
                self.assertFalse(task._chess_outside_page_visible())
                self.assertTrue(task.chess_result_page_visible())
                self.assertTrue(task._advance_chess_result_stage())
                task.goto_page.assert_not_called()
                self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

    def test_saved_rank_progress_continues_without_global_navigation(self):
        files = list(Path('log/error/1791958714553').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = cv2.imdecode(np.fromfile(str(files[0]), dtype=np.uint8), cv2.IMREAD_COLOR)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        task = FrameChess([])
        task.appear = lambda marker, **kwargs: marker.match(frame)
        task.goto_page = Mock()
        self.assertTrue(task.I_CHESS_RANK_PROGRESS.match(frame))
        self.assertTrue(task.chess_result_page_visible())
        self.assertFalse(task.chess_lobby_visible())
        self.assertTrue(task._advance_chess_result_stage())
        task.goto_page.assert_not_called()
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE])

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
        task.goto_page = Mock(side_effect=lambda _: setattr(task, "appear", lambda marker, **kwargs: False))
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
