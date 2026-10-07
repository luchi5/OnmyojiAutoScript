"""Capture contracts under offline images; no ADB, settings, or pushes."""
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch

import numpy as np

from dev_tools import test_closeout_integration as integration
from tasks.Component import daily_feedback_capture as capture
from module.exception import AccountLoggedInElsewhere, GameStuckError, RequestHumanTakeover


class FeedbackAssets:
    I_TALISMAN_HEADER = 'header'
    I_TALISMAN_TODAY_SELECTED = 'today_selected'
    C_TALISMAN_TASK_TAB = 'task_tab'
    C_TALISMAN_TODAY_TAB = 'today_tab'


def text(value, x=500, y=570, width=180, height=28):
    return S(ocr_text=value, box=np.array(((x, y), (x + width, y),
                                          (x + width, y + height), (x, y + height))))


class Runtime:
    I_TP_GOTO = 'goto'
    I_TP_EXP = 'exp'
    I_UI_REWARD = 'reward'
    I_CM_RECORDS = 'records'

    def __init__(self, counts=((30, 0, 30), (30, 0, 30)), pages=(True, True), rewards=(False, False),
                 today=(True, True)):
        self.config = S(config_name='fake-account')
        self.device = S(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        self.O_CM_NUMBER = S(ocr=Mock(side_effect=counts))
        self.pages = pages
        self.rewards = rewards
        self.today = today
        self.frames = 0
        self.device.screenshot = Mock(side_effect=self._screenshot_frame)
        self.screenshot = Mock(side_effect=AssertionError('BaseTask screenshot may execute gameplay'))
        self.click = Mock(side_effect=AssertionError('Capture must not click'))
        self.goto_page = Mock(side_effect=AssertionError('Capture must not navigate'))

    def _screenshot_frame(self):
        self.device.image = np.full((720, 1280, 3), self.frames + 1, dtype=np.uint8)
        self.frames += 1

    def appear(self, marker, **kwargs):
        if marker == 'reward':
            return self.rewards[self.frames - 1]
        if marker == 'today_selected':
            return self.today[self.frames - 1]
        return self.pages[self.frames - 1]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.store = patch.object(capture, '_store', side_effect=lambda task, kind, **values:
                                  self.records.append(dict(kind=kind, **values)) or self.records[-1])
        self.store.start()
        self.sleep = patch.object(capture.time, 'sleep')
        self.sleep.start()
        self.debug = patch.object(capture, '_debug_talisman')
        self.debug.start()
        self.enabled = patch.object(capture, '_enabled', return_value=True)
        self.enabled.start()
        self.assets = patch.dict(sys.modules, {'tasks.Component.daily_feedback_capture_assets':
                                S(DailyFeedbackCaptureAssets=FeedbackAssets)})
        self.assets.start()
        self.target = patch.object(capture, '_talisman_target', return_value=None)
        self.target.start()

    def tearDown(self):
        self.target.stop()
        self.assets.stop()
        self.enabled.stop()
        self.debug.stop()
        self.sleep.stop()
        self.store.stop()

    def test_collective_30_uses_two_fresh_images_and_preserves_last(self):
        task = Runtime()
        result = capture.capture_collective(task)
        self.assertEqual((result['current'], result['total'], result['verified']), (30, 30, True))
        self.assertEqual(task.frames, 2)
        self.assertTrue(np.all(result['image'] == 2))
        task.device.image[:] = 9
        self.assertTrue(np.all(result['image'] == 2))
        task.click.assert_not_called()
        task.goto_page.assert_not_called()
        task.screenshot.assert_not_called()
        self.assertEqual(task.device.screenshot.call_count, 2)

    def test_collective_partial_25_is_proven_progress_not_completion(self):
        result = capture.capture_collective(Runtime(counts=((25, 5, 30), (25, 5, 30))))
        self.assertEqual((result['current'], result['total'], result['verified']), (25, 30, True))

    def test_collective_changed_count_never_verified(self):
        result = capture.capture_collective(Runtime(counts=((25, 5, 30), (26, 4, 30))))
        self.assertFalse(result['verified'])
        self.assertIsNone(result['current'])
        self.assertIsNotNone(result['image'])

    def test_collective_blank_or_invalid_count_does_not_become_zero(self):
        for reading in ((0, 0, 0), (30, 1, 30), (31, -1, 30), (True, 29, 30), None, '', (30, 30)):
            with self.subTest(reading=reading):
                result = capture.capture_collective(Runtime(counts=(reading, reading)))
                self.assertFalse(result['verified'])
                self.assertIsNone(result['current'])

    def test_collective_actual_zero_requires_a_real_30_denominator(self):
        result = capture.capture_collective(Runtime(counts=((0, 30, 30), (0, 30, 30))))
        self.assertEqual((result['current'], result['verified']), (0, True))

    def test_collective_wrong_page_and_reward_overlay_never_store_image(self):
        for pages, rewards in (((False, False), (False, False)), ((True, True), (True, True)),
                               ((True, False), (False, False)), ((True, True), (False, True))):
            with self.subTest(pages=pages, rewards=rewards):
                result = capture.capture_collective(Runtime(pages=pages, rewards=rewards))
                self.assertFalse(result['verified'])
                self.assertNotIn('image', result)

    def test_ocr_exception_is_fail_safe(self):
        task = Runtime()
        task.O_CM_NUMBER.ocr = Mock(side_effect=RuntimeError('OCR unavailable'))
        result = capture.capture_collective(task)
        self.assertFalse(result['verified'])
        task.click.assert_not_called()

    def test_store_exception_is_fail_safe(self):
        with patch.object(capture, '_store', side_effect=RuntimeError('disk unavailable')):
            self.assertFalse(capture.capture_collective(Runtime()))

    def test_midnight_does_not_move_prior_day_proof_to_today(self):
        clock = Mock()
        clock.now.side_effect = [datetime(2026, 10, 7, 23, 59, 59),
                                 datetime(2026, 10, 7, 23, 59, 59),
                                 datetime(2026, 10, 8), datetime(2026, 10, 8)]
        with patch.object(capture, 'datetime', clock):
            result = capture.capture_collective(Runtime())
        self.assertFalse(result['verified'])
        self.assertNotIn('image', result)

    def test_task_started_yesterday_cannot_supply_today_evidence(self):
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 8)
        for collector in (capture.capture_collective, capture.capture_talisman):
            with self.subTest(collector=collector.__name__):
                task = Runtime()
                task._daily_feedback_started_date = datetime(2026, 10, 7).date()
                with patch.object(capture, 'datetime', clock):
                    result = collector(task, final=True)
                self.assertFalse(result['verified'])
                self.assertNotIn('image', result)
                task.device.screenshot.assert_not_called()

    def test_disabled_feedback_never_reads_device_or_stores(self):
        with patch.object(capture, '_enabled', return_value=False):
            for collector in (capture.capture_collective, capture.capture_talisman):
                task = Runtime()
                self.assertFalse(collector(task))
                task.device.screenshot.assert_not_called()
        self.assertEqual(self.records, [])

    def test_device_control_exceptions_reach_scheduler(self):
        for collector in (capture.capture_collective, capture.capture_talisman):
            for error in capture.CONTROL_EXCEPTIONS:
                with self.subTest(collector=collector.__name__, exception=error.__name__):
                    task = Runtime()
                    task.device.screenshot.side_effect = error('control fixture')
                    with self.assertRaises(error):
                        collector(task)
        self.assertEqual(self.records, [])

    def test_other_device_login_subclass_is_not_optional_capture_failure(self):
        task = Runtime()
        task.device.screenshot.side_effect = AccountLoggedInElsewhere('login fixture')
        with self.assertRaises(AccountLoggedInElsewhere):
            capture.capture_collective(task)
        self.assertEqual(self.records, [])

    def talisman(self, readings, *, final=False, **kwargs):
        with patch.object(capture, '_talisman_lines', side_effect=readings):
            return capture.capture_talisman(Runtime(**kwargs), final=final)

    def test_talisman_explicit_daily_activity_two_frames(self):
        lines = [text('今日活跃度:100/100')]
        result = self.talisman([lines, lines], final=True)
        self.assertEqual((result['current'], result['total'], result['verified'], result['final']),
                         (100, 100, True, True))

    def test_talisman_missing_total_is_not_invented(self):
        lines = [text('今日活跃度:80')]
        result = self.talisman([lines, lines])
        self.assertEqual(result['current'], 80)
        self.assertIsNone(result['total'])
        self.assertFalse(result['final'])

    def test_today_experience_can_exceed_chest_target_without_inventing_total(self):
        lines = [text('今日获得经验：113')]
        with patch.object(capture, '_talisman_target', side_effect=(100, 100)):
            result = self.talisman([lines, lines], final=True)
        self.assertEqual(result['current'], 113)
        self.assertTrue(result['verified'])
        self.assertEqual(result['target'], 100)
        self.assertIsNone(result['total'])

    def test_inconsistent_or_unreadable_target_is_not_reported(self):
        lines = [text('今日获得经验113')]
        for targets in ((100, 80), (None, 100), (100, None), (None, None)):
            with self.subTest(targets=targets), patch.object(capture, '_talisman_target', side_effect=targets):
                result = self.talisman([lines, lines])
            self.assertTrue(result['verified'])
            self.assertEqual(result['current'], 113)
            self.assertIsNone(result['target'])
            self.assertIsNone(result['total'])

    def test_talisman_changed_activity_only_keeps_unverified_image(self):
        result = self.talisman([[text('今日活跃度80')], [text('今日活跃度100')]])
        self.assertFalse(result['verified'])
        self.assertIsNone(result['current'])
        self.assertIsNotNone(result['image'])

    def test_talisman_unreadable_number_keeps_positive_day_image(self):
        lines = [text('今日活跃度9O/1OO')]
        result = self.talisman([lines, lines])
        self.assertFalse(result['verified'])
        self.assertIsNone(result['current'])
        self.assertIsNotNone(result['image'])

    def test_talisman_weekly_progress_or_level_currency_never_daily_proof(self):
        for label in ('本周活跃度100/100', '活跃度100/100', '500/500', '花合战', '今日',
                      '完成今日活跃度达到100', '任务经验100', '本月活跃度100'):
            with self.subTest(label=label):
                result = self.talisman([[text(label)]])
                self.assertFalse(result['verified'])
                self.assertNotIn('image', result)

    def test_talisman_page_or_popup_failure_does_not_capture(self):
        for kwargs in ({'pages': (False, False)}, {'rewards': (True, False)}):
            result = self.talisman([], **kwargs)
            self.assertFalse(result['verified'])
            self.assertNotIn('image', result)

    def test_talisman_daily_tab_must_be_selected_on_both_frames(self):
        for today in ((False, False), (True, False)):
            result = self.talisman([[text('今日获得经验113')]] * 2, today=today)
            self.assertFalse(result['verified'])
            self.assertNotIn('image', result)

    def test_talisman_ocr_failure_retains_final_failure_marker(self):
        with patch.object(capture, '_talisman_lines', side_effect=RuntimeError('OCR unavailable')):
            result = capture.capture_talisman(Runtime(), final=True)
        self.assertFalse(result['verified'])
        self.assertTrue(result['final'])


class ActivityParserTests(unittest.TestCase):
    def test_strict_labeled_numbers(self):
        self.assertEqual(capture.parse_activity('今日活跃度： 100 / 100'), (100, 100))
        self.assertEqual(capture.parse_activity('每日活跃度0/100'), (0, 100))
        self.assertEqual(capture.parse_activity('今日活跃99'), (99, None))
        self.assertEqual(capture.parse_activity('今日获得经验:113'), (113, None))
        for invalid in ('', '100/100', '本周活跃度100/100', '今日活跃度9O/100',
                        '今日活跃度101/100', '今日活跃度0/0', '今日活跃度999',
                        '今日活跃度100/100累计经验500', '今日活跃度-1/100'):
            self.assertIsNone(capture.parse_activity(invalid), invalid)

    def test_adjacent_number_on_same_row_and_right(self):
        day, value = capture._activity_from_lines([text('今日活跃度', width=120), text('80/100', x=628)])
        self.assertTrue(day)
        self.assertEqual(value, (80, 100))

    def test_unrelated_row_or_ambiguous_numbers_not_accepted(self):
        for extra in ([text('80/100', x=628, y=605)], [text('80/100', x=800)],
                      [text('80/100', x=628), text('60/100', x=632)]):
            day, value = capture._activity_from_lines([text('今日活跃度', width=120), *extra])
            self.assertTrue(day)
            self.assertIsNone(value)

    def test_duplicate_activity_lines_not_reliable(self):
        day, value = capture._activity_from_lines([text('今日活跃度80'), text('今日活跃度80')])
        self.assertTrue(day)
        self.assertIsNone(value)

    def test_chest_target_ocr_is_strict_and_does_not_guess_letters(self):
        task = Runtime()
        fake = S(ocr=Mock())
        for value, expected in (('100', 100), (' 100 ', 100), ('300', 300), ('', None),
                                ('1OO', None), ('0', None), ('301', None), ('100/100', None)):
            with self.subTest(value=value), patch.object(capture, '_TALISMAN_TARGET_OCR', fake):
                fake.ocr.return_value = value
                self.assertEqual(capture._talisman_target(task), expected)


class HookIntegrationTests(unittest.TestCase):
    """Execute the real task hook/finish functions with existing RAM fixtures."""

    def setUp(self):
        self.integration = integration
        self.case = integration.CloseoutIntegrationTests()
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def task(self):
        task = self.integration.talisman(self.case.config)
        source = Path(__file__).resolve().parents[1] / 'tasks/TalismanPass/script_task.py'
        methods = self.integration.extract_methods(source, 'ScriptTask',
                         ('_capture_daily_feedback', '_capture_collective_feedback'), {'logger': Mock()})
        for name, method in methods.items():
            setattr(task, name, method.__get__(task))
        return task

    def test_automatic_claim_capture_level_finish_then_notify(self):
        self.case.queue()
        task = self.task()
        captured = Mock(side_effect=lambda target, **kwargs: target.actions.append(('feedback', kwargs['final'])))
        revisited = Mock(side_effect=lambda target, **kwargs: target.actions.append(('guild_feedback',)))
        finalized = Mock(side_effect=lambda *args, **kwargs: task.actions.append(('finalize',)))
        with patch.object(capture, 'capture_talisman', captured), patch.object(
                capture, 'refresh_collective_feedback', revisited), patch.dict(
                sys.modules, {'tasks.Component.daily_feedback': S(finalize_feedback=finalized)}):
            self.case.finish(task)
        self.assertLess(task.actions.index(('collect',)), task.actions.index(('feedback', True)))
        level_index = next(index for index, action in enumerate(task.actions) if action[0] == 'level')
        self.assertLess(task.actions.index(('feedback', True)), level_index)
        self.assertLess(level_index, task.actions.index(('finalize',)))
        self.assertLess(level_index, task.actions.index(('guild_feedback',)))
        self.assertLess(task.actions.index(('guild_feedback',)), task.actions.index(('finalize',)))
        finalized.assert_called_once()
        self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                         self.integration.Clock.value)['completed_date'], '2026-10-05')

    def test_capture_exception_does_not_stop_reward_or_completion(self):
        self.case.queue()
        task = self.task()
        with patch.object(capture, 'capture_talisman', side_effect=RuntimeError('capture failed')):
            self.case.finish(task)
        self.assertIn(('collect',), task.actions)
        self.assertTrue(any(action[0] == 'level' for action in task.actions))
        self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                         self.integration.Clock.value)['completed_date'], '2026-10-05')

    def test_ordinary_talisman_does_not_finalize_or_request_final_notification(self):
        self.case.config.talisman_pass.closeout_config.enable = False
        task = self.task()
        captured = Mock(return_value=False)
        finalized = Mock()
        with patch.object(capture, 'capture_talisman', captured), patch.dict(
                sys.modules, {'tasks.Component.daily_feedback': S(finalize_feedback=finalized)}):
            self.case.finish(task)
        captured.assert_called_once_with(task, final=False)
        finalized.assert_not_called()

    def test_level_reward_exception_does_not_notify_final(self):
        self.case.queue()
        task = self.task()
        task.get_flower = Mock(side_effect=RuntimeError('level rewards failed'))
        finalized = Mock()
        with patch.object(capture, 'capture_talisman', return_value=False), patch.dict(
                sys.modules, {'tasks.Component.daily_feedback': S(finalize_feedback=finalized)}):
            with self.assertRaises(RuntimeError):
                task.run()
        finalized.assert_not_called()
        self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                         self.integration.Clock.value)['completed_date'], '')

    def test_consumed_exact_recapture_request_skips_all_reward_operations(self):
        self.case.queue()
        self.case.config.talisman_pass.talisman.harvest_soul = True
        task = self.task()
        task.goto_page = Mock(side_effect=AssertionError('Capture-only must not navigate with gameplay hooks'))
        task.in_task = Mock(side_effect=AssertionError('Capture-only must not call BaseTask screenshot'))
        scheduled_at = self.case.config.talisman_pass.scheduler.next_run
        consumed = Mock(return_value=True)
        captured = Mock(side_effect=lambda target, **kw: target.actions.append(('feedback', kw['final'])))
        revisited = Mock(side_effect=lambda target, **kw: target.actions.append(('guild_feedback',)))
        finalized = Mock(side_effect=lambda *args, **kw: task.actions.append(('finalize',)))
        request_finished = Mock(side_effect=lambda *args, **kw: task.actions.append(('request_finished',)))
        original_finish = task._finish_dynamic_run
        def finish_dynamic(*args, **kw):
            result = original_finish(*args, **kw)
            task.actions.append(('dynamic_finished',))
            return result
        task._finish_dynamic_run = finish_dynamic
        module = S(consume_recapture_request=consumed, finish_recapture_request=request_finished,
                   finalize_feedback=finalized)
        with patch.dict(sys.modules, {'tasks.Component.daily_feedback': module}), \
                patch.object(capture, 'capture_talisman', captured), \
                patch.object(capture, 'refresh_collective_feedback', revisited), \
                patch.object(capture, 'goto_talisman_readonly', return_value=True) as safe_goto:
            self.case.finish(task)
        consumed.assert_called_once_with(self.case.config, scheduled_at, now=self.integration.Clock.value)
        request_finished.assert_called_once_with(self.case.config, scheduled_at, now=self.integration.Clock.value)
        self.assertIn(('feedback', True), task.actions)
        self.assertIn(('guild_feedback',), task.actions)
        self.assertIn(('finalize',), task.actions)
        self.assertNotIn(('collect',), task.actions)
        self.assertFalse(any(action[0] in ('level', 'soul') for action in task.actions))
        self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                         self.integration.Clock.value)['completed_date'], '')
        finalized.assert_called_once()
        safe_goto.assert_called_once_with(task)
        task.goto_page.assert_not_called()
        task.in_task.assert_not_called()
        self.assertLess(task.actions.index(('guild_feedback',)), task.actions.index(('dynamic_finished',)))
        self.assertLess(task.actions.index(('dynamic_finished',)), task.actions.index(('request_finished',)))
        self.assertLess(task.actions.index(('request_finished',)), task.actions.index(('finalize',)))

    def test_recapture_with_unavailable_images_finishes_exact_attempt_before_finalize(self):
        self.case.queue()
        task = self.task()
        scheduled_at = self.case.config.talisman_pass.scheduler.next_run
        request_finished = Mock(side_effect=lambda *args, **kw: task.actions.append(('request_finished',)))
        finalized = Mock(side_effect=lambda *args, **kw: task.actions.append(('finalize',)))
        original_finish = task._finish_dynamic_run
        def finish_dynamic(*args, **kw):
            result = original_finish(*args, **kw)
            task.actions.append(('dynamic_finished',))
            return result
        task._finish_dynamic_run = finish_dynamic
        module = S(consume_recapture_request=Mock(return_value=True),
                   finish_recapture_request=request_finished, finalize_feedback=finalized)
        with patch.dict(sys.modules, {'tasks.Component.daily_feedback': module}), \
                patch.object(capture, 'goto_talisman_readonly', return_value=False), \
                patch.object(capture, 'capture_talisman', return_value=False) as flower, \
                patch.object(capture, 'refresh_collective_feedback', return_value=False) as guild:
            self.case.finish(task)
        flower.assert_called_once_with(task, final=True)
        guild.assert_called_once_with(task, expected_date=self.integration.Clock.value.date())
        request_finished.assert_called_once_with(self.case.config, scheduled_at, now=self.integration.Clock.value)
        finalized.assert_called_once()
        self.assertLess(task.actions.index(('dynamic_finished',)), task.actions.index(('request_finished',)))
        self.assertLess(task.actions.index(('request_finished',)), task.actions.index(('finalize',)))
        self.assertNotIn(('collect',), task.actions)

    def test_recapture_control_interruption_keeps_request_and_never_finalizes(self):
        self.case.queue()
        for method in ('goto_talisman_readonly', 'ensure_talisman_today', 'capture_talisman',
                       'refresh_collective_feedback', '_finish_dynamic_run'):
            with self.subTest(method=method):
                task = self.task()
                request_finished, finalized = Mock(), Mock()
                module = S(consume_recapture_request=Mock(return_value=True),
                           finish_recapture_request=request_finished, finalize_feedback=finalized)
                with patch.dict(sys.modules, {'tasks.Component.daily_feedback': module}), \
                        patch.object(capture, 'goto_talisman_readonly', return_value=True), \
                        patch.object(capture, 'capture_talisman', return_value=False), \
                        patch.object(capture, 'refresh_collective_feedback', return_value=False):
                    target = task if method == '_finish_dynamic_run' else capture
                    with patch.object(target, method, side_effect=AccountLoggedInElsewhere('interrupted')):
                        with self.assertRaises(AccountLoggedInElsewhere):
                            task.run()
                request_finished.assert_not_called()
                finalized.assert_not_called()
                self.assertNotIn(('collect',), task.actions)

    def test_without_consumed_recapture_request_auto_still_collects_rewards(self):
        self.case.queue()
        task = self.task()
        consumed = Mock(return_value=False)
        finalized = Mock()
        with patch.dict(sys.modules, {'tasks.Component.daily_feedback':
                        S(consume_recapture_request=consumed, finalize_feedback=finalized)}), \
                patch.object(capture, 'capture_talisman', return_value=False):
            self.case.finish(task)
        consumed.assert_called_once()
        self.assertIn(('collect',), task.actions)
        self.assertTrue(any(action[0] == 'level' for action in task.actions))
        self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                         self.integration.Clock.value)['completed_date'], '2026-10-05')

    def test_disabled_dynamic_mode_never_consumes_capture_request(self):
        self.case.config.talisman_pass.closeout_config.enable = False
        task = self.task()
        consumed = Mock(return_value=True)
        with patch.dict(sys.modules, {'tasks.Component.daily_feedback':
                        S(consume_recapture_request=consumed, finalize_feedback=Mock())}), \
                patch.object(capture, 'capture_talisman', return_value=False):
            self.case.finish(task)
        consumed.assert_not_called()
        self.assertIn(('collect',), task.actions)

    def test_feedback_ocr_failure_does_not_veto_original_reward_claim(self):
        self.case.queue()
        task = self.task()
        with patch.object(capture, 'ensure_talisman_today', return_value=False), \
                patch.object(capture, 'capture_talisman', return_value=False):
            self.case.finish(task)
        self.assertIn(('collect',), task.actions)
        self.assertTrue(any(action[0] == 'level' for action in task.actions))

    def test_top_level_feedback_wrappers_do_not_swallow_control_exceptions(self):
        self.case.queue()
        for method, error in (('ensure_talisman_today', AccountLoggedInElsewhere),
                              ('capture_talisman', GameStuckError),
                              ('refresh_collective_feedback', RequestHumanTakeover)):
            with self.subTest(method=method):
                task = self.task()
                with patch.object(capture, method, side_effect=error('fixture')):
                    with self.assertRaises(error):
                        task.run()
                self.assertEqual(self.integration.DC.closeout_state(self.case.config,
                                 self.integration.Clock.value)['completed_date'], '')


class RevisitTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.object(capture, '_enabled', return_value=True)
        self.enabled.start()

    def tearDown(self):
        self.enabled.stop()

    def task(self, enabled=True):
        return S(config=S(config_name='offline', collective_missions=S(scheduler=S(enable=enabled))),
                 device=S(image=np.zeros((720, 1280, 3), dtype=np.uint8), click_record=[], screenshot=Mock()),
                 goto_page=Mock(side_effect=AssertionError('Read-only revisit must not use navigator hooks')),
                 screenshot=Mock(side_effect=AssertionError('Do not execute BaseTask._burst')), appear=Mock(return_value=False),
                 appear_then_click=Mock(return_value=True), I_UI_REWARD='reward',
                 I_UI_BACK_RED='red', I_UI_BACK_YELLOW='yellow')

    def test_weekday_adapter_keeps_original_device_and_marks_final(self):
        task = self.task()
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 7, 23)
        with patch.object(capture, 'datetime', clock), patch.object(capture, '_goto_guild_readonly', return_value=True) as goto, \
                patch.object(capture, '_open_feedback_panel', return_value=True) as opened, \
                patch.object(capture, '_exit_feedback_panel', return_value=True) as exited, \
                patch.object(capture, 'capture_collective', return_value='proof') as collect:
            result = capture.refresh_collective_feedback(task, expected_date=datetime(2026, 10, 7).date())
        self.assertEqual(result, 'proof')
        self.assertEqual(opened.call_count, 2)
        adapter = collect.call_args.args[0]
        self.assertIs(adapter.device, task.device)
        self.assertIs(adapter.config, task.config)
        self.assertFalse(hasattr(adapter, 'screenshot'))
        self.assertIs(adapter.device.screenshot, task.device.screenshot)
        self.assertTrue(collect.call_args.kwargs['final'])
        exited.assert_called_once()
        goto.assert_called_once_with(task)
        task.goto_page.assert_not_called()
        task.screenshot.assert_not_called()

    def test_disabled_or_friday_to_sunday_never_opens_guild(self):
        for enabled, day in ((False, 7), (True, 9), (True, 10), (True, 11)):
            task = self.task(enabled)
            clock = Mock()
            clock.now.return_value = datetime(2026, 10, day, 23)
            with patch.object(capture, 'datetime', clock), patch.object(capture, '_failed') as failed:
                self.assertFalse(capture.refresh_collective_feedback(task))
            task.goto_page.assert_not_called()
            failed.assert_not_called()

    def test_crossed_day_does_not_open_or_prove_new_day(self):
        task = self.task()
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 8)
        with patch.object(capture, 'datetime', clock), patch.object(capture, '_failed', return_value=False):
            self.assertFalse(capture.refresh_collective_feedback(task, expected_date=datetime(2026, 10, 7).date()))
        task.goto_page.assert_not_called()

    def test_open_failure_still_exits_without_submission(self):
        task = self.task()
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 7, 23)
        with patch.object(capture, 'datetime', clock), patch.object(capture, '_goto_guild_readonly', return_value=True), \
                patch.object(capture, '_open_feedback_panel', return_value=False), \
                patch.object(capture, '_exit_feedback_panel', return_value=True) as exited, \
                patch.object(capture, '_failed', return_value=False) as failed, \
                patch.object(capture, 'capture_collective') as collect:
            self.assertFalse(capture.refresh_collective_feedback(task))
        collect.assert_not_called()
        exited.assert_called_once()
        self.assertTrue(failed.call_args.kwargs['final'])
        task.appear_then_click.assert_not_called()

    def test_open_attempts_are_bounded_and_only_click_entry(self):
        class Timer:
            def __init__(self, seconds):
                self.frames = 0

            def start(self):
                return self

            def reached(self):
                self.frames += 1
                return self.frames > 8

        task = self.task()
        task.appear.side_effect = lambda marker, **kw: marker == 'entry'
        with patch('module.base.timer.Timer', Timer):
            self.assertFalse(capture._open_feedback_panel(task, 'entry', 'destination'))
        self.assertEqual(task.appear_then_click.call_count, 3)
        for call in task.appear_then_click.call_args_list:
            self.assertEqual(call.args, ('entry',))
        self.assertLessEqual(task.device.screenshot.call_count, 4)
        task.screenshot.assert_not_called()

    def test_unsafe_starting_page_never_opens_or_cleans_up_panels(self):
        task = self.task()
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 7, 23)
        with patch.object(capture, 'datetime', clock), patch.object(capture, '_goto_guild_readonly', return_value=False), \
                patch.object(capture, '_open_feedback_panel') as opened, \
                patch.object(capture, '_exit_feedback_panel') as exited, \
                patch.object(capture, '_failed', return_value=False):
            self.assertFalse(capture.refresh_collective_feedback(task))
        opened.assert_not_called()
        exited.assert_not_called()
        task.goto_page.assert_not_called()

    def test_device_control_error_after_entering_does_not_click_cleanup(self):
        task = self.task()
        clock = Mock()
        clock.now.return_value = datetime(2026, 10, 7, 23)
        with patch.object(capture, 'datetime', clock), patch.object(capture, '_goto_guild_readonly', return_value=True), \
                patch.object(capture, '_open_feedback_panel', side_effect=AccountLoggedInElsewhere('fixture')), \
                patch.object(capture, '_exit_feedback_panel') as exited:
            with self.assertRaises(AccountLoggedInElsewhere):
                capture.refresh_collective_feedback(task)
        exited.assert_not_called()


class ReadOnlyNavigationTests(unittest.TestCase):
    class Timer:
        def __init__(self, seconds):
            self.frames = 0

        def start(self):
            return self

        def reached(self):
            self.frames += 1
            return self.frames > 12

    def setUp(self):
        self.patches = [patch.object(capture, '_enabled', return_value=True),
                        patch.object(capture, '_debug_talisman'),
                        patch('module.base.timer.Timer', self.Timer),
                        patch.dict(sys.modules, {'tasks.Component.daily_feedback_capture_assets':
                                                 S(DailyFeedbackCaptureAssets=FeedbackAssets)})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def task(self, state):
        task = S(config=S(config_name='offline'),
                 device=S(image=np.zeros((720, 1280, 3), dtype=np.uint8), screenshot=Mock(), click_record=[]),
                 screenshot=Mock(side_effect=AssertionError('BaseTask screenshot executes gameplay')),
                 goto_page=Mock(side_effect=AssertionError('Navigator enter hooks are forbidden')),
                 I_UI_REWARD='reward', I_TP_GOTO='goto', I_TP_EXP='exp',
                 I_CHECK_MAIN='main', I_CHECK_GUILD='guild', I_BACK_DAILY='back_daily',
                 I_MAIN_GOTO_GUILD='goto_guild', I_MAIN_GOTO_DAILY='goto_daily')
        task.visible = set(state)
        task.appear = Mock(side_effect=lambda marker, **kw: marker in task.visible)
        task.click = Mock(return_value=True)
        task.appear_then_click = Mock(return_value=True)
        return task

    def test_today_already_selected_does_not_ocr_or_click(self):
        task = self.task(('header', 'goto', 'today_selected'))
        with patch.object(capture, '_talisman_lines', side_effect=AssertionError('Navigation must not depend on OCR')):
            self.assertTrue(capture.ensure_talisman_today(task))
        task.click.assert_not_called()
        task.screenshot.assert_not_called()
        self.assertEqual(task.device.screenshot.call_count, 1)

    def test_weekly_tab_only_clicks_today_control_then_requires_selected_marker(self):
        task = self.task(('header', 'goto'))
        def select_today(marker, **kw):
            task.visible.add('today_selected')
            return True
        task.click.side_effect = select_today
        self.assertTrue(capture.ensure_talisman_today(task))
        task.click.assert_called_once_with('today_tab', interval=1.5)

    def test_disabled_navigation_does_not_read_device(self):
        task = self.task(('header', 'goto', 'today_selected'))
        with patch.object(capture, '_enabled', return_value=False):
            self.assertIsNone(capture.ensure_talisman_today(task))
        task.device.screenshot.assert_not_called()
        task.click.assert_not_called()

    def test_unknown_login_and_rewards_never_receive_navigation_input(self):
        for helper in (capture.ensure_talisman_today, capture.goto_talisman_readonly,
                       capture._goto_guild_readonly):
            for visible in ((), ('login',), ('reward', 'header', 'main')):
                with self.subTest(helper=helper.__name__, visible=visible):
                    task = self.task(visible)
                    self.assertFalse(helper(task))
                    task.click.assert_not_called()
                    task.appear_then_click.assert_not_called()
                    task.goto_page.assert_not_called()
                    task.screenshot.assert_not_called()

    def test_guild_route_only_closes_daily_and_enters_guild(self):
        task = self.task(('header', 'back_daily'))
        def advance(marker, **kwargs):
            task.visible = ({'main', 'goto_guild'} if marker == 'back_daily' else {'guild'})
            return True
        task.appear_then_click.side_effect = advance
        self.assertTrue(capture._goto_guild_readonly(task))
        self.assertEqual([call.args[0] for call in task.appear_then_click.call_args_list],
                         ['back_daily', 'goto_guild'])
        task.goto_page.assert_not_called()
        task.screenshot.assert_not_called()

    def test_talisman_route_only_enters_daily_from_positive_main(self):
        task = self.task(('main', 'goto_daily'))
        def advance(marker, **kwargs):
            task.visible = {'header'}
            return True
        task.appear_then_click.side_effect = advance
        self.assertTrue(capture.goto_talisman_readonly(task))
        task.appear_then_click.assert_called_once_with('goto_daily', interval=1.5)
        task.goto_page.assert_not_called()
        task.screenshot.assert_not_called()

    def test_failed_navigation_uses_at_most_three_inputs(self):
        for helper, visible in ((capture.goto_talisman_readonly, ('main', 'goto_daily')),
                                (capture._goto_guild_readonly, ('main', 'goto_guild'))):
            task = self.task(visible)
            self.assertFalse(helper(task))
            self.assertEqual(task.appear_then_click.call_count, 3)


if __name__ == '__main__':
    unittest.main()
