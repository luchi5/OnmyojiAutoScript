"""Runhey integration checks; no real Device, ADB or game actions."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.atom.image import RuleImage
from module.config.config_model import ConfigModel
from module.exception import TaskEnd
from tasks.Chess.assets import ChessAssets
from tasks.Chess.config import Chess
from tasks.Chess.runtime.decision import ChessAction, decide_round
from tasks.Chess.runtime.image import ChessImage
from tasks.Chess.runtime.press_and_drag import Press_and_Drag
from tasks.Chess.runtime.state import ChessGameState, ChessObservation, ChessRoundProgress
from tasks.Chess.script_task import ScriptTask
from tasks.Chess.strategy.lineup import LINEUP_REGISTRY
from tasks.Component.config_base import Time


class ChessPortTests(unittest.TestCase):
    def make_task(self):
        model = ConfigModel()
        config = SimpleNamespace(
            model=model, chess=model.chess, global_game=model.global_game,
        )
        # Missing xy-only attributes deliberately exercise the runhey API.
        device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        return ScriptTask(config, device)

    def test_task_constructs_with_mainline_config_and_stays_disabled(self):
        task = self.make_task()
        self.assertEqual(task.get_task_name(), 'Chess')
        self.assertFalse(task.config.chess.scheduler.enable)
        self.assertIsInstance(task.chess_state, ChessGameState)

    def test_generated_assets_and_all_lineups_load_with_mainline_rule_image(self):
        task = self.make_task()
        rules = [value for value in vars(ChessAssets).values() if isinstance(value, RuleImage)]
        rules.extend([task.store_gold_rule, *task.board_occupancy_rules])
        rules.extend(rule for _, rule in task.shikigami_hand_rules)
        rules.extend(rule for _, rule in task.all_shikigami_shop_rules)
        rules.extend(rule for _, rule in task.soul_hand_rules)
        for key in LINEUP_REGISTRY:
            task.select_lineup_strategy(key)
            self.assertTrue(task.lineup_shikigami_hand_rules)
            self.assertTrue(task.shikigami_shop_rules)
            rules.extend(rule for _, rule in task.lineup_shikigami_hand_rules)
            rules.extend(rule for _, rule in task.shikigami_shop_rules)
            rules.append(task.hakuzosu_protect_rule)
        for rule in rules:
            with self.subTest(file=rule.file):
                self.assertTrue(Path(rule.file).is_file())
                self.assertGreater(rule.image.size, 0)
        # These paths formerly required the xy frame_id keyword and Device field.
        self.assertEqual(task._read_shop_slot_price(1), (None, ''))
        # Hand templates have different sizes; small cards must not trigger
        # OpenCV's size assertion or its reversed-input false matches.
        self.assertEqual(task.classify_hand_card((200, 500, 47, 64))['type'], 'unknown')

    def test_template_larger_than_roi_is_not_a_match(self):
        rng = np.random.default_rng(7)
        rule = ChessImage(roi_front=(0, 0, 20, 30), roi_back=(0, 0, 100, 100),
                          method='Template matching', threshold=0.99, file='synthetic.png')
        rule._image = rng.integers(0, 255, (30, 20, 3), dtype=np.uint8)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        for roi in ((0, 0, 10, 40), (0, 0, 40, 10), (0, 0, 10, 10), (100, 100, 0, 0)):
            with self.subTest(roi=roi):
                self.assertEqual(rule.match_all_any(frame, roi=roi), [])
        frame[40:70, 50:70] = rule._image
        matches = rule.match_all_any(frame, roi=(0, 0, 100, 100))
        self.assertTrue(any(match[1:] == (50, 40, 20, 30) for match in matches))

    def test_press_and_drag_supports_each_existing_control_backend(self):
        for method, helper in (
            ('minitouch', '_press_and_drag_minitouch'),
            ('uiautomator2', '_press_and_drag_uiautomator2'),
            ('scrcpy', '_press_and_drag_scrcpy'),
            ('ADB', None),
        ):
            with self.subTest(method=method):
                device = SimpleNamespace(
                    config=SimpleNamespace(script=SimpleNamespace(
                        device=SimpleNamespace(control_method=method))),
                    handle_control_check=Mock(), swipe_adb=Mock(),
                )
                if helper:
                    with patch('tasks.Chess.runtime.press_and_drag.' + helper) as drag:
                        Press_and_Drag(device, (100, 100), (200, 200))
                        drag.assert_called_once()
                    device.swipe_adb.assert_not_called()
                else:
                    Press_and_Drag(device, (100, 100), (200, 200))
                    device.swipe_adb.assert_called_once()

    def test_full_coin_schedules_next_monday_including_on_monday(self):
        task = self.make_task()
        task.set_next_run = Mock()
        for now, configured, expected in (
            (datetime(2026, 10, 5, 12, 34, 56), Time(hour=9), datetime(2026, 10, 12, 12, 34, 56)),
            (datetime(2026, 10, 11, 23, 0), Time(hour=6, minute=30), datetime(2026, 10, 12, 6, 30)),
        ):
            with self.subTest(now=now), patch('tasks.Chess.script_task.datetime') as clock:
                clock.now.return_value = now
                clock.combine.side_effect = datetime.combine
                task.set_next_run_next_monday('Chess', SimpleNamespace(server_update=configured))
                task.set_next_run.assert_called_with(task='Chess', server=False, target=expected)

    def test_run_count_and_coin_stop_without_starting_extra_games(self):
        for full_coin in (False, True):
            with self.subTest(full_coin=full_coin):
                task = self.make_task()
                task.config.chess.chess_config.coin_full_exit = full_coin
                task._recover_interrupted_chess_game = Mock()
                task.goto_page = Mock()
                task.screenshot = Mock()
                task._coin_is_full = Mock(return_value=full_coin)
                task.run_one_game = Mock(return_value=5)
                task.return_to_chess_lobby = Mock()
                task.set_next_run = Mock()
                task.set_next_run_next_monday = Mock()
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assertEqual(task.run_one_game.call_count, 0 if full_coin else 1)
                if full_coin:
                    task.set_next_run_next_monday.assert_called_once()
                else:
                    task.set_next_run.assert_called_once_with(task='Chess', success=True, finish=True)

    def test_round_transition_requires_confirmation_before_actions(self):
        observation = ChessObservation(frame_id=1, observed_at=0, round_no=2, mode='备')
        first = decide_round(observation, ChessRoundProgress(1), now=0,
                             confirm_frames=2, unknown_timeout=10,
                             in_game=True, grigri_visible=False)
        self.assertEqual(first.action, ChessAction.ROUND_PENDING)
        second = decide_round(observation, first.progress, now=1,
                              confirm_frames=2, unknown_timeout=10,
                              in_game=True, grigri_visible=False)
        self.assertEqual(second.action, ChessAction.NEXT_ROUND)
        self.assertEqual(second.next_round, 2)

    def test_new_frame_drops_stale_observations_and_new_game_resets_board(self):
        state = ChessGameState()
        state.observe(1, mode='备', gold=42)
        state.observe(2, mode='战')
        self.assertIsNone(state.observation.gold)
        state.board_lineup_names.add('test')
        state.begin_game()
        self.assertFalse(state.board_lineup_names)
        self.assertIsNone(state.observation)


if __name__ == '__main__':
    unittest.main()
