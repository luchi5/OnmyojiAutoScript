"""Exercise Chess navigation and login recovery without a Config or Device."""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.atom.click import RuleClick
from tasks.Chess.assets import ChessAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.page import (
    PageRegistry,
    page_chess,
    page_chess_battle,
    page_chess_result,
    page_soul_zones,
)
from tasks.GameUi.session import NavigatorSession
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Restart.assets import RestartAssets
from tasks.Restart.login import LoginHandler


@dataclass
class Screen:
    visible: tuple
    next_click: object = None


class ScriptedScreens:
    """Only a specified click advances a frame; all I/O stays in memory."""

    def setup_screens(self, screens):
        self.screens = screens
        self.screen_index = 0
        self.screenshot_count = 0
        self.clicked = []
        self.device = SimpleNamespace(
            screenshot=self.screenshot,
            check_screen_size_sample=lambda: True,
            stuck_record_add=Mock(),
            stuck_record_clear=Mock(),
            click_record_clear=Mock(),
            get_orientation=Mock(),
            app_stop=Mock(side_effect=AssertionError('Unexpected app restart')),
            app_start=Mock(side_effect=AssertionError('Unexpected app restart')),
        )

    def screenshot(self):
        self.screenshot_count += 1
        if self.screenshot_count > 100:
            raise AssertionError('Navigation did not finish the scripted screens')

    def maybe_screenshot(self, skip_first_screenshot=True):
        if not skip_first_screenshot:
            self.screenshot()

    def prepare_appear_cache(self, targets):
        pass

    def appear(self, marker, **kwargs):
        return any(marker is visible for visible in self.screens[self.screen_index].visible)

    def ocr_appear(self, marker, **kwargs):
        return False

    def appear_then_click(self, marker, **kwargs):
        return self.click(marker, **kwargs) if self.appear(marker) else False

    def click(self, marker, **kwargs):
        expected = self.screens[self.screen_index].next_click
        matches = isinstance(marker, RuleClick) if expected is RuleClick else marker is expected
        if expected is None or not matches:
            raise AssertionError(f'Unexpected click on screen {self.screen_index}: {marker}')
        self.clicked.append(marker)
        self.screen_index += 1
        if self.screen_index >= len(self.screens):
            raise AssertionError('Clicked past the final screen')
        return True


class ScriptedNavigation(ScriptedScreens, GameUi):
    def __init__(self, screens, category='Orochi'):
        # Skip BaseTask.__init__ and never construct Config/Device.
        self.setup_screens(screens)
        self.navigator = NavigatorSession(task_category=category)
        self.navigator.bootstrap(PageRegistry.all())


class ScriptedLogin(ScriptedScreens, LoginHandler):
    def __init__(self, screens):
        self.setup_screens(screens)
        self.config = SimpleNamespace(
            restart=SimpleNamespace(harvest_config=SimpleNamespace(enable=True)),
        )
        self.harvest = Mock()

    def _burst(self):
        pass


def route_from_lobby_to_souls():
    return [
        Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START, GlobalGameAssets.I_UI_BACK_YELLOW),
               GlobalGameAssets.I_UI_BACK_YELLOW),
        Screen((GameUiAssets.I_CHECK_ENTERTAINMENT, GlobalGameAssets.I_UI_BACK_YELLOW),
               GlobalGameAssets.I_UI_BACK_YELLOW),
        Screen((GameUiAssets.I_CHECK_TOWN, GameUiAssets.I_TOWN_GOTO_MAIN),
               GameUiAssets.I_TOWN_GOTO_MAIN),
        Screen((GameUiAssets.I_CHECK_MAIN, GameUiAssets.I_MAIN_GOTO_EXPLORATION),
               GameUiAssets.I_MAIN_GOTO_EXPLORATION),
        Screen((GameUiAssets.I_CHECK_EXPLORATION, GameUiAssets.I_EXPLORATION_GOTO_SOUL_ZONE),
               GameUiAssets.I_EXPLORATION_GOTO_SOUL_ZONE),
        Screen((GameUiAssets.I_CHECK_SOUL_ZONES,)),
    ]


def result_screens():
    return [
        Screen((GameUiAssets.I_CHESS_EXIT_TO_LOBBY,), GameUiAssets.I_CHESS_EXIT_TO_LOBBY),
        Screen((ChessAssets.I_SHARE,), RuleClick),
        Screen((GameUiAssets.I_CHECK_CHESS_RANK, GameUiAssets.I_CHESS_RANK_GOTO_LOBBY),
               GameUiAssets.I_CHESS_RANK_GOTO_LOBBY),
    ]


class ChessNavigationTests(unittest.TestCase):
    def setUp(self):
        sleep_patch = patch('tasks.GameUi.chess_battle.time.sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_main_can_navigate_to_chess_through_town_and_entertainment(self):
        task = ScriptedNavigation([
            Screen((GameUiAssets.I_CHECK_MAIN, GameUiAssets.I_MAIN_GOTO_TOWN),
                   GameUiAssets.I_MAIN_GOTO_TOWN),
            Screen((GameUiAssets.I_CHECK_TOWN, GameUiAssets.I_TOWN_GOTO_ENTERTAINMENT),
                   GameUiAssets.I_TOWN_GOTO_ENTERTAINMENT),
            Screen((GameUiAssets.I_CHECK_ENTERTAINMENT, GameUiAssets.I_ENTERTAINMENT_GOTO_CHESS),
                   GameUiAssets.I_ENTERTAINMENT_GOTO_CHESS),
            Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START)),
        ], category='Chess')
        self.assertTrue(task.goto_page(page_chess))
        self.assertEqual(task.navigator.current_page, page_chess)
        self.assertEqual(len(task.clicked), 3)

    def test_interrupted_battle_exits_and_continues_to_another_task(self):
        task = ScriptedNavigation([
            Screen((GameUiAssets.I_CHECK_CHESS_BATTLE, GameUiAssets.I_CHESS_EXIT),
                   GameUiAssets.I_CHESS_EXIT),
            Screen((GameUiAssets.I_CHESS_EXIT_CONFIRM, GameUiAssets.I_CHESS_EXIT_CANCEL),
                   GameUiAssets.I_CHESS_EXIT_CONFIRM),
            *result_screens(),
            *route_from_lobby_to_souls(),
        ])
        self.assertEqual(task.get_current_page(), page_chess_battle)
        self.assertTrue(task.goto_page(page_soul_zones))
        self.assertEqual(task.navigator.current_page, page_soul_zones)
        self.assertIs(task.clicked[0], GameUiAssets.I_CHESS_EXIT)
        self.assertIs(task.clicked[1], GameUiAssets.I_CHESS_EXIT_CONFIRM)
        self.assertEqual(task.screen_index, len(task.screens) - 1)

    def test_unfinished_result_returns_to_lobby_then_another_task(self):
        task = ScriptedNavigation([*result_screens(), *route_from_lobby_to_souls()])
        self.assertEqual(task.get_current_page(), page_chess_result)
        self.assertTrue(task.goto_page(page_soul_zones))
        self.assertEqual(task.navigator.current_page, page_soul_zones)
        self.assertNotIn(GameUiAssets.I_CHESS_EXIT_CONFIRM, task.clicked)

    def test_result_overlay_takes_priority_over_lobby_background(self):
        for overlay in (ChessAssets.I_SHARE, ChessAssets.I_REWARD_CHESS,
                        GameUiAssets.I_CHECK_CHESS_RANK):
            with self.subTest(overlay=overlay.name):
                task = ScriptedNavigation([Screen((GameUiAssets.I_CHECK_CHESS, overlay))])
                self.assertEqual(task.get_current_page(), page_chess_result)
                self.assertEqual(task.clicked, [])

    def test_normal_navigation_does_not_trigger_chess_recovery(self):
        task = ScriptedNavigation(route_from_lobby_to_souls()[3:])
        with patch.object(task, 'exit_chess_battle') as exit_battle, \
                patch.object(task, 'return_to_chess_lobby') as return_lobby:
            self.assertTrue(task.goto_page(page_soul_zones))
        exit_battle.assert_not_called()
        return_lobby.assert_not_called()
        self.assertFalse(task.chess_result_flow_visible())
        self.assertEqual(len(task.clicked), 2)


class ChessLoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        timer_patch = patch('tasks.Restart.login.Timer')
        timer = timer_patch.start()
        timer.return_value.reached.return_value = False
        timer.return_value.start.return_value.reached.return_value = True
        self.addCleanup(timer_patch.stop)
        sleep_patch = patch('tasks.GameUi.chess_battle.time.sleep')
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_cancel_rejoin_finishes_result_and_skips_courtyard_harvest(self):
        task = ScriptedLogin([
            Screen((RestartAssets.I_RETURN_CHESS_CANCEL,), RestartAssets.I_RETURN_CHESS_CANCEL),
            *result_screens(),
            Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START)),
        ])
        self.assertTrue(task.app_handle_login())
        self.assertTrue(task._login_recovered_chess)
        task.harvest.assert_not_called()
        self.assertIs(task.clicked[0], RestartAssets.I_RETURN_CHESS_CANCEL)
        self.assertEqual(task.screen_index, len(task.screens) - 1)

    def test_lobby_login_does_not_run_result_clicks_or_harvest(self):
        task = ScriptedLogin([Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START))])
        with patch.object(task, 'return_to_chess_lobby') as return_lobby:
            self.assertTrue(task.app_handle_login())
        return_lobby.assert_not_called()
        task.harvest.assert_not_called()
        self.assertEqual(task.clicked, [])

    def test_login_finishes_share_overlay_before_accepting_lobby_background(self):
        task = ScriptedLogin([
            Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_SHARE), RuleClick),
            Screen((GameUiAssets.I_CHECK_CHESS_RANK, GameUiAssets.I_CHESS_RANK_GOTO_LOBBY),
                   GameUiAssets.I_CHESS_RANK_GOTO_LOBBY),
            Screen((GameUiAssets.I_CHECK_CHESS, ChessAssets.I_CHESS_START)),
        ])
        self.assertTrue(task.app_handle_login())
        self.assertEqual(task.screen_index, 2)
        self.assertTrue(task._login_recovered_chess)
        task.harvest.assert_not_called()

    def test_normal_login_clears_previous_chess_flag_and_keeps_harvest(self):
        task = ScriptedLogin([
            Screen((LoginHandler.I_BUFF_1, GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS)),
        ])
        task._login_recovered_chess = True
        with patch.object(task, 'return_to_chess_lobby') as return_lobby:
            self.assertTrue(task.app_handle_login())
        return_lobby.assert_not_called()
        self.assertFalse(task._login_recovered_chess)
        task.harvest.assert_called_once_with()
        self.assertEqual(task.clicked, [])


if __name__ == '__main__':
    unittest.main()
