"""Replay anonymized real scoreboards and round/settlement flow without a device."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import GameStuckError, TaskEnd
from tasks.Chess.config import ChessConfig
from tasks.Chess.runtime.top_four_exit import (
    ChessTopFourExitMixin, parse_player_health, scoreboard_alive_count,
)
from tests import test_chess_port as port


FIXTURES = Path(__file__).parent / 'fixtures/chess/survivors'


def frame(name):
    return cv2.cvtColor(cv2.imread(str(FIXTURES / name)), cv2.COLOR_BGR2RGB)


def boundary_frame(alive):
    """Synthetic boundary assembled from labelled live and eliminated rows."""
    image = frame('two_alive.png')
    live = frame('seven_alive_high_health.png')
    for row in range(alive):
        cy = 72 + 62 * row
        image[cy - 26:cy + 28, 1180:1270] = live[cy - 26:cy + 28, 1180:1270]
    return image


class SurvivorRecognitionTests(unittest.TestCase):
    def test_real_anonymized_captures(self):
        labels = json.loads((FIXTURES / 'labels.json').read_text())
        for name, label in labels.items():
            if not name.endswith('.png'):
                continue
            with self.subTest(name=name):
                self.assertEqual(scoreboard_alive_count(frame(name)), label['alive'])

    def test_four_five_boundary(self):
        for count in (3, 4, 5):
            with self.subTest(count=count):
                self.assertEqual(scoreboard_alive_count(boundary_frame(count)), count)

    def test_missing_health_is_not_elimination(self):
        image = boundary_frame(5)
        image[324:348, 1180:1218] = 0
        self.assertIsNone(scoreboard_alive_count(image))

    def test_missing_row_is_not_elimination(self):
        image = boundary_frame(4)
        image[356:410, 1180:1270] = 0
        self.assertIsNone(scoreboard_alive_count(image))

    def test_non_contiguous_living_rows_are_unknown(self):
        image = boundary_frame(4)
        other = boundary_frame(5)
        image[356:410, 1180:1270] = other[294:348, 1180:1270]
        self.assertIsNone(scoreboard_alive_count(image))

    def test_dark_overlay_is_unknown(self):
        for factor in (.3, .5, .7):
            with self.subTest(factor=factor):
                image = (boundary_frame(5).astype(float) * factor).astype(np.uint8)
                self.assertIsNone(scoreboard_alive_count(image))

    def test_unknown_layout_is_rejected(self):
        for image in (None, np.zeros((720, 1280, 3), dtype=np.uint8),
                      np.zeros((1080, 1920, 3), dtype=np.uint8),
                      np.roll(boundary_frame(4), 10, axis=0)):
            self.assertIsNone(scoreboard_alive_count(image))

    def test_health_parser_rejects_uncertain_values(self):
        for value in ('', None, '0', '-1', '101', 'O', '1O', '1/2', '血量30', '030'):
            with self.subTest(value=value):
                self.assertIsNone(parse_player_health(value))
        for value in (1, 30, 99, 100):
            self.assertEqual(parse_player_health(str(value)), value)

    def test_candidate_health_must_be_valid_and_sorted(self):
        task = SimpleNamespace(device=SimpleNamespace(image=boundary_frame(4)))
        for values, expected in ((['90', '80', '70', '60'], 4),
                                 (['90', '', '70', '60'], None),
                                 (['90', '80', '0', '60'], None),
                                 (['90', '70', '80', '60'], None)):
            with self.subTest(values=values), patch(
                'tasks.Chess.runtime.top_four_exit.RuleOcr'
            ) as rule:
                rule.return_value.ocr.side_effect = values
                self.assertEqual(ChessTopFourExitMixin._read_surviving_player_count(task), expected)

    def test_five_players_do_not_pay_for_health_ocr(self):
        task = SimpleNamespace(device=SimpleNamespace(image=boundary_frame(5)))
        with patch('tasks.Chess.runtime.top_four_exit.RuleOcr') as rule:
            self.assertEqual(ChessTopFourExitMixin._read_surviving_player_count(task), 5)
            rule.assert_not_called()


class TopFourExitFlowTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('tasks.Chess.runtime.top_four_exit.time.sleep')
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def make_task(self):
        task = port.ChessPortTests().make_task()
        task.config.chess.chess_config.coin_full_exit = True
        task.device.stuck_record_clear = Mock()
        task._is_in_chess_game = Mock(return_value=True)
        task._is_shop_open = Mock(return_value=False)
        task.appear = Mock(return_value=False)
        task.chess_result_page_visible = Mock(return_value=False)
        task.exit_chess_battle = Mock(return_value=True)
        task._wait_for_top_four_result = Mock(
            side_effect=lambda: setattr(task, '_last_game_rank', 4)
        )
        task._save_top_four_evidence = Mock()
        task._read_surviving_player_count = Mock(return_value=4)
        task._read_chess_mode = Mock(return_value='备')
        task._read_round_number = Mock(return_value=22)
        task._ensure_shop_closed = Mock(return_value=True)
        def screenshot():
            task.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        task.screenshot = Mock(side_effect=screenshot)
        return task

    def poll(self, task, count=4, mode='备'):
        task.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        task._read_surviving_player_count.return_value = count
        return task._try_top_four_exit(mode)

    def test_three_new_frames_required_then_one_exit(self):
        task = self.make_task()
        self.assertTrue(self.poll(task))
        self.assertEqual(task.screenshot.call_count, 2)
        self.assertEqual(task._read_surviving_player_count.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        task.exit_chess_battle.assert_called_once_with(return_to_lobby=False)
        task._wait_for_top_four_result.assert_called_once()
        self.assertEqual(task._last_game_rank, 4)
        self.assertTrue(task._top_four_exit_succeeded)
        self.assertEqual(task._save_top_four_evidence.call_count, 3)

    def test_logged_battle_candidates_never_exit(self):
        # 25号实测：战阶段识别四人后，分别结算第七、第五及未知。
        # 即便人数识别连续稳定，战/待阶段也不能作为淘汰完成的证明。
        for mode in ('战', '待'):
            task = self.make_task()
            with self.subTest(mode=mode):
                self.assertFalse(task._check_top_four_exit(23, mode))
                self.assertFalse(self.poll(task, mode=mode))
                task._read_surviving_player_count.assert_not_called()
                task.exit_chess_battle.assert_not_called()

    def test_preparation_ending_during_confirmation_cancels_exit(self):
        for next_mode in ('战', '待', None):
            task = self.make_task()
            task._read_chess_mode.return_value = next_mode
            with self.subTest(next_mode=next_mode):
                self.assertFalse(self.poll(task))
                task.exit_chess_battle.assert_not_called()

    def test_round_change_or_unknown_cancels_exit(self):
        for rounds in ([22, 23], [22, None], [19], [None]):
            task = self.make_task()
            task._read_round_number.side_effect = rounds
            with self.subTest(rounds=rounds):
                self.assertFalse(self.poll(task))
                task.exit_chess_battle.assert_not_called()

    def test_failed_or_unknown_rank_blocks_further_early_exits(self):
        for rank in (5, 7, None):
            task = self.make_task()
            task._wait_for_top_four_result.side_effect = (
                lambda: setattr(task, '_last_game_rank', rank)
            )
            with self.subTest(rank=rank):
                # 对局已经退出，仍必须结束回合循环，不能再买卡。
                self.assertTrue(self.poll(task))
                self.assertFalse(task._top_four_exit_succeeded)
                self.assertFalse(task._top_four_exit_enabled())
                task._wait_until_in_chess_game = Mock()
                task._start_chess_game()
                self.assertFalse(self.poll(task))
                task.exit_chess_battle.assert_called_once()

    def test_reused_screenshot_cannot_confirm(self):
        task = self.make_task()
        task.screenshot = Mock()
        self.assertFalse(self.poll(task))
        self.assertEqual(task._read_surviving_player_count.call_count, 1)
        task.exit_chess_battle.assert_not_called()

    def test_five_alive_and_unknown_reset_confirmation(self):
        for interruption in (5, None):
            task = self.make_task()
            task._read_surviving_player_count.side_effect = [4, 4, interruption, 4, interruption]
            self.assertFalse(self.poll(task))
            self.assertFalse(self.poll(task))  # 上次的两次命中不能累计到这里。
            task.exit_chess_battle.assert_not_called()

    def test_count_change_requires_fresh_confirmation(self):
        task = self.make_task()
        task._read_surviving_player_count.side_effect = [4, 4, 3, 3, 3, 3]
        self.assertFalse(self.poll(task))
        self.assertTrue(self.poll(task, 3))

    def test_shop_reopening_during_confirmation_cancels_exit(self):
        task = self.make_task()
        task._is_shop_open.side_effect = [False, True]
        self.assertFalse(self.poll(task))
        task.exit_chess_battle.assert_not_called()

    def test_disabled_modes_and_protection_never_exit(self):
        for setting in ('coin_off', 'option_off', 'rank_protection', 'not_in_game',
                        'shop', 'result', 'grigri', 'ghost', 'unknown'):
            task = self.make_task()
            mode = '备'
            if setting == 'coin_off': task.config.chess.chess_config.coin_full_exit = False
            if setting == 'option_off': task.config.chess.chess_config.top_four_exit = False
            if setting == 'rank_protection': task._rank_protection_exit_requested = True
            if setting == 'not_in_game': task._is_in_chess_game.return_value = False
            if setting == 'shop': task._is_shop_open.return_value = True
            if setting == 'result': task.chess_result_page_visible.return_value = True
            if setting == 'grigri': task.appear.return_value = True
            if setting == 'ghost': mode = '鬼'
            if setting == 'unknown': mode = None
            with self.subTest(setting=setting):
                for _ in range(4): self.assertFalse(self.poll(task, mode=mode))
                task.exit_chess_battle.assert_not_called()

    def test_failed_exit_does_not_resume_normal_actions(self):
        task = self.make_task()
        task.exit_chess_battle.return_value = False
        with self.assertRaises(GameStuckError): self.poll(task)
        task._wait_for_top_four_result.assert_not_called()

    def test_first_nineteen_rounds_never_close_shop_or_read_survivors(self):
        task = self.make_task()
        task._try_top_four_exit = Mock()
        for round_no in range(1, 20):
            self.assertFalse(task._check_top_four_exit(round_no, '备'))
        task._is_shop_open.assert_not_called()
        task._ensure_shop_closed.assert_not_called()
        task._try_top_four_exit.assert_not_called()

    def test_round_twenty_closes_shop_before_reading_then_only_once_per_round(self):
        task = self.make_task()
        task._is_shop_open.return_value = True
        task._try_top_four_exit = Mock(return_value=False)
        sequence = []
        task._ensure_shop_closed.side_effect = lambda **kw: sequence.append('close') or True
        task._try_top_four_exit.side_effect = lambda mode: sequence.append('read') or False
        self.assertFalse(task._check_top_four_exit(20, '备'))
        self.assertEqual(sequence, ['close', 'read'])
        self.assertFalse(task._check_top_four_exit(20, '备'))
        task._ensure_shop_closed.assert_called_once_with(allowed_modes=('备',))
        self.assertFalse(task._check_top_four_exit(21, '备'))
        self.assertEqual(task._ensure_shop_closed.call_count, 2)

    def test_shop_close_failure_skips_reading(self):
        task = self.make_task()
        task._is_shop_open.return_value = True
        task._ensure_shop_closed.return_value = False
        task._try_top_four_exit = Mock()
        self.assertFalse(task._check_top_four_exit(20, '备'))
        task._try_top_four_exit.assert_not_called()

    def test_new_game_resets_shop_check_and_success_state(self):
        task = self.make_task()
        task._top_four_shop_check_round = 20
        task._top_four_exit_succeeded = True
        task._wait_until_in_chess_game = Mock()
        task._start_chess_game()
        self.assertIsNone(task._top_four_shop_check_round)
        self.assertFalse(task._top_four_exit_succeeded)

    def test_active_round_loop_stops_before_economy(self):
        task = self.make_task()
        task.screenshot = Mock()
        task._finish_chess_game_after_markers_missing = Mock(return_value=False)
        task._read_round_number = Mock(return_value=22)
        task._read_chess_mode = Mock(return_value='备')
        task._check_top_four_exit = Mock(return_value=True)
        task.purchase_lineup_cards_once = Mock()
        self.assertIsNone(task.run_one_round(22))
        task._check_top_four_exit.assert_called_once_with(22, '备')
        task.purchase_lineup_cards_once.assert_not_called()

    def test_defaults_preserve_non_coin_games(self):
        config = ChessConfig()
        self.assertTrue(config.top_four_exit)
        self.assertFalse(config.coin_full_exit)

    def test_full_coin_still_finishes_two_rank_protection_exits(self):
        task = self.make_task()
        task.config.chess.chess_config.rank_protection = True
        task.config.chess.chess_config.run_count = -1
        task._recover_interrupted_chess_game = Mock()
        task.goto_page = Mock()
        task.screenshot = Mock()
        task._coin_is_full = Mock(side_effect=[False, True])
        played = []
        def play():
            protection = task._rank_protection_exit_requested
            played.append('protection' if protection else 'normal')
            task._rank_protection_exit_succeeded = protection
            return None if protection else 4
        task.run_one_game = Mock(side_effect=play)
        task.return_to_chess_lobby = Mock()
        task._restart_chess_game_from_result = Mock(return_value=True)
        task._notify_weekly_coin_full = Mock()
        task.set_next_run_next_monday = Mock()
        with self.assertRaises(TaskEnd): task.run()
        self.assertEqual(played, ['normal', 'protection', 'protection'])
        task.return_to_chess_lobby.assert_called_once()
        self.assertEqual(task._restart_chess_game_from_result.call_count, 2)
        task.set_next_run_next_monday.assert_called_once()

    def test_unverified_top_four_flag_does_not_schedule_protection_exits(self):
        for rank in (5, 7, None):
            task = self.make_task()
            task.config.chess.chess_config.rank_protection = True
            task.config.chess.chess_config.run_count = 1
            task._recover_interrupted_chess_game = Mock()
            task.goto_page = Mock()
            task.screenshot = Mock()
            task._coin_is_full = Mock(return_value=False)
            # 模拟旧代码/残留标志，实际名次必须优先。
            task._top_four_exit_succeeded = True
            task.run_one_game = Mock(return_value=rank)
            task.return_to_chess_lobby = Mock()
            task._restart_chess_game_from_result = Mock(return_value=True)
            task.set_next_run = Mock()
            with self.subTest(rank=rank), self.assertRaises(TaskEnd):
                task.run()
            task.run_one_game.assert_called_once()
            task._restart_chess_game_from_result.assert_not_called()

    def test_below_coin_limit_continues_with_normal_run_count(self):
        task = self.make_task()
        task.config.chess.chess_config.run_count = 2
        task._recover_interrupted_chess_game = Mock()
        task.goto_page = Mock()
        task.screenshot = Mock()
        task._coin_is_full = Mock(return_value=False)
        task.run_one_game = Mock(return_value=4)
        task.return_to_chess_lobby = Mock()
        task.set_next_run = Mock()
        with self.assertRaises(TaskEnd): task.run()
        self.assertEqual(task.run_one_game.call_count, 2)
        self.assertEqual(task.return_to_chess_lobby.call_count, 2)

    def test_settlement_requires_visible_result_and_uses_actual_rank(self):
        for visible, rank in ((True, 3), (True, None), (False, None)):
            task = self.make_task()
            task.screenshot = Mock()
            task.appear.return_value = visible
            task.chess_result_page_visible.return_value = not visible
            task._read_game_rank = Mock(return_value=(rank, '第三名' if rank else ''))
            ChessTopFourExitMixin._wait_for_top_four_result(task)
            self.assertEqual(task._last_game_rank, rank)
            if not visible: task._read_game_rank.assert_not_called()

    def test_missing_result_times_out_with_unknown_rank(self):
        task = self.make_task()
        task.screenshot = Mock()
        task._last_game_rank = 4
        with patch('tasks.Chess.runtime.top_four_exit.time.monotonic', side_effect=[0, 61]):
            with self.assertRaises(GameStuckError):
                ChessTopFourExitMixin._wait_for_top_four_result(task)
        self.assertIsNone(task._last_game_rank)


if __name__ == '__main__':
    unittest.main()
