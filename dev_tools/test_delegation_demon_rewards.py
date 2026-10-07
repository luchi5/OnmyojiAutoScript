"""Offline regressions for account 01 reward errors; no OAS/ADB/OCR session."""
from collections import deque
from copy import copy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeLogger, extract_methods


ROOT = Path(__file__).resolve().parents[1]
DELEGATION = ROOT / 'tasks/Delegation/script_task.py'
DEMON = ROOT / 'tasks/DemonEncounter/script_task.py'
DEVICE = ROOT / 'module/device/device.py'


class GameTooManyClickError(Exception):
    pass


class GameStuckError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class RequestHumanTakeover(Exception):
    pass


class AccountLoggedInElsewhere(RequestHumanTakeover):
    pass


class TaskEnd(Exception):
    pass


class RuleClick:
    def __init__(self, roi_front, roi_back, name):
        self.roi_front, self.roi_back, self.name = roi_front, roi_back, name


class Guard:
    """Use the actual duplicate-click check instead of a test imitation."""
    def __init__(self, logger):
        self.click_record = deque(maxlen=15)
        self.clears = 0
        methods = extract_methods(DEVICE, 'Device', ('click_record_check',), {
            'logger': logger, 'GameTooManyClickError': GameTooManyClickError,
        })
        self.click_record_check = methods['click_record_check'].__get__(self)

    def click_record_clear(self):
        self.clears += 1
        self.click_record.clear()

    def click(self, name):
        self.click_record.append(name)
        self.click_record_check()


class Reader:
    """Real error-image completion boxes supplied in their original coordinates."""
    roi = (675, 129, 441, 517)

    def __init__(self, harness):
        self.harness = harness

    def detect_and_ocr(self, _image):
        results = []
        x, y, width, height = self.roi
        for text, (left, top, right, bottom) in self.harness.boxes():
            if x <= left < right <= x + width and y <= top < bottom <= y + height:
                results.append(SimpleNamespace(ocr_text=text, box=(
                    (left - x, top - y), (right - x, top - y),
                    (right - x, bottom - y), (left - x, bottom - y),
                )))
        return results


MARKERS = ('I_REWARDS_MIN', 'I_REWARDS_GET', 'I_REWARDS_FALSE',
           'I_REWARDS_DONE', 'I_REWARDS_CHAT', 'I_CHAT_1', 'I_CHAT_2')


class DelegationHarness:
    def __init__(self, mode='list_works'):
        self.mode = mode
        self.state = 'map'
        self.clock = FakeClock(step=0.25)
        self.logger = FakeLogger()
        self.device = Guard(self.logger)
        self.device.image = None
        self.O_D_DONE = Reader(self)
        self.clicks, self.delays, self.navigation, self.delegated = [], [], [], []
        self.screenshots = 0
        self.config = SimpleNamespace(delegation=SimpleNamespace(delegation_config=SimpleNamespace(
            miyoshino_painting=True, bird_feather=False, find_earring=False,
            cat_boss=False, miyoshino=False, strange_trace=False,
        )))
        for marker in MARKERS:
            setattr(self, marker, marker)
        namespace = {
            'copy': copy, 'RuleClick': RuleClick, 'datetime': datetime, 'timedelta': timedelta,
            'logger': self.logger, 'TaskEnd': TaskEnd, 'GamePageUnknownError': GamePageUnknownError,
            'GameStuckError': GameStuckError, 'GameTooManyClickError': GameTooManyClickError,
            'task_time': SimpleNamespace(monotonic=lambda: self.clock.now),
            'sleep': lambda seconds: setattr(self.clock, 'now', self.clock.now + seconds),
            'page_main': 'page_main', 'page_delegation': 'page_delegation',
        }
        names = ('run', '_completed_reward_candidates', '_reward_dialog_visible',
                 '_wait_reward_dialog', 'check_reward')
        for name, method in extract_methods(DELEGATION, 'ScriptTask', names, namespace).items():
            setattr(self, name, method.__get__(self))

    def boxes(self):
        if self.mode == 'no_ocr':
            return []
        if self.state == 'empty' or self.mode == 'empty':
            return [('画', (1150, 282, 1170, 306))]
        return [('完成', (995, 263, 1052, 297)),
                ('完成', (650, 604, 700, 632))]

    def screenshot(self):
        self.clock.advance()
        self.screenshots += 1
        if self.screenshots > 600:
            raise AssertionError('Unbounded reward loop')
        if self.mode == 'takeover':
            raise AccountLoggedInElsewhere('Owner online')
        if self.mode == 'human':
            raise RequestHumanTakeover('Special stop')
        if self.mode == 'stuck':
            raise GameStuckError('No progress')

    def appear(self, marker):
        if marker == 'I_REWARDS_MIN':
            return self.state in ('map', 'empty') or self.mode == 'overlay'
        if marker == 'I_REWARDS_GET':
            return self.state == 'get'
        if marker == 'I_REWARDS_DONE':
            return self.state == 'done'
        if marker == 'I_CHAT_1':
            return self.state == 'chat'
        return False

    def click(self, rule, interval=None):
        self.device.click(rule.name)
        self.clicks.append((rule.name, rule.roi_front, interval))
        is_map = rule.roi_front[0] < 956
        if self.mode not in ('frozen', 'no_ocr') and (self.mode != 'map_works' or is_map):
            self.state = 'get'
        return True

    def appear_then_click(self, marker, interval=None):
        if not self.appear(marker):
            return False
        self.device.click(marker)
        self.clicks.append((marker, None, interval))
        if marker == 'I_REWARDS_GET':
            self.state = 'map' if self.mode == 'unconfirmed' else 'done'
        elif marker == 'I_REWARDS_DONE':
            self.state = 'empty'
        elif marker == 'I_CHAT_1':
            self.state = 'done'
        return True

    def set_next_run(self, **kwargs):
        self.delays.append(kwargs)

    def goto_page(self, page):
        self.navigation.append(page)

    def delegate_one(self, name):
        self.delegated.append(name)


class TextReader:
    def __init__(self, harness, kind):
        self.harness, self.kind = harness, kind

    def detect_text(self, _image):
        if self.kind == 'question':
            return 'test question %s' % self.harness.answers
        return 'option %s' % self.kind


class DemonHarness:
    def __init__(self, stuck=None):
        self.logger = FakeLogger()
        self.device = Guard(self.logger)
        self.device.image = None
        self.stuck = stuck
        self.state = 'letter'
        self.answers = 0
        self.screenshots = 0
        self.clock = FakeClock(step=0.5)
        for marker in ('I_LETTER_CLOSE', 'I_UI_REWARD', 'C_ANSWER_1', 'C_ANSWER_2', 'C_ANSWER_3'):
            setattr(self, marker, marker)
        self.O_LETTER_QUESTION = TextReader(self, 'question')
        for number in range(1, 4):
            setattr(self, f'O_LETTER_ANSWER_{number}', TextReader(self, number))
        namespace = {
            'logger': self.logger,
            'Answer': lambda: SimpleNamespace(answer_one=lambda **kwargs: 1),
            'time': SimpleNamespace(sleep=lambda seconds: setattr(self.clock, 'now', self.clock.now + seconds)),
        }
        self._mail = extract_methods(DEMON, 'ScriptTask', ('_mail',), namespace)['_mail'].__get__(self)

    def screenshot(self):
        self.clock.advance()
        self.screenshots += 1
        if self.screenshots > 100:
            raise AssertionError('No click guard within a stuck question')
        if self.stuck == 'takeover':
            raise AccountLoggedInElsewhere('Owner online')

    def appear(self, marker, threshold=None):
        if marker == 'I_LETTER_CLOSE':
            return self.state == 'letter'
        return marker == 'I_UI_REWARD' and self.state == 'reward'

    def click(self, marker, interval=None):
        name = 'answer_1' if marker == 'C_ANSWER_1' else marker
        self.device.click(name)
        if self.stuck != 'question':
            self.state = 'reward'
        return True

    def ui_reward_appear_click(self):
        if self.state != 'reward':
            return False
        self.device.click('UI_UI_REWARD')
        if self.stuck != 'reward':
            self.answers += 1
            self.state = 'map' if self.answers % 3 == 0 else 'letter'
        return True


class DelegationRewardTests(unittest.TestCase):
    def test_actual_image_boxes_make_individual_interactive_candidates(self):
        harness = DelegationHarness()
        candidates = harness._completed_reward_candidates()
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0][1].roi_front, (995, 306, 60, 34))
        self.assertEqual(candidates[1][1].roi_front, (657, 564, 36, 28))
        # No click on the completion flag at y263..297, or between both labels.
        self.assertGreater(candidates[0][1].roi_front[1], 297)
        self.assertLess(candidates[1][1].roi_front[1] + 28, 604)
        self.assertEqual(harness.O_D_DONE.roi, (675, 129, 441, 517))

    def test_list_reward_is_confirmed_before_completion(self):
        harness = DelegationHarness()
        self.assertTrue(harness.check_reward())
        self.assertEqual(harness.state, 'empty')
        self.assertEqual(harness.device.clears, 2)

    def test_frozen_flag_uses_alternative_map_marker_once(self):
        harness = DelegationHarness('map_works')
        self.assertTrue(harness.check_reward())
        task_clicks = [click for click in harness.clicks if click[0] == 'delegation_completed_task']
        self.assertEqual(len(task_clicks), 2)

    def test_no_progress_is_bounded_and_never_clears_guard(self):
        harness = DelegationHarness('frozen')
        self.assertFalse(harness.check_reward())
        self.assertEqual(len(harness.clicks), 2)
        self.assertEqual(harness.device.clears, 0)
        self.assertLess(harness.clock.now, 120)

    def test_unconfirmed_return_is_pending(self):
        harness = DelegationHarness('unconfirmed')
        self.assertFalse(harness.check_reward())
        self.assertEqual(harness.device.clears, 1)  # Entering a real reward dialog.

    def test_reward_overlay_does_not_look_like_empty_map(self):
        harness = DelegationHarness('overlay')
        self.assertTrue(harness.check_reward())
        self.assertEqual(harness.state, 'empty')

    def test_empty_visible_tasks_need_two_readable_scans(self):
        harness = DelegationHarness('empty')
        self.assertTrue(harness.check_reward())
        self.assertEqual(harness.screenshots, 2)
        self.assertFalse(harness.clicks)

    def test_empty_ocr_is_not_proof_rewards_absent(self):
        harness = DelegationHarness('no_ocr')
        self.assertFalse(harness.check_reward())

    def test_routine_stuck_is_skipped(self):
        self.assertFalse(DelegationHarness('stuck').check_reward())

    def test_owner_takeover_propagates(self):
        with self.assertRaises(AccountLoggedInElsewhere):
            DelegationHarness('takeover').check_reward()

    def test_other_human_takeover_propagates(self):
        with self.assertRaises(RequestHumanTakeover):
            DelegationHarness('human').check_reward()

    def test_pending_reward_defers_without_claiming_or_starting_delegation(self):
        harness = DelegationHarness('frozen')
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assertFalse(harness.delegated)
        self.assertEqual(harness.navigation, ['page_delegation', 'page_main'])
        self.assertEqual(len(harness.delays), 1)
        self.assertIsNone(harness.delays[0]['success'])
        self.assertFalse(harness.delays[0]['server'])
        self.assertAlmostEqual((harness.delays[0]['target'] - datetime.now()).total_seconds(), 1800, delta=2)

    def test_confirmed_rewards_allow_new_delegation(self):
        harness = DelegationHarness()
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assertEqual(harness.delegated, ['画'])
        self.assertTrue(harness.delays[0]['success'])


class DemonAnswerTests(unittest.TestCase):
    def test_two_letter_lanterns_do_not_trigger_history_from_previous_answers(self):
        harness = DemonHarness()
        harness.device.click_record.extend(['de_2', 'answer_1', 'UI_UI_REWARD'])
        harness._mail('de_2')
        harness.state = 'letter'
        harness._mail('de_3')
        self.assertEqual(harness.answers, 6)
        self.assertEqual(harness.device.clears, 6)

    def test_single_question_without_progress_keeps_duplicate_click_guard(self):
        harness = DemonHarness('question')
        with self.assertRaises(GameTooManyClickError):
            harness._mail('de_2')
        self.assertEqual(harness.answers, 0)
        # The actual guard clears its own record once while raising.
        self.assertEqual(harness.device.clears, 1)

    def test_reward_that_never_leaves_does_not_clear_click_guard(self):
        harness = DemonHarness('reward')
        with self.assertRaises(GameTooManyClickError):
            harness._mail('de_2')
        self.assertEqual(harness.device.clears, 1)

    def test_owner_takeover_is_never_swallowed_in_answering(self):
        with self.assertRaises(AccountLoggedInElsewhere):
            DemonHarness('takeover')._mail('de_2')


if __name__ == '__main__':
    unittest.main()
