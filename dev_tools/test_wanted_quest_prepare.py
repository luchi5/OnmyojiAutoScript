"""Wanted quest preparation regression checks without a game or backend.

Compile the real task and general battle methods from AST, preserving the
actual handoff to battle_wait while deterministic frames emulate preparation.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import (
    FakeClock, FakeLogger, FakeTimer, extract_methods,
)


ROOT = Path(__file__).resolve().parents[1]
WANTED_SOURCE = ROOT / 'tasks/WantedQuests/explore.py'
GENERAL_SOURCE = ROOT / 'tasks/Component/GeneralBattle/general_battle.py'
MARKERS = (
    'I_DISABLE_7DAYS_DIFF_SOUL', 'I_CONFIRM_CLOSE_DIFF_SOUL',
    'I_PREPARE_HIGHLIGHT', 'I_PREPARE_DARK', 'I_PRESET',
    'I_BATTLE_INFO', 'I_WIN', 'I_DE_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD',
)


class GameStuckError(Exception):
    pass


class Harness:
    def __init__(self, frames, lock=True):
        self.frames = frames
        self.frame = set()
        self.clock = FakeClock(0.5)
        self.screenshots = 0
        self.clicks = []
        self.last_click = {}
        self.wait_calls = []
        self.green_calls = []
        self.current_count = 0
        self.delegate_calls = []
        self.logger = FakeLogger()
        self.battle_config = SimpleNamespace(
            lock_team_enable=lock, preset_enable=True, preset_group=2,
            preset_team=3, green_enable=True, green_mark='left1',
            random_click_swipt_enable=False,
        )
        for name in MARKERS:
            setattr(self, name, name)

        def delegate(task, buff, config, timeout):
            task.delegate_calls.append((buff, config, timeout))
            return True

        namespace = {
            'Timer': lambda limit, count=0: FakeTimer(self.clock, limit, count),
            'sleep': lambda seconds: setattr(self.clock, 'now', self.clock.now + seconds),
            'GameStuckError': GameStuckError, 'logger': self.logger,
            'GeneralBattleConfig': lambda: self.battle_config,
            'BuffClass': object,
            'GeneralBattle': SimpleNamespace(battle_before=delegate),
        }
        method = extract_methods(WANTED_SOURCE, 'WQExplore', ('battle_before',), namespace)
        self.battle_before = method['battle_before'].__get__(self)
        method = extract_methods(GENERAL_SOURCE, 'GeneralBattle', ('run_general_battle',), namespace)
        self.run_general_battle = method['run_general_battle'].__get__(self)

    def screenshot(self):
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.clock.advance()
        if self.screenshots > 120:
            raise AssertionError('Preparation loop is unbounded')

    def appear(self, marker, **kwargs):
        return marker in self.frame

    def appear_then_click(self, marker, interval=0, **kwargs):
        if not self.appear(marker):
            return False
        if self.clock.now - self.last_click.get(marker, -float('inf')) < interval:
            return False
        self.last_click[marker] = self.clock.now
        self.clicks.append((marker, interval, self.screenshots))
        return True

    def is_in_prepare(self, screenshot=True):
        if screenshot:
            self.screenshot()
        return any(self.appear(name) for name in (
            'I_PREPARE_HIGHLIGHT', 'I_PREPARE_DARK', 'I_PRESET'))

    def is_in_real_battle(self, screenshot=True):
        if screenshot:
            self.screenshot()
        return self.appear('I_BATTLE_INFO')

    def is_in_battle(self, screenshot=True):
        return self.is_in_real_battle(screenshot)

    def green_mark(self, *args):
        self.green_calls.append(args)

    def battle_wait(self, **kwargs):
        self.wait_calls.append((kwargs, self.screenshots, self.frame))
        return True

    def prepare_clicks(self):
        return [item for item in self.clicks if item[0] == 'I_PREPARE_HIGHLIGHT']


PREPARE = {'I_PREPARE_HIGHLIGHT', 'I_PRESET'}
BATTLE = {'I_BATTLE_INFO'}


class WantedPreparationTests(unittest.TestCase):
    def test_locked_lineup_still_clicks_prepare(self):
        h = Harness([PREPARE, BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(h.prepare_clicks(), [('I_PREPARE_HIGHLIGHT', 0.8, 1)])
        self.assertEqual(h.wait_calls[0][2], BATTLE)
        self.assertTrue(h.battle_config.lock_team_enable)
        self.assertTrue(h.battle_config.preset_enable)
        self.assertEqual(h.delegate_calls, [])

    def test_swallowed_prepare_click_retries_until_battle(self):
        h = Harness([PREPARE] * 5 + [BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertGreaterEqual(len(h.prepare_clicks()), 2)
        self.assertEqual(h.wait_calls[0][2], BATTLE)
        self.assertTrue(all(click[1] == 0.8 for click in h.prepare_clicks()))

    def test_preparation_has_time_for_slow_scene_transition(self):
        h = Harness([set()] * 8 + [PREPARE, BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(len(h.prepare_clicks()), 1)
        self.assertEqual(h.wait_calls[0][2], BATTLE)

    def test_dark_prepare_does_not_trigger_a_blind_click(self):
        h = Harness([{'I_PREPARE_DARK'}] * 3 + [PREPARE, BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(h.prepare_clicks()[0][2], 4)

    def test_prompt_dismissal_precedes_prepare(self):
        h = Harness([
            {'I_DISABLE_7DAYS_DIFF_SOUL'}, {'I_CONFIRM_CLOSE_DIFF_SOUL'},
            PREPARE, BATTLE,
        ])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual([c[0] for c in h.clicks], [
            'I_DISABLE_7DAYS_DIFF_SOUL', 'I_CONFIRM_CLOSE_DIFF_SOUL',
            'I_PREPARE_HIGHLIGHT',
        ])

    def test_already_in_battle_does_not_click_prepare(self):
        h = Harness([BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(h.clicks, [])
        self.assertEqual(len(h.wait_calls), 1)

    def test_fast_settlement_is_a_valid_entry(self):
        for marker in ('I_WIN', 'I_DE_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD'):
            with self.subTest(marker=marker):
                h = Harness([{marker}])
                self.assertTrue(h.run_general_battle(h.battle_config))
                self.assertEqual(h.clicks, [])
                self.assertEqual(len(h.wait_calls), 1)

    def test_battle_marker_does_not_skip_a_visible_prepare_stage(self):
        h = Harness([PREPARE | BATTLE, BATTLE])
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(len(h.prepare_clicks()), 1)
        self.assertEqual(h.wait_calls[0][1], 2)

    def test_preparation_timeout_never_enters_result_wait(self):
        h = Harness([{'I_PREPARE_DARK'}])
        with self.assertRaisesRegex(GameStuckError, 'preparation stage'):
            h.run_general_battle(h.battle_config)
        self.assertEqual(h.wait_calls, [])
        self.assertEqual(h.clicks, [])
        self.assertLess(h.screenshots, 40)

    def test_unlocked_secret_battle_preserves_general_settings(self):
        h = Harness([set()], lock=False)
        buff = object()
        self.assertTrue(h.battle_before(buff, h.battle_config, timeout=12))
        self.assertEqual(h.delegate_calls, [(buff, h.battle_config, 12)])
        self.assertEqual(h.screenshots, 0)


if __name__ == '__main__':
    unittest.main()
