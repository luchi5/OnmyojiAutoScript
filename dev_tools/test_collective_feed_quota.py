"""Actual bounded N-card quota/continuation methods, entirely in RAM.

No live account JSON, backend, OCR model, ADB or notification service is used.
Run: toolkit/python.exe -X utf8 -B -m unittest dev_tools.test_collective_feed_quota
"""
import ast
import copy
from collections import deque
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import re
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/CollectiveMissions/script_task.py'


class Clock(datetime):
    current = datetime(2026, 10, 7, 20, 16, 30)
    elapsed = 0

    @classmethod
    def now(cls):
        return cls.current + timedelta(seconds=cls.elapsed)


class Timer:
    def __init__(self, seconds):
        self.seconds = seconds

    def start(self):
        self.started = Clock.elapsed
        return self

    def reached(self):
        Clock.elapsed += 0.1
        return Clock.elapsed - self.started >= self.seconds


def load_methods(names, env):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ScriptTask')
    nodes = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), env)
    return {name: env[name] for name in names}


ENV = dict(datetime=Clock, timedelta=timedelta, Timer=Timer, logger=Mock(), re=re)
TREE = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
ENUM = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == 'MC')
exec(compile(ast.fix_missing_locations(ast.Module(body=[ENUM], type_ignores=[])), str(SOURCE), 'exec'),
     dict(Enum=Enum), ENV)
MC = ENV['MC']
ACTUAL = load_methods(('_feed_resume_allowed', '_save_feed_progress', '_feed_to_quota'), ENV)


class Runtime:
    _feed_resume_allowed = ACTUAL['_feed_resume_allowed']
    _save_feed_progress = ACTUAL['_save_feed_progress']
    _feed_to_quota = ACTUAL['_feed_to_quota']

    def __init__(self, readings=(), *, kind='', attempted='', date='2026-10-07',
                 selected=MC.FEED, settled=True, overview=True):
        self.readings = deque(readings)
        self.snapshots = []
        self.feed_calls = []
        self._collective_pending_until = None
        self.state = S(pending_kind=kind, pending_until='', attempted_date=attempted,
                       completed_date='', dokan_finished_date=date,
                       missions_select=selected.value)
        self.config = S(collective_missions=S(missions_config=self.state), save=self.save)
        self._preferred_mission = Mock(return_value=selected)
        self._mission_counter = Mock(side_effect=self.counter)
        self._return_to_missions = Mock(return_value=overview)
        self._feed = Mock(side_effect=self.feed)
        self.settled = settled

    def counter(self):
        return self.readings.popleft() if self.readings else None

    def save(self):
        self.snapshots.append(copy.deepcopy(self.state))

    def feed(self, index, max_items=2):
        self.feed_calls.append((index, max_items, self.state.pending_kind))
        return self.settled


class FeedQuotaTests(unittest.TestCase):
    def setUp(self):
        Clock.current = datetime(2026, 10, 7, 20, 16, 30)
        Clock.elapsed = 0

    def test_twenty_of_thirty_is_completed_in_five_confirmed_batches(self):
        task = Runtime([(20, 30)] + [value for n in (22, 24, 26, 28, 30) for value in ((n, 30), (n, 30))])
        self.assertTrue(task._feed_to_quota(0, (20, 30)))
        self.assertEqual([budget for _, budget, _ in task.feed_calls], [2] * 5)
        self.assertEqual([kind for _, _, kind in task.feed_calls],
                         ['feed_inflight:20', 'feed_inflight:22', 'feed_inflight:24',
                          'feed_inflight:26', 'feed_inflight:28'])
        self.assertEqual(task.state.pending_kind, 'feed_confirmed:30')
        self.assertTrue(all(s.attempted_date == '2026-10-07' for s in task.snapshots))

    def test_final_odd_remainder_selects_only_one_n_card(self):
        task = Runtime([(29, 30), (30, 30), (30, 30)])
        self.assertTrue(task._feed_to_quota(2, (29, 30)))
        self.assertEqual(task.feed_calls, [(2, 1, 'feed_inflight:29')])

    def test_each_batch_persists_inflight_before_any_resource_click(self):
        task = Runtime([(28, 30), (30, 30), (30, 30)])

        def inspect(index, max_items):
            self.assertEqual(task.snapshots[-1].pending_kind, 'feed_inflight:28')
            self.assertEqual(task.snapshots[-1].attempted_date, '2026-10-07')
            return True

        task._feed.side_effect = inspect
        self.assertTrue(task._feed_to_quota(0, (28, 30)))

    def test_unconfirmed_reward_or_overview_never_repeats_submission(self):
        for kwargs in (dict(settled=False), dict(overview=False)):
            with self.subTest(kwargs=kwargs):
                task = Runtime([(20, 30)], **kwargs)
                self.assertFalse(task._feed_to_quota(0, (20, 30)))
                self.assertEqual(len(task.feed_calls), 1)
                self.assertEqual(task.state.pending_kind, 'feed_inflight:20')

    def test_no_progress_bad_ocr_wrong_increment_or_mismatched_frames_stop(self):
        for pair in (((20, 30), (20, 30)), (None, None),
                     ((21, 30), (21, 30)), ((23, 30), (23, 30)),
                     ((22, 30), (21, 30))):
            with self.subTest(pair=pair):
                task = Runtime([(20, 30), *pair])
                self.assertFalse(task._feed_to_quota(0, (20, 30)))
                self.assertEqual(len(task.feed_calls), 1)
                self.assertEqual(task.state.pending_kind, 'feed_inflight:20')

    def test_initial_unconfirmed_counter_cannot_select_or_submit(self):
        for first, initial in (((21, 30), (20, 30)), (None, (20, 30)),
                               ((20, 30), None), ((30, 30), (30, 30))):
            with self.subTest(first=first, initial=initial):
                task = Runtime([first])
                self.assertFalse(task._feed_to_quota(0, initial))
                task._feed.assert_not_called()
                self.assertEqual(task.snapshots, [])

    def test_failed_checkpoint_save_stops_before_spending(self):
        task = Runtime([(20, 30)])
        task.config.save = Mock(side_effect=OSError('storage unavailable'))
        with self.assertRaises(OSError):
            task._feed_to_quota(0, (20, 30))
        task._feed.assert_not_called()

    def test_full_zero_counter_is_bounded_to_fifteen_batches(self):
        task = Runtime([(0, 30)] + [value for n in range(2, 31, 2) for value in ((n, 30), (n, 30))])
        self.assertTrue(task._feed_to_quota(0, (0, 30)))
        self.assertEqual(len(task.feed_calls), 15)
        self.assertEqual(sum(budget for _, budget, _ in task.feed_calls), 30)

    def test_time_limit_keeps_confirmed_partial_progress_without_new_batch(self):
        task = Runtime([(20, 30), (22, 30), (22, 30)])

        def slow_feed(index, max_items):
            task.feed_calls.append((index, max_items, task.state.pending_kind))
            Clock.elapsed += 181
            return True

        task._feed.side_effect = slow_feed
        self.assertFalse(task._feed_to_quota(0, (20, 30)))
        self.assertEqual(len(task.feed_calls), 1)
        self.assertEqual(task.state.pending_kind, 'feed_confirmed:22')
        self.assertIsNotNone(task._collective_pending_until)

    def test_midnight_during_submission_is_not_saved_as_confirmed_next_day(self):
        Clock.current = datetime(2026, 10, 7, 23, 59, 59)
        task = Runtime([(20, 30), (22, 30), (22, 30)])

        def cross_midnight(index, max_items):
            Clock.elapsed += 2
            return True

        task._feed.side_effect = cross_midnight
        self.assertFalse(task._feed_to_quota(0, (20, 30)))
        self.assertEqual(task.state.pending_kind, 'feed_inflight:20')
        self.assertEqual(task.state.attempted_date, '2026-10-07')

    def test_old_schema_without_checkpoint_fields_never_spends(self):
        for field in ('pending_kind', 'pending_until', 'attempted_date'):
            with self.subTest(field=field):
                task = Runtime([(20, 30)])
                delattr(task.state, field)
                self.assertFalse(task._feed_to_quota(0, (20, 30)))
                task._feed.assert_not_called()
                self.assertEqual(task.snapshots, [])

    def test_unsubmitted_batch_with_double_unchanged_counter_remains_retryable(self):
        task = Runtime([(20, 30), (20, 30), (20, 30)], settled=False)
        self.assertFalse(task._feed_to_quota(0, (20, 30)))
        self.assertEqual(len(task.feed_calls), 1)
        self.assertFalse(task._feed_submitted)
        self.assertEqual(task.state.pending_kind, 'feed_confirmed:20')
        self.assertEqual(task._collective_pending_until,
                         datetime(2026, 10, 7, 20, 19, 30))
        task.readings.append((20, 30))
        self.assertTrue(task._feed_resume_allowed((20, 30)))

    def test_zero_start_unsubmitted_failure_can_resume_without_clearing_guard(self):
        task = Runtime([(0, 30), (0, 30), (0, 30)], settled=False)
        self.assertFalse(task._feed_to_quota(0, (0, 30)))
        self.assertEqual(task.state.pending_kind, 'feed_confirmed:0')
        self.assertEqual(task.state.attempted_date, '2026-10-07')
        task.readings.append((0, 30))
        self.assertTrue(task._feed_resume_allowed((0, 30)))
        self.assertEqual(task.state.attempted_date, '2026-10-07')

    def test_submitted_but_unsettled_result_cannot_save_retryable_checkpoint(self):
        task = Runtime([(20, 30), (20, 30), (20, 30)])

        def submitted_then_failed(index, max_items):
            task._feed_submitted = True
            return False

        task._feed.side_effect = submitted_then_failed
        self.assertFalse(task._feed_to_quota(0, (20, 30)))
        self.assertEqual(task.state.pending_kind, 'feed_inflight:20')
        task._return_to_missions.assert_not_called()
        self.assertEqual(task._mission_counter.call_count, 1)

    def test_unsubmitted_failure_requires_overview_same_date_and_two_equal_counters(self):
        for kwargs, second, third in ((dict(overview=False), (20, 30), (20, 30)),
                                       (dict(), None, (20, 30)),
                                       (dict(), (20, 30), None),
                                       (dict(), (20, 30), (21, 30))):
            with self.subTest(kwargs=kwargs, second=second, third=third):
                task = Runtime([(20, 30), second, third], settled=False, **kwargs)
                self.assertFalse(task._feed_to_quota(0, (20, 30)))
                self.assertEqual(task.state.pending_kind, 'feed_inflight:20')
        Clock.current = datetime(2026, 10, 7, 23, 59, 59)
        task = Runtime([(20, 30), (20, 30), (20, 30)])
        task._feed.side_effect = lambda *args, **kwargs: (setattr(Clock, 'elapsed', 2) or False)
        self.assertFalse(task._feed_to_quota(0, (20, 30)))
        self.assertEqual(task.state.pending_kind, 'feed_inflight:20')


class ActualNCardSelectionTests(unittest.TestCase):
    def test_actual_single_card_budget_does_not_spend_an_extra_card(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime()
        task.ready_after = 1
        self.assertTrue(task._feed(0, max_items=1))
        self.assertEqual(task.selection_clicks, 1)
        self.assertEqual(task.selection_long_clicks, 0)
        self.assertEqual(len(task.submits), 1)
        task.device.click_record_clear.assert_not_called()

    def test_actual_selection_never_repeats_an_unready_click(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime()
        task.ready_after = 99
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(task.selection_long_clicks, 0)
        self.assertEqual(task.submits, [])
        task.device.click_record_clear.assert_not_called()

    def test_invalid_budget_or_blocked_click_never_submits(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        for budget in (0, 3, True, None):
            with self.subTest(budget=budget):
                SubmitClock.now = 0
                task = SubmitRuntime()
                self.assertFalse(task._feed(0, max_items=budget))
                self.assertEqual(task.selection_clicks, 0)
                self.assertEqual(task.submits, [])
        SubmitClock.now = 0
        task = SubmitRuntime()
        task._collective_click_available = Mock(return_value=False)
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 0)
        self.assertEqual(task.submits, [])

    def test_actual_stacked_extra_count_cannot_submit_three_as_two(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        # Reproduce the real 1-card + 2-card stack result even if the expanded
        # marker erroneously appears: actual header quantity remains decisive.
        task = SubmitRuntime(selected_steps=(1, 2))
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selected_count, 3)
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(task.selection_long_clicks, 0)
        self.assertEqual(task.submits, [])

    def test_actual_existing_selection_is_canceled_before_budgeted_new_selection(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime(selected_count=3)
        task.phase = 'overview'
        self.assertTrue(task._feed(0, max_items=2))
        self.assertEqual(task.cancel_clicks, 1)
        self.assertEqual(task.selected_count, 2)
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(task.selection_long_clicks, 0)
        self.assertEqual(len(task.submits), 1)

    def test_actual_unconfirmed_expanded_mode_never_selects_or_submits(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime(expand_confirmed=False)
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 0)
        self.assertEqual(task.submits, [])
        self.assertLessEqual(task.expand_clicks, 8)
        self.assertLessEqual(SubmitClock.now, 12)

    def test_actual_unknown_selection_count_stops_before_second_slot(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime()
        task.O_FEED_SUBMIT_COUNT.ocr.side_effect = lambda _: '乱码'
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 1)
        self.assertEqual(task.submits, [])
        self.assertLessEqual(SubmitClock.now, 12)

    def test_actual_final_count_is_rechecked_immediately_before_submission(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime()
        numbers = iter((1, 1, 2, 2, 3))
        task.O_FEED_SUBMIT_COUNT.ocr.side_effect = lambda _: f'将提交{next(numbers)}次任务'
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(task.submits, [])

    def test_actual_non_n_page_cannot_enter_selection(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime(n_page=False)
        task.phase = 'overview'
        self.assertFalse(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 0)
        self.assertEqual(task.submits, [])

    def test_actual_lower_count_animation_waits_for_two_correct_frames(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime()
        texts = iter(('', '将提交1次任务', '将提交1次任务',
                      '将提交1次任务', '将提交2次任务', '将提交2次任务',
                      '将提交2次任务', '将提交2次任务'))
        task.O_FEED_SUBMIT_COUNT.ocr.side_effect = lambda _: next(texts)
        self.assertTrue(task._feed(0, max_items=2))
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(task.selection_long_clicks, 0)
        self.assertTrue(task._feed_submitted)
        self.assertEqual(len(task.submits), 1)
        self.assertGreaterEqual(SubmitClock.now, 2.2)


class ActualNPageExitTests(unittest.TestCase):
    def task(self, *, selected=0):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime(selected_count=selected)
        task.phase = 'feed_select'
        return task

    def test_empty_n_page_uses_blue_back_to_return_to_missions(self):
        task = self.task()
        self.assertTrue(task._return_to_missions())
        self.assertEqual(task.feed_back_clicks, 1)
        self.assertEqual(task.cancel_clicks, 0)
        self.assertEqual(task.submits, [])
        self.assertEqual(task.phase, 'collective_overview')

    def test_selected_n_page_cancels_before_blue_back_without_submitting(self):
        task = self.task(selected=3)
        self.assertTrue(task._return_to_missions())
        self.assertEqual(task.cancel_clicks, 1)
        self.assertEqual(task.feed_back_clicks, 1)
        self.assertEqual(task.selected_count, 0)
        self.assertEqual(task.submits, [])
        self.assertEqual(list(task.device.click_record), ['feed_cancel', 'feed_back'])

    def test_leaving_selected_n_page_cancels_backs_out_and_reaches_guild(self):
        task = self.task(selected=3)
        self.assertTrue(task._leave_missions())
        self.assertEqual(task.cancel_clicks, 1)
        self.assertEqual(task.feed_back_clicks, 1)
        self.assertEqual(task.submits, [])
        self.assertEqual(task.phase, 'guild')

    def test_only_an_actual_feed_submit_sets_the_spending_flag(self):
        task = self.task()
        task.phase = 'ready'
        self.assertTrue(task._submit_collective_once_and_claim(task.I_FEED_SUBMIT))
        self.assertTrue(task._feed_submitted)
        task = self.task()
        task.phase = 'overview'
        task._feed_submitted = False
        self.assertFalse(task._submit_collective_once_and_claim(task.I_FEED_SUBMIT))
        self.assertFalse(task._feed_submitted)

    def test_generic_material_submit_does_not_require_feed_attribute(self):
        from dev_tools.test_collective_missions_rewards import Clock as SubmitClock, Runtime as SubmitRuntime
        SubmitClock.now = 0
        task = SubmitRuntime(kind='donate')
        del task.I_FEED_SUBMIT
        self.assertTrue(task._submit_collective_once_and_claim(task.I_CM_PRESENT))
        self.assertFalse(hasattr(task, '_feed_submitted'))


class SelectedCountParserTests(unittest.TestCase):
    def read(self, text):
        from dev_tools.test_collective_missions_rewards import ACTUAL as SubmissionMethods
        task = S(O_FEED_SUBMIT_COUNT=S(ocr=lambda _: text), device=S(image=object()))
        return SubmissionMethods['_feed_selected_count'](task)

    def test_complete_header_and_whitespace_are_parsed(self):
        for text, expected in (('将提交1次任务', 1), ('将提交2次任务', 2),
                               ('将提交30次任务', 30), (' 将提交 2 次任务\n', 2)):
            with self.subTest(text=text):
                self.assertEqual(self.read(text), expected)

    def test_partial_numeric_or_invalid_header_is_not_a_resource_budget(self):
        for text in ('', '3', '提交2', '将提交2', '2次任务', '将提交0次任务',
                     '将提交31次任务', '将提交-1次任务', '将提交2.0次任务',
                     '将提交2次任务其它文字', None, True, (2, 28, 30)):
            with self.subTest(text=text):
                self.assertIsNone(self.read(text))


class ExpandedModePixelTests(unittest.TestCase):
    def fixture(self, name):
        import cv2
        import numpy as np
        path = ROOT / 'tests' / 'fixtures' / 'collective_feed' / name
        patch_pixels = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(patch_pixels)
        patch_pixels = cv2.cvtColor(patch_pixels, cv2.COLOR_BGR2RGB)
        self.assertEqual(patch_pixels.shape, (82, 139, 3))
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[606:688, 5:144] = patch_pixels
        return frame

    def copied_rule(self):
        from tasks.CollectiveMissions.feed_selection_assets import FeedSelectionAssets
        original = FeedSelectionAssets.I_FEED_EXPANDED
        saved_roi = list(original.roi_front)
        saved_image = original._image
        rule = copy.copy(original)
        rule.roi_front = list(original.roi_front)
        return original, saved_roi, saved_image, rule

    def assert_original_unchanged(self, original, saved_roi, saved_image):
        self.assertEqual(original.roi_front, saved_roi)
        self.assertIs(original._image, saved_image)

    def test_real_stacked_pixels_are_rejected_despite_matching_label_shape(self):
        from module.atom.image import RuleImage
        original, saved_roi, saved_image, rule = self.copied_rule()
        frame = self.fixture('stacked.png')
        # This is the observed false positive that plain normalized template
        # matching permits; brightness confirmation must reject it.
        self.assertTrue(RuleImage.match(rule, frame))
        self.assertFalse(rule.match(frame))
        self.assert_original_unchanged(original, saved_roi, saved_image)

    def test_real_expanded_pixels_are_accepted_without_mutating_shared_asset(self):
        original, saved_roi, saved_image, rule = self.copied_rule()
        self.assertTrue(rule.match(self.fixture('expanded.png')))
        self.assert_original_unchanged(original, saved_roi, saved_image)


class FeedContinuationTests(unittest.TestCase):
    def setUp(self):
        Clock.current = datetime(2026, 10, 7, 20, 16, 30)
        Clock.elapsed = 0

    def test_confirmed_partial_checkpoint_resumes_only_at_exact_counter(self):
        task = Runtime([(22, 30)], kind='feed_confirmed:22', attempted='2026-10-07')
        self.assertTrue(task._feed_resume_allowed((22, 30)))
        self.assertEqual(task.state.attempted_date, '2026-10-07')
        self.assertEqual(task.snapshots, [])

    def test_unknown_or_inflight_attempt_never_resumes_a_submission(self):
        for kind in ('', 'feed_inflight:20', 'bondling_reward', 'feed_confirmed:garbage',
                     'feed_confirmed:0', 'feed_confirmed:30'):
            with self.subTest(kind=kind):
                task = Runtime([(22, 30)], kind=kind, attempted='2026-10-07')
                self.assertFalse(task._feed_resume_allowed((22, 30)))
                task._feed.assert_not_called()

    def test_changed_count_day_or_target_rejects_checkpoint(self):
        for kwargs, counter, second in ((dict(attempted='2026-10-06'), (22, 30), (22, 30)),
                                         (dict(selected=MC.AW3), (22, 30), (22, 30)),
                                         (dict(), (24, 30), (24, 30)),
                                         (dict(), (22, 30), None),
                                         (dict(), (22, 30), (23, 30))):
            with self.subTest(kwargs=kwargs, counter=counter, second=second):
                kwargs.setdefault('attempted', '2026-10-07')
                task = Runtime([second], kind='feed_confirmed:22', **kwargs)
                self.assertFalse(task._feed_resume_allowed(counter))
                task._feed.assert_not_called()


class ActualRunContinuationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_collective_missions_link import Clock as RunClock
        Clock.current = RunClock.current = datetime(2026, 10, 7, 20, 16, 30)
        Clock.elapsed = 0

    def task(self, readings, *, checkpoint='feed_confirmed:22', selected_feed=True):
        from tests.test_collective_missions_link import FakeConfig, Runtime as RunRuntime
        config = FakeConfig()
        config.state.dokan_finished_date = '2026-10-07'
        config.state.attempted_date = '2026-10-07'
        config.state.pending_kind = checkpoint
        config.state.missions_select = '养成'
        task = RunRuntime(config, readings)
        task._preferred_mission = Mock(return_value=task.MC.FEED)
        task.detect_best.return_value = (task.MC.FEED if selected_feed else task.MC.AW3, 0)
        task._feed.return_value = True
        for name in ('_feed_resume_allowed', '_save_feed_progress', '_feed_to_quota'):
            setattr(task, name, ACTUAL[name].__get__(task))
        return task

    def run_task(self, task):
        from tests.test_collective_missions_link import TaskEnd
        with self.assertRaises(TaskEnd):
            task.run()

    def test_real_run_recovers_verified_twenty_two_without_clearing_daily_guard(self):
        from tasks.Component import collective_missions_link as link
        readings = [(22, 8, 30)] * 4
        readings += [value for n in (24, 26, 28, 30) for value in ((n, 30 - n, 30),) * 2]
        readings += [(30, 0, 30)] * 2
        task = self.task(readings)
        with patch.object(link, 'begin_collective_attempt', wraps=link.begin_collective_attempt) as begin:
            self.run_task(task)
        begin.assert_not_called()
        self.assertEqual(task._feed.call_count, 4)
        self.assertEqual([call.kwargs['max_items'] for call in task._feed.call_args_list], [2] * 4)
        self.assertEqual(task.config.state.attempted_date, '2026-10-07')
        self.assertEqual(task.config.state.completed_date, '2026-10-07')
        self.assertEqual(task.config.state.pending_kind, '')
        self.assertEqual(task.config.collective_missions.scheduler.next_run, link.LINK_IDLE_TARGET)
        task._soul.assert_not_called()
        task._donate.assert_not_called()

    def test_real_run_rejects_changed_live_counter_or_missing_checkpoint(self):
        for checkpoint, counter in (('feed_confirmed:22', (24, 6, 30)), ('', (22, 8, 30)),
                                     ('feed_inflight:22', (22, 8, 30))):
            with self.subTest(checkpoint=checkpoint, counter=counter):
                task = self.task([counter], checkpoint=checkpoint)
                self.run_task(task)
                task.select_mission.assert_not_called()
                task._feed.assert_not_called()
                self.assertEqual(task.config.state.attempted_date, '2026-10-07')
                self.assertEqual(task.config.state.completed_date, '')

    def test_real_run_cannot_resume_other_resource_after_valid_feed_checkpoint(self):
        task = self.task([(22, 8, 30)] * 2, selected_feed=False)
        self.run_task(task)
        task._feed.assert_not_called()
        task._soul.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(task.config.state.pending_kind, 'feed_confirmed:22')
        self.assertEqual(task.config.state.completed_date, '')


if __name__ == '__main__':
    unittest.main()
