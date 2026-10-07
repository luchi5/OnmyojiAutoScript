"""Chess result regression tests; no Config, Device, OCR, ADB or game control."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import AccountLoggedInElsewhere, GameTooManyClickError
from tasks.Chess.assets import ChessAssets
from tasks.Chess.runtime.result_navigation import (
    ChessResultNavigationMixin, _share_panel_rule, chess_game_ui_type,
)
from tasks.Chess.script_task import ScriptTask, ChessLegacyScriptTask
from tasks.base_task import BaseTask
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.page import page_chess_result
from tests.test_chess_navigation import Screen, ScriptedNavigation


LOBBY = (GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START)
PANEL = (GameUiAssets.I_CHESS_SHARE_PANEL_BAR,
         GameUiAssets.I_CHESS_SHARE_PANEL_CLOSE, ChessAssets.I_SHARE)
RANK = (GameUiAssets.I_CHECK_CHESS_RANK, GameUiAssets.I_CHESS_RANK_GOTO_LOBBY)


class FrameChess(ScriptTask):
    """Each screenshot provides the next observed frame; clicks only record."""
    def __init__(self, frames):
        self.frames = frames
        self.visible = ()
        self.screenshot_count = 0
        self.clicked = []
        self.device = SimpleNamespace(stuck_record_clear=Mock(), click_record_clear=Mock(),
                                      click_record_remove=Mock())

    def screenshot(self):
        if self.screenshot_count >= len(self.frames):
            raise AssertionError('Read beyond the scripted frames')
        frame = self.frames[self.screenshot_count]
        self.screenshot_count += 1
        if isinstance(frame, Exception):
            raise frame
        self.visible = frame

    def appear(self, marker, **kwargs):
        return any(marker is item for item in self.visible)

    def click(self, marker, **kwargs):
        self.clicked.append((marker, self.screenshot_count, kwargs))
        return True

    def appear_then_click(self, marker, **kwargs):
        return self.click(marker, **kwargs) if self.appear(marker) else False


class ChessShareGuardTests(unittest.TestCase):
    def setUp(self):
        sleep_patch = patch('tasks.Chess.runtime.result_navigation.time.sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_results_and_share_panel_override_both_lobby_markers(self):
        for overlay in ((ChessAssets.I_SHARE,), (ChessAssets.I_REWARD_CHESS,), RANK,
                        (ChessAssets.I_RESTART_AGAIN,), PANEL):
            with self.subTest(overlay=overlay):
                task = FrameChess([LOBBY + overlay])
                task.screenshot()
                self.assertFalse(task.chess_lobby_visible())
                self.assertTrue(task.chess_result_page_visible())
                self.assertEqual(task.clicked, [])

    def test_share_panel_navigation_takes_priority_over_lobby(self):
        task = ScriptedNavigation([Screen(LOBBY + PANEL)])
        self.assertEqual(task.get_current_page(), page_chess_result)
        self.assertFalse(task.clicked)

    def test_share_panel_requires_platform_bar_and_close_together(self):
        for frame in ((PANEL[0],), (PANEL[1],), (PANEL[0], PANEL[1]), ()):
            task = FrameChess([frame])
            task.screenshot()
            self.assertFalse(task.chess_share_panel_visible())
            self.assertFalse(task._advance_chess_result_stage())
            self.assertFalse(task.clicked)

    def test_share_panel_closes_only_x_then_continues_result_to_lobby(self):
        task = FrameChess([LOBBY + PANEL + (ChessAssets.I_SHARE,),
                           LOBBY + (ChessAssets.I_SHARE,), RANK, LOBBY, LOBBY])
        self.assertTrue(task.return_to_chess_lobby())
        self.assertEqual([item[0] for item in task.clicked], [PANEL[1], task.CHESS_RESULT_CONTINUE,
                                                          GameUiAssets.I_CHESS_RANK_GOTO_LOBBY])
        task.device.click_record_clear.assert_not_called()
        task.device.click_record_remove.assert_not_called()
        self.assertNotIn(ChessAssets.I_CHESS_START, [item[0] for item in task.clicked])

    def test_lobby_needs_two_frames_before_start_and_clicks_recognized_start(self):
        task = FrameChess([(GameUiAssets.I_CHECK_CHESS,), (ChessAssets.I_CHESS_START,),
                           LOBBY, LOBBY, (ChessAssets.I_OPEN_LINEUP,)])
        task._wait_until_in_chess_game(timeout=30, retry_start=True)
        self.assertEqual(len(task.clicked), 1)
        marker, frame, kwargs = task.clicked[0]
        self.assertIs(marker, ChessAssets.I_CHESS_START)
        self.assertEqual(frame, 4)
        self.assertEqual(kwargs['interval'], 2 * task.SLOW_POLL_INTERVAL)

    def test_reward_overlay_does_not_skip_real_return_button(self):
        task = FrameChess([(ChessAssets.I_REWARD_CHESS,),
                           (GameUiAssets.I_CHESS_EXIT_TO_LOBBY,),
                           (ChessAssets.I_SHARE,), RANK, LOBBY, LOBBY])
        self.assertTrue(task.return_to_chess_lobby())
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE,
                         GameUiAssets.I_CHESS_EXIT_TO_LOBBY, task.CHESS_RESULT_CONTINUE,
                         GameUiAssets.I_CHESS_RANK_GOTO_LOBBY])

    def test_reward_to_result_still_restarts_from_rank(self):
        task = FrameChess([(ChessAssets.I_REWARD_CHESS,),
                           (GameUiAssets.I_CHESS_EXIT_TO_LOBBY,),
                           (ChessAssets.I_SHARE,),
                           (GameUiAssets.I_CHECK_CHESS_RANK, ChessAssets.I_RESTART_AGAIN),
                           (ChessAssets.I_OPEN_LINEUP,)])
        self.assertTrue(task._restart_chess_game_from_result())
        self.assertEqual([item[0] for item in task.clicked], [task.CHESS_RESULT_CONTINUE,
                         GameUiAssets.I_CHESS_EXIT_TO_LOBBY, task.CHESS_RESULT_CONTINUE,
                         ChessAssets.I_RESTART_AGAIN])

    def test_rank_restart_wins_over_lingering_share_icon(self):
        task = FrameChess([(ChessAssets.I_SHARE, GameUiAssets.I_CHECK_CHESS_RANK,
                            ChessAssets.I_RESTART_AGAIN), (ChessAssets.I_OPEN_LINEUP,)])
        self.assertTrue(task._restart_chess_game_from_result())
        self.assertEqual([item[0] for item in task.clicked], [ChessAssets.I_RESTART_AGAIN])

    def test_visible_return_button_wins_over_lingering_share_icon(self):
        task = FrameChess([(ChessAssets.I_SHARE, GameUiAssets.I_CHESS_EXIT_TO_LOBBY),
                           (ChessAssets.I_SHARE,), RANK, LOBBY, LOBBY])
        self.assertTrue(task.return_to_chess_lobby())
        self.assertIs(task.clicked[0][0], GameUiAssets.I_CHESS_EXIT_TO_LOBBY)
        self.assertEqual(len(task.clicked), 3)

    def test_one_transient_lobby_frame_does_not_skip_rank_restart(self):
        task = FrameChess([LOBBY, (GameUiAssets.I_CHECK_CHESS_RANK, ChessAssets.I_RESTART_AGAIN),
                           (ChessAssets.I_OPEN_LINEUP,)])
        self.assertTrue(task._restart_chess_game_from_result())
        self.assertEqual([item[0] for item in task.clicked], [ChessAssets.I_RESTART_AGAIN])

    def test_normal_stable_lobby_returns_without_result_click(self):
        task = FrameChess([LOBBY, LOBBY])
        self.assertFalse(task._restart_chess_game_from_result())
        self.assertFalse(task.clicked)
        self.assertEqual(task.screenshot_count, 2)

    def test_startup_recovery_handles_share_before_lobby_background(self):
        task = FrameChess([LOBBY + PANEL])
        task.return_to_chess_lobby = Mock(return_value=True)
        self.assertTrue(task._recover_interrupted_chess_game())
        task.return_to_chess_lobby.assert_called_once_with()

    def test_safe_continue_region_cannot_reach_share_or_platform_buttons(self):
        x, y, w, h = ChessResultNavigationMixin.CHESS_RESULT_CONTINUE.roi_front
        self.assertLess(x + w, 100)
        self.assertLess(y + h, 568)

    def test_account_takeover_exception_propagates_without_click(self):
        for method, kwargs in (('return_to_chess_lobby', {}),
                               ('_restart_chess_game_from_result', {}),
                               ('_wait_until_in_chess_game', {'timeout': 30, 'retry_start': True})):
            with self.subTest(method=method):
                task = FrameChess([AccountLoggedInElsewhere('fixture')])
                with self.assertRaises(AccountLoggedInElsewhere):
                    getattr(task, method)(**kwargs)
                self.assertFalse(task.clicked)

    def test_share_close_keeps_repeated_click_guard_exception(self):
        task = FrameChess([PANEL])
        task.click = Mock(side_effect=GameTooManyClickError('fixture'))
        with self.assertRaises(GameTooManyClickError):
            task.return_to_chess_lobby()
        task.device.click_record_clear.assert_not_called()
        task.device.click_record_remove.assert_not_called()

    def test_old_cached_game_ui_gets_new_guard_without_mutating_old_class(self):
        class OldChessMixin:
            def _advance_chess_result_stage(self):
                raise AssertionError('Old cached result path used')

        class LegacyGameUi(OldChessMixin, BaseTask, GameUiAssets):
            pass

        adapted = chess_game_ui_type(LegacyGameUi)
        self.assertIs(adapted._advance_chess_result_stage,
                      ChessResultNavigationMixin._advance_chess_result_stage)
        self.assertTrue(issubclass(adapted, LegacyGameUi))
        self.assertFalse(hasattr(LegacyGameUi, 'chess_lobby_visible'))
        # Keep the real other bases (including GeneralBattle -> BaseTask) to
        # exercise the same diamond inheritance as a cached production worker.
        bases = tuple(adapted if issubclass(base, GameUi) else base
                      for base in ChessLegacyScriptTask.__bases__)
        legacy_task_type = type('LegacyChessTask', bases, {})
        task = object.__new__(legacy_task_type)
        task.appear = lambda marker, **kwargs: any(marker is item for item in PANEL)
        task.click = Mock()
        self.assertTrue(task._advance_chess_result_stage())
        task.click.assert_called_once_with(PANEL[1], interval=1.5)
        # Fresh GameUi already inherits the canonical guard: no conflicting MRO.
        self.assertIs(chess_game_ui_type(GameUi), GameUi)

    def test_old_cached_assets_fallback_loads_json_rules(self):
        with patch('tasks.Chess.runtime.result_navigation.GameUiAssets', SimpleNamespace()):
            for name in ('chess_share_panel_bar', 'chess_share_panel_close'):
                rule = _share_panel_rule(name)
                self.assertTrue(Path(rule.file).is_file())
                self.assertGreater(rule.image.size, 0)


class ChessShareImageTests(unittest.TestCase):
    def test_templates_are_small_public_ui_fragments_registered_in_resource(self):
        records = json.loads(Path('tasks/GameUi/page/image_chess.json').read_text(encoding='utf-8'))
        for name, rule in (('chess_share_panel_bar', PANEL[0]), ('chess_share_panel_close', PANEL[1])):
            item = next(item for item in records if item['itemName'] == name)
            self.assertEqual(tuple(map(int, item['roiBack'].split(','))), rule.roi_back)
            self.assertEqual(item['threshold'], rule.threshold)
            self.assertLess(rule.image.shape[0], 100)
            self.assertLess(rule.image.shape[1], 200)

    def test_real_saved_share_panels_match_both_markers(self):
        matched = 0
        for folder in ('1791283091271', '1791283120594'):
            files = list((Path('log/error') / folder).glob('*.png'))
            if not files:
                continue
            frame = cv2.imdecode(np.fromfile(str(files[0]), dtype=np.uint8), cv2.IMREAD_COLOR)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self.assertTrue(PANEL[0].match(frame))
            self.assertTrue(PANEL[1].match(frame))
            self.assertFalse(GameUiAssets.I_CHECK_CHESS.match(frame))
            self.assertFalse(ChessAssets.I_CHESS_START.match(frame))
            matched += 1
        if not matched:
            self.skipTest('Local incident screenshots are not part of the public repository')
        self.assertEqual(matched, 2)

    def test_platform_bar_alone_does_not_authorize_closing(self):
        # Uses only the cropped public UI fragment, no user/QR/account material.
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, _, _ = PANEL[0].roi_front
        h, w = PANEL[0].image.shape[:2]
        frame[y:y + h, x:x + w] = PANEL[0].image
        self.assertTrue(PANEL[0].match(frame))
        self.assertFalse(PANEL[1].match(frame))


if __name__ == '__main__':
    unittest.main()
