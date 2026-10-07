"""Area Boss preparation regressions without importing tasks or starting devices.

Compile the real task-local methods and the unchanged general battle entry from
AST. Deterministic UI frames exercise the handoff from presets to preparation.
"""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeLogger, extract_methods


ROOT = Path(__file__).resolve().parents[1]
TASK_SOURCE = ROOT / 'tasks/AreaBoss/script_task.py'
GENERAL_SOURCE = ROOT / 'tasks/Component/GeneralBattle/general_battle.py'
MARKERS = (
    'I_PREPARE_HIGHLIGHT', 'I_PREPARE_DARK', 'I_PRESET', 'I_PRESET_WIT_NUMBER',
    'I_PRESET_ENSURE', 'I_BUFF', 'I_BATTLE_INFO', 'I_WIN', 'I_DE_WIN', 'I_FALSE',
    'I_REWARD', 'I_REWARD_GOLD', 'I_DISABLE_7DAYS_DIFF_SOUL',
    'I_CONFIRM_CLOSE_DIFF_SOUL', 'I_EXIT', 'I_EXIT_ENSURE', 'I_AB_CLOSE_RED',
    'I_FILTER', 'I_CHECK_MAIN', 'I_UI_BACK_YELLOW', 'I_FIRE',
)


class TaskEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class GameTooManyClickError(Exception):
    pass


class RequestHumanTakeover(Exception):
    pass


def prepare(**extra):
    return {'visible': {'I_PREPARE_HIGHLIGHT', 'I_PRESET'}, **extra}


def battle(**extra):
    return {'visible': {'I_BATTLE_INFO'}, **extra}


class Harness:
    def __init__(self, frames, *, preset=True, count=1, real_defer=False,
                 config_seconds=0, step=1):
        self.frames = frames
        self.frame = frames[0]
        self.clock = FakeClock(step)
        self.screenshots = 0
        self.clicks = []
        self.interval_last = {}
        self.preset_calls = []
        self.buff_calls = []
        self.green_calls = []
        self.wait_calls = []
        self.delays = []
        self.navigation = []
        self.deferred = 0
        self.config_seconds = config_seconds
        self.config_finished_frame = None
        self.current_count = count
        self.exception = None
        self.device = SimpleNamespace(image=None)
        self.logger = FakeLogger()
        self.config = SimpleNamespace(config_name='offline-account')
        self.battle_config = SimpleNamespace(
            lock_team_enable=False, preset_enable=preset, preset_group=2,
            preset_team=3, green_enable=True, green_mark='left1',
            random_click_swipt_enable=False,
        )
        for name in MARKERS:
            setattr(self, name, name)
        namespace = {
            'time': SimpleNamespace(monotonic=lambda: self.clock.now, sleep=self.sleep),
            'datetime': datetime, 'timedelta': timedelta, 'logger': self.logger,
            'TaskEnd': TaskEnd, 'GameStuckError': GameStuckError,
            'GamePageUnknownError': GamePageUnknownError,
            'GameTooManyClickError': GameTooManyClickError, 'page_main': 'courtyard',
            'GeneralBattleConfig': lambda: self.battle_config, 'BuffClass': object,
        }
        methods = ('battle_before', '_area_boss_battle_started', '_defer_battle_preparation')
        for name, method in extract_methods(TASK_SOURCE, 'ScriptTask', methods, namespace).items():
            setattr(self, name, method.__get__(self))
        method = extract_methods(GENERAL_SOURCE, 'GeneralBattle', ('run_general_battle',), namespace)
        self.run_general_battle = method['run_general_battle'].__get__(self)
        if not real_defer:
            self._defer_battle_preparation = self.mock_defer

    def sleep(self, seconds):
        self.clock.now += seconds

    def screenshot(self):
        if self.exception:
            raise self.exception
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.clock.advance()
        if self.screenshots > 180:
            raise AssertionError('Preparation or exit loop is unbounded')
        if self.frame.get('exception'):
            raise self.frame['exception']

    def appear(self, marker, **kwargs):
        return marker in self.frame.get('visible', set())

    def click(self, marker, interval=0, **kwargs):
        if self.clock.now - self.interval_last.get(marker, -float('inf')) < interval:
            return False
        self.interval_last[marker] = self.clock.now
        self.clicks.append((marker, interval, self.screenshots, self.clock.now))
        return True

    def appear_then_click(self, marker, interval=0, **kwargs):
        return self.appear(marker) and self.click(marker, interval, **kwargs)

    def is_in_prepare(self, is_screenshot=True):
        if is_screenshot:
            self.screenshot()
        return any(self.appear(marker) for marker in (
            'I_PREPARE_HIGHLIGHT', 'I_PREPARE_DARK', 'I_PRESET',
            'I_PRESET_WIT_NUMBER', 'I_BUFF',
        ))

    def is_in_real_battle(self, is_screenshot=True):
        if is_screenshot:
            self.screenshot()
        return self.appear('I_BATTLE_INFO')

    def is_in_battle(self, is_screenshot=True):
        if is_screenshot:
            self.screenshot()
        return self.is_in_real_battle(False)

    def switch_preset_team(self, enable, group, team):
        self.preset_calls.append((enable, group, team))
        self.clock.now += self.config_seconds
        self.config_finished_frame = self.screenshots

    def check_and_open_buff(self, buff):
        self.buff_calls.append(buff)
        self.config_finished_frame = self.screenshots

    def green_mark(self, enable, mark):
        self.green_calls.append((enable, mark, self.current_count))

    def battle_wait(self, **kwargs):
        self.wait_calls.append((kwargs, self.current_count, self.screenshots))
        return True

    def set_next_run(self, **kwargs):
        self.delays.append(kwargs)

    def goto_page(self, page):
        self.navigation.append(page)

    def mock_defer(self):
        self.deferred += 1
        raise TaskEnd('Area Boss preparation pending')

    def prepare_clicks(self):
        return [item for item in self.clicks if item[0] == 'I_PREPARE_HIGHLIGHT']


class PreparationTests(unittest.TestCase):
    def test_slow_preset_leaves_a_fresh_prepare_budget_and_frame(self):
        h = Harness([prepare(), prepare(), prepare(), battle(), battle()], config_seconds=40)
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual(h.preset_calls, [(True, 2, 3)])
        self.assertEqual(h.buff_calls, [None])
        self.assertGreater(h.prepare_clicks()[0][2], h.config_finished_frame)
        self.assertEqual(len(h.prepare_clicks()), 2)
        self.assertEqual(h.deferred, 0)

    def test_first_prepare_click_swallowed_by_animation_is_retried(self):
        h = Harness([prepare(), prepare(), prepare(), battle(), battle()])
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual(len(h.prepare_clicks()), 2)
        self.assertTrue(all(item[1] == 0.8 for item in h.prepare_clicks()))

    def test_two_battle_frames_are_required(self):
        h = Harness([battle(), {'visible': set()}, battle(), battle()])
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual(h.screenshots, 4)
        self.assertEqual(h.clicks, [])

    def test_requested_legacy_five_seconds_keeps_slow_loading_budget(self):
        h = Harness([{'visible': set()}] * 9 + [prepare(), battle(), battle()], count=2)
        self.assertTrue(h.battle_before(None, h.battle_config, timeout=5))
        self.assertEqual(len(h.prepare_clicks()), 1)
        self.assertGreater(h.clock.now, 105)

    def test_prepare_marker_overrides_same_frame_battle_marker(self):
        h = Harness([prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_BATTLE_INFO'}), battle(), battle()], count=2)
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual(len(h.prepare_clicks()), 1)
        self.assertEqual(h.screenshots, 3)

    def test_second_battle_does_not_repeat_preset_or_buff(self):
        h = Harness([prepare(), battle(), battle()], count=2)
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual(h.preset_calls, [])
        self.assertEqual(h.buff_calls, [])

    def test_unknown_page_defers_with_bounded_time(self):
        h = Harness([{'visible': set()}])
        with self.assertRaises(TaskEnd):
            h.battle_before(None, h.battle_config)
        self.assertEqual(h.deferred, 1)
        self.assertEqual(h.clicks, [])
        self.assertLessEqual(h.clock.now, 132)

    def test_unchanged_prepare_is_not_clicked_forever(self):
        h = Harness([prepare()])
        with self.assertRaises(TaskEnd):
            h.battle_before(None, h.battle_config)
        self.assertEqual(len(h.prepare_clicks()), 6)
        self.assertEqual(h.deferred, 1)
        self.assertLessEqual(h.clock.now, 135)

    def test_prepare_with_only_dark_button_is_not_blindly_clicked(self):
        h = Harness([{'visible': {'I_PREPARE_DARK'}}], count=2)
        with self.assertRaises(TaskEnd):
            h.battle_before(None, h.battle_config)
        self.assertEqual(h.prepare_clicks(), [])

    def test_owner_takeover_exception_propagates(self):
        h = Harness([prepare()])
        h.exception = RequestHumanTakeover('owner logged in')
        with self.assertRaises(RequestHumanTakeover):
            h.battle_before(None, h.battle_config)
        self.assertEqual(h.deferred, 0)
        self.assertEqual(h.clicks, [])

    def test_different_soul_confirmation_is_handled_before_prepare(self):
        h = Harness([
            {'visible': {'I_DISABLE_7DAYS_DIFF_SOUL'}},
            {'visible': {'I_CONFIRM_CLOSE_DIFF_SOUL'}}, prepare(), battle(), battle(),
        ], count=2)
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertEqual([item[0] for item in h.clicks], [
            'I_DISABLE_7DAYS_DIFF_SOUL', 'I_CONFIRM_CLOSE_DIFF_SOUL', 'I_PREPARE_HIGHLIGHT',
        ])

    def test_short_fight_clear_settlement_markers_are_accepted(self):
        for marker in ('I_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD'):
            with self.subTest(marker=marker):
                h = Harness([{'visible': {marker}}, {'visible': {marker}}])
                self.assertTrue(h.battle_before(None, h.battle_config))
                self.assertEqual(h.screenshots, 2)
                self.assertEqual(h.clicks, [])


class GeneralEntryIntegrationTests(unittest.TestCase):
    def test_normal_entry_keeps_original_count_green_and_wait(self):
        h = Harness([prepare(), prepare(), battle(), battle(), battle()], count=0)
        self.assertTrue(h.run_general_battle(h.battle_config))
        self.assertEqual(h.current_count, 1)
        self.assertEqual(h.green_calls, [(True, 'left1', 1)])
        self.assertEqual(len(h.wait_calls), 1)
        self.assertEqual(h.wait_calls[0][:2], ({'random_click_swipt_enable': False}, 1))
        self.assertGreaterEqual(h.wait_calls[0][2], 4)
        self.assertEqual(h.preset_calls, [(True, 2, 3)])

    def test_unconfirmed_prepare_never_enters_general_battle_wait(self):
        h = Harness([prepare()], count=0)
        with self.assertRaises(TaskEnd):
            h.run_general_battle(h.battle_config)
        self.assertEqual(h.current_count, 1)
        self.assertEqual(h.wait_calls, [])
        self.assertEqual(h.green_calls, [])
        self.assertEqual(h.deferred, 1)


class DeferredPreparationTests(unittest.TestCase):
    def test_unknown_exit_is_bounded_and_preserves_pending_task(self):
        h = Harness([{'visible': set()}], real_defer=True)
        before = datetime.now()
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        after = datetime.now()
        self.assertEqual(h.clicks, [])
        self.assertEqual(len(h.delays), 1)
        delay = h.delays[0]
        self.assertEqual(delay['task'], 'AreaBoss')
        self.assertIsNone(delay['success'])
        self.assertFalse(delay['server'])
        self.assertGreaterEqual(delay['target'], before + timedelta(minutes=30))
        self.assertLessEqual(delay['target'], after + timedelta(minutes=30))
        self.assertLessEqual(h.clock.now, 114)

    def test_leaves_prepare_confirms_exit_and_closes_detail(self):
        h = Harness([
            prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_EXIT'}),
            {'visible': {'I_EXIT_ENSURE'}},
            {'visible': {'I_FALSE'}},
            {'visible': {'I_AB_CLOSE_RED', 'I_FIRE'}},
            {'visible': {'I_FILTER'}},
        ], real_defer=True)
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        self.assertEqual([item[0] for item in h.clicks], [
            'I_EXIT', 'I_EXIT_ENSURE', 'I_FALSE', 'I_AB_CLOSE_RED',
        ])
        self.assertEqual(len(h.delays), 1)
        self.assertEqual(h.wait_calls, [])

    def test_stuck_exit_buttons_have_bounded_click_counts(self):
        for marker, frame in (
            ('I_EXIT', prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_EXIT'})),
            ('I_AB_CLOSE_RED', {'visible': {'I_AB_CLOSE_RED', 'I_FIRE'}}),
        ):
            with self.subTest(marker=marker):
                h = Harness([frame], real_defer=True)
                with self.assertRaises(TaskEnd):
                    h._defer_battle_preparation()
                self.assertEqual(len([item for item in h.clicks if item[0] == marker]), 3)
                self.assertLessEqual(h.clock.now, 114)

    def test_late_real_battle_is_accepted_without_exit_or_delay(self):
        h = Harness([battle(), battle()], real_defer=True)
        self.assertTrue(h._defer_battle_preparation())
        self.assertEqual(h.screenshots, 2)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.delays, [])

    def test_first_battle_frame_at_deadline_gets_one_fresh_confirmation(self):
        # The first screenshot consumes the complete twelve-second budget;
        # the second frame must be the bounded grace confirmation, not a
        # normal loop iteration or immediate unconfirmed acceptance.
        h = Harness([battle(), battle()], real_defer=True, step=12)
        self.assertTrue(h._defer_battle_preparation())
        self.assertEqual(h.screenshots, 2)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.delays, [])

    def test_battle_disappearing_in_deadline_confirmation_still_defers(self):
        h = Harness([battle(), prepare()], real_defer=True, step=12)
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        self.assertEqual(h.screenshots, 2)
        self.assertEqual(h.clicks, [])
        self.assertEqual(len(h.delays), 1)
        self.assertIsNone(h.delays[0]['success'])

    def test_owner_takeover_during_deadline_confirmation_propagates(self):
        h = Harness([
            battle(), {'exception': RequestHumanTakeover('owner logged in')},
        ], real_defer=True, step=12)
        with self.assertRaises(RequestHumanTakeover):
            h._defer_battle_preparation()
        self.assertEqual(h.screenshots, 2)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.delays, [])

    def test_single_battle_frame_does_not_hide_failed_preparation(self):
        h = Harness([battle(), prepare()], real_defer=True)
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        self.assertEqual(len(h.delays), 1)

    def test_late_battle_after_exit_click_is_accepted_before_confirming_exit(self):
        h = Harness([
            prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_EXIT'}), battle(), battle(),
        ], real_defer=True)
        self.assertTrue(h._defer_battle_preparation())
        self.assertEqual([item[0] for item in h.clicks], ['I_EXIT'])
        self.assertEqual(h.delays, [])

    def test_failure_after_requested_exit_is_not_treated_as_started_battle(self):
        h = Harness([
            prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_EXIT'}),
            {'visible': {'I_FALSE'}},
        ], real_defer=True)
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        self.assertEqual([item[0] for item in h.clicks], ['I_EXIT', 'I_FALSE', 'I_FALSE', 'I_FALSE'])
        self.assertEqual(len(h.delays), 1)

    def test_cleanup_takeover_exception_propagates_before_further_clicks(self):
        h = Harness([
            prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_EXIT'}),
            {'exception': RequestHumanTakeover('owner logged in')},
        ], real_defer=True)
        with self.assertRaises(RequestHumanTakeover):
            h._defer_battle_preparation()
        self.assertEqual([item[0] for item in h.clicks], ['I_EXIT'])
        self.assertEqual(h.delays, [])

    def test_prepare_controls_override_false_battle_marker_during_cleanup(self):
        h = Harness([
            prepare(visible={'I_PREPARE_HIGHLIGHT', 'I_BATTLE_INFO', 'I_EXIT'}),
        ], real_defer=True)
        with self.assertRaises(TaskEnd):
            h._defer_battle_preparation()
        self.assertEqual(len(h.delays), 1)
        self.assertEqual(len([item for item in h.clicks if item[0] == 'I_EXIT']), 3)


if __name__ == '__main__':
    unittest.main()
