"""Offline GoldYoukai wait regressions using the actual Device guard methods."""
from collections import deque
from pathlib import Path
import unittest

from dev_tools.test_bondling_battle_entry import (
    FakeClock, FakeLogger, FakeTimer, extract_methods,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/GoldYoukai/script_task.py'
DEVICE = ROOT / 'module/device/device.py'


class GameStuckError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class GameTooManyClickError(Exception):
    pass


class RequestHumanTakeover(Exception):
    pass


class AccountLoggedInElsewhere(RequestHumanTakeover):
    pass


class DeviceGuard:
    def __init__(self, clock, logger):
        self.clock = clock
        self.detect_record = set()
        self.click_record = deque(maxlen=15)
        self.stuck_timer = FakeTimer(clock, 60, count=60).start()
        self.stuck_timer_long = FakeTimer(clock, 300, count=300).start()
        self.stuck_long_wait_list = ['BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK', 'PREPARE_BEFORE_BATTLE']
        self.app_running = True
        self.image = None
        self.additions, self.clear_calls = [], []
        namespace = {
            'logger': logger, 'GameStuckError': GameStuckError,
            'GameNotRunningError': GameNotRunningError,
            'GameTooManyClickError': GameTooManyClickError,
        }
        names = ('stuck_record_add', 'stuck_record_clear', 'stuck_record_check',
                 'handle_control_check', 'click_record_add', 'click_record_clear',
                 'click_record_check')
        methods = extract_methods(DEVICE, 'Device', names, namespace)
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))
        actual_add = self.stuck_record_add
        actual_clear = self.stuck_record_clear

        def record_add(marker):
            self.additions.append((self.clock.now, marker))
            return actual_add(marker)

        def record_clear():
            self.clear_calls.append(self.clock.now)
            return actual_clear()

        self.stuck_record_add = record_add
        self.stuck_record_clear = record_clear

    def app_is_running(self):
        return self.app_running


class BattleHarness:
    def __init__(self, frames):
        self.frames, self.frame = frames, {}
        self.clock = FakeClock(step=1)
        self.logger = FakeLogger()
        self.device = DeviceGuard(self.clock, self.logger)
        self.screenshots, self.clicks, self.result_clicks = 0, [], []
        self.after_prepare_records = []
        self.I_PREPARE_HIGHLIGHT = 'prepare'
        self.I_DE_WIN, self.I_GOLD_WIN, self.I_FALSE = 'de_win', 'gold_win', 'false'
        method = extract_methods(SOURCE, 'ScriptTask', ('battle_wait',), {
            'logger': self.logger,
        })['battle_wait']
        self.battle_wait = method.__get__(self)

    def screenshot(self):
        self.clock.advance()
        if self.screenshots > 1000:
            raise AssertionError('Long wait guard has been disabled')
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        # Preserve Device.screenshot ordering: owner-login protection precedes
        # the generic timeout check, including on a timeout boundary.
        if isinstance(self.frame, Exception):
            raise self.frame
        if self.after_prepare_records:
            self.after_prepare_records[-1].append(set(self.device.detect_record))
        self.device.stuck_record_check()

    def appear_then_click(self, marker, interval=None):
        if not self.frame.get(marker) or self.frame.get('throttled'):
            return False
        self.device.handle_control_check(marker)
        self.clicks.append((self.clock.now, marker, interval))
        self.after_prepare_records.append([set(self.device.detect_record)])
        return True

    def appear(self, marker):
        return self.frame.get(marker, False)

    def ui_click_until_disappear(self, marker):
        self.result_clicks.append(marker)
        self.device.handle_control_check(marker)


class GoldBattleWaitTests(unittest.TestCase):
    def test_prepare_click_clears_marker_then_task_restores_it(self):
        harness = BattleHarness([{'prepare': True}] + [{}] * 82 + [{'de_win': True}])
        self.assertTrue(harness.battle_wait(False))
        self.assertEqual(harness.after_prepare_records[0][0], set())
        self.assertEqual(harness.after_prepare_records[0][1], {'BATTLE_STATUS_S'})
        self.assertEqual(len(harness.device.additions), 2)
        self.assertEqual(harness.clicks[0][2], 1)
        self.assertGreater(harness.screenshots, 60)
        # Only the actual prepare and result clicks reset the timers.
        self.assertEqual(len(harness.device.clear_calls), 2)

    def test_long_battle_without_late_prepare_still_waits(self):
        harness = BattleHarness([{}] * 104 + [{'gold_win': True}])
        self.assertTrue(harness.battle_wait(True))
        self.assertEqual(len(harness.device.additions), 1)
        self.assertEqual(harness.result_clicks, ['gold_win'])

    def test_real_no_progress_beyond_300_seconds_still_raises(self):
        harness = BattleHarness([{'prepare': True}, {}])
        with self.assertRaises(GameStuckError):
            harness.battle_wait(False)
        self.assertGreaterEqual(harness.clock.now - harness.clicks[0][0], 300)
        self.assertLess(harness.clock.now - harness.clicks[0][0], 303)
        self.assertEqual(len(harness.device.additions), 2)
        # One prepare reset, then the timeout handler's existing reset.
        self.assertEqual(len(harness.device.clear_calls), 2)

    def test_throttled_prepare_does_not_restore_or_reset_each_frame(self):
        harness = BattleHarness([{'prepare': True, 'throttled': True}] * 83 + [{'gold_win': True}])
        self.assertTrue(harness.battle_wait(False))
        self.assertFalse(harness.clicks)
        self.assertEqual(len(harness.device.additions), 1)

    def test_standard_gold_win_exits_successfully(self):
        harness = BattleHarness([{'gold_win': True}])
        self.assertTrue(harness.battle_wait(False))
        self.assertEqual(harness.result_clicks, ['gold_win'])

    def test_demon_style_win_exits_successfully(self):
        harness = BattleHarness([{'de_win': True}])
        self.assertTrue(harness.battle_wait(False))
        self.assertEqual(harness.result_clicks, ['de_win'])

    def test_loss_exits_as_failure(self):
        harness = BattleHarness([{'false': True}])
        self.assertFalse(harness.battle_wait(False))
        self.assertEqual(harness.result_clicks, ['false'])

    def test_owner_login_is_never_swallowed(self):
        harness = BattleHarness([{}, AccountLoggedInElsewhere('Owner online')])
        with self.assertRaises(AccountLoggedInElsewhere):
            harness.battle_wait(False)

    def test_owner_login_on_timeout_boundary_takes_priority(self):
        harness = BattleHarness([{}] * 300 + [AccountLoggedInElsewhere('Owner online')])
        with self.assertRaises(AccountLoggedInElsewhere):
            harness.battle_wait(False)
        self.assertFalse(harness.device.clear_calls)

    def test_other_human_takeover_is_never_swallowed(self):
        harness = BattleHarness([RequestHumanTakeover('Special stop')])
        with self.assertRaises(RequestHumanTakeover):
            harness.battle_wait(False)

    def test_game_closed_remains_a_distinct_recovery_error(self):
        harness = BattleHarness([{}])
        harness.device.app_running = False
        with self.assertRaises(GameNotRunningError):
            harness.battle_wait(False)


if __name__ == '__main__':
    unittest.main()
