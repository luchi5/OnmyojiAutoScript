"""Regressions for accounts 4, 7, 15; no emulator actions."""
from pathlib import Path
from types import SimpleNamespace as S
from unittest.mock import Mock
import unittest
import numpy as np
from PIL import Image

from dev_tools.test_bondling_battle_entry import extract_methods
from dev_tools import test_gold_buff_switch as buff_tests
from tasks.Chess.runtime.lobby_recovery import ChessLobbyRecoveryMixin
from tasks.Chess.runtime.result_navigation import ChessResultNavigationMixin
from tasks.GameUi.assets import GameUiAssets
from tasks.GoldYoukai.script_task import ScriptTask as Gold
from module.exception import GameStuckError

ROOT = Path(__file__).resolve().parents[1]


class ResultHarness:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.frame = set()
        self.clock = 0
        self.clicks = []
        for key in ('I_DE_WIN','I_GOLD_WIN','I_FALSE','I_REWARD','I_CHECK_TEAM',
                    'I_CHECK_MAIN','I_CHECK_EXPLORATION','GOLD_RESULT_CONTINUE'):
            setattr(self,key,key)
        self.is_in_room = lambda: 'room' in self.frame
        def sleep(n): self.clock += n
        self.finish = extract_methods(ROOT/'tasks/GoldYoukai/script_task.py','ScriptTask',
            ('finish_gold_result',),{'time':S(monotonic=lambda:self.clock,sleep=sleep),
                                   'GameStuckError':GameStuckError})['finish_gold_result'].__get__(self)
    def screenshot(self): self.frame = next(self.frames,self.frame)
    def appear(self,marker): return marker in self.frame
    def click(self,marker): self.clicks.append(marker)


class ChessHarness(ChessLobbyRecoveryMixin, ChessResultNavigationMixin, GameUiAssets):
    def __init__(self): self.present=set()
    def appear(self, marker): return id(marker) in self.present


class RecoveryTests(unittest.TestCase):
    def test_gold_transition_miss_does_not_mean_done(self):
        h=ResultHarness([{'I_GOLD_WIN'},set(),{'I_GOLD_WIN'},set(),{'room'},{'room'}])
        h.finish()
        self.assertEqual(h.frame,{'room'})
        self.assertGreaterEqual(len(h.clicks),1)
    def test_gold_unknown_never_clicks_or_reports_success(self):
        h=ResultHarness([set()])
        with self.assertRaises(GameStuckError):h.finish()
        self.assertFalse(h.clicks)
    def test_gold_stuck_result_has_bounded_clicks(self):
        h=ResultHarness([{'I_GOLD_WIN'}])
        with self.assertRaises(GameStuckError):h.finish()
        self.assertEqual(len(h.clicks),6)
    def test_gold_requires_two_consecutive_destination_frames(self):
        h=ResultHarness([{'room'},set(),{'I_GOLD_WIN'},{'I_CHECK_TEAM'},{'I_CHECK_TEAM'}])
        h.finish();self.assertEqual(len(h.clicks),1)
    def test_chess_result_overrides_false_courtyard_match(self):
        h=ChessHarness();h.present={id(h.I_CHECK_MAIN),id(h.I_CHESS_EXIT_TO_LOBBY_2)}
        self.assertFalse(h._chess_outside_page_visible())
        h.goto_page=Mock();self.assertFalse(h._recover_chess_outside_page());h.goto_page.assert_not_called()
    def test_chess_nested_recovery_never_recurses_and_flag_resets(self):
        h=ChessHarness();h.present={id(h.I_CHECK_MAIN)};calls=[]
        def goto(page):
            calls.append(page);self.assertFalse(h._recover_chess_outside_page())
        h.goto_page=goto
        with self.assertRaises(GameStuckError):h._recover_chess_outside_page()
        self.assertEqual(len(calls),1);self.assertFalse(h._chess_outside_recovering)
    def test_chess_successful_recovery(self):
        h=ChessHarness();h.present={id(h.I_CHECK_COLLECTION)}
        h.goto_page=lambda page:h.present.clear()
        self.assertTrue(h._recover_chess_outside_page())
    def test_gold_buff_end_of_list_stops(self):
        h,_,_=buff_tests.GoldSwitchTests().harness([(['战斗胜利获得的经验增加50%'],False,143)])
        self.assertFalse(h.gold_50());self.assertLessEqual(h.device.swipe.call_count,2)
    def test_buff_shared_history_limits_next_percentage(self):
        h,_,_=buff_tests.GoldSwitchTests().harness([(['战斗胜利获得的经验增加50%'],False,143)])
        h.device.click_record=['SWIPE']*6
        self.assertFalse(h.gold_50());h.device.swipe.assert_not_called()
    def test_real_incident_screenshots(self):
        gold=np.array(Image.open(ROOT/'dev_tools/fixtures/gold_settlement_account04.png').convert('RGB'))
        self.assertTrue(Gold.I_GOLD_WIN.match(gold))
        chess=np.array(Image.open(ROOT/'dev_tools/fixtures/chess_first_account15.png').convert('RGB'))
        h=ChessHarness();self.assertTrue(h.I_CHESS_EXIT_TO_LOBBY_2.match(chess))
        h.appear=lambda marker: marker is h.I_CHECK_MAIN or marker.match(chess)
        self.assertFalse(h._chess_outside_page_visible())


if __name__=='__main__':unittest.main()
