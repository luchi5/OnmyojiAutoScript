"""Offline feedback persistence/notification checks using isolated temp files.

No game devices, production options, or network notifications are used.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace as S
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import cv2
import numpy as np

from tasks.Component import daily_feedback as DF
from tasks.Component import daily_closeout as DC


class DailyFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oas-feedback-test-')
        self.root = Path(self.temp.name)
        self.config = self.make_config('demo-account-005')
        self.now = datetime(2026, 10, 7, 22, 30, 45, 123456)
        self.patches = [
            patch.object(DF, 'ROOT', self.root),
            patch.object(DF, 'STATE_DIR', self.root / 'config' / 'daily_feedback'),
            patch.object(DF, 'OPTIONS_PATH', self.root / 'config' / 'daily_feedback' / 'settings.json'),
            patch.object(DF, '_warn'),
            patch.object(DF, '_push', return_value=True),
            patch.object(DC, 'closeout_state', return_value={'completed_date': self.now.date().isoformat()}),
            patch.object(DC, 'closeout_summary', return_value={
                'completed': [{'task': 'Dokan', 'label': '道馆'}],
                'incomplete': [{'task': 'CollectiveMissions', 'label': '寮集体任务', 'outcome': 'unconfirmed'}],
                'reason': 'fallback'}),
            patch.object(DC, 'closeout_dependencies', return_value=('Dokan', 'CollectiveMissions')),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.push = self.mocks[4]
        self.state = self.mocks[5]
        self.options()
        self.image = np.zeros((120, 160, 3), dtype=np.uint8)
        self.image[0:40, :, :] = (241, 7, 33)
        self.image[40:80, :, :] = (17, 222, 5)
        self.image[80:120, :, :] = (3, 21, 230)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    @staticmethod
    def make_config(name):
        return S(config_name=name,
                 talisman_pass=S(scheduler=S(enable=True)),
                 collective_missions=S(scheduler=S(enable=True),
                     missions_config=S(monday_to_thursday=True, run_after_dokan=True)))

    def options(self, **changes):
        options = {'enable': True, 'notify': True, 'accounts': [self.config.config_name],
                   'public_origin': DF.PUBLIC_ORIGIN}
        options.update(changes)
        DF.OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        DF.OPTIONS_PATH.write_text(json.dumps(options, ensure_ascii=False), encoding='utf-8')

    def directory(self, config=None, now=None):
        return DF._directory(config or self.config, now or self.now)

    def report(self, config=None, now=None):
        return DF._load(self.directory(config, now), config or self.config, now or self.now)

    def save(self, kind='talisman', **kwargs):
        data = {'image': self.image, 'current': 100, 'total': 100, 'verified': True,
                'now': self.now}
        if kind == 'collective':
            data.update(current=30, total=30)
        data.update(kwargs)
        return DF.save_evidence(self.config, kind, **data)

    def finish(self, now=None):
        return DF.finalize_feedback(self.config, now=now or self.now)

    def test_counter_normalization_accepts_only_reliable_numbers(self):
        self.assertEqual(DF._valid_counter('talisman', 100, 100, True), (100, 100, True))
        self.assertEqual(DF._valid_counter('talisman', 90, None, True), (90, None, True))
        self.assertEqual(DF._valid_counter('collective', 25, 30, True), (25, 30, True))

    def test_counter_unknown_never_becomes_zero(self):
        for current, total, verified in [(None, 30, False), (0, 30, False),
                                         ('0', 30, True), (False, 30, True)]:
            with self.subTest(current=current, total=total, verified=verified):
                self.assertEqual(DF._valid_counter('collective', current, total, verified),
                                 (None, None, False))

    def test_legitimately_verified_zero_is_preserved(self):
        self.assertEqual(DF._valid_counter('collective', 0, 30, True), (0, 30, True))

    def test_counter_rejects_ranges_types_and_wrong_collective_total(self):
        cases = [('talisman', -1, 100, True), ('talisman', 301, None, True),
                 ('talisman', 101, 100, True), ('talisman', 1, 0, True),
                 ('talisman', 1, 301, True), ('talisman', 1, True, True),
                 ('talisman', 1.0, 100, True), ('talisman', 1, '100', True),
                 ('talisman', 1, 100, 1), ('collective', 20, None, True),
                 ('collective', 20, 100, True)]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(DF._valid_counter(*case), (None, None, False))

    def test_saved_png_preserves_rgb_pixels_and_metadata(self):
        result = self.save()
        self.assertEqual(result['current'], 100)
        self.assertTrue(result['verified'])
        pixels = cv2.imread(str(self.directory() / result['image']))
        self.assertTrue(np.array_equal(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB), self.image))
        self.assertEqual(self.report()['evidence']['talisman'], result)

    def test_unknown_number_keeps_real_image_without_claiming_verification(self):
        result = self.save(current=None, total=None, verified=False)
        self.assertEqual(result['status'], 'captured')
        self.assertTrue((self.directory() / result['image']).is_file())
        self.assertIsNone(result['current'])
        self.assertFalse(result['verified'])

    def test_generated_directory_is_account_hash_under_date(self):
        expected = hashlib.sha256(self.config.config_name.encode('utf-8')).hexdigest()[:16]
        self.assertEqual(self.directory(), DF.STATE_DIR / '2026-10-07' / expected)
        self.assertNotIn(self.config.config_name, str(self.directory()))

    def test_accounts_cannot_read_each_others_evidence(self):
        other = self.make_config('demo-account-006')
        self.options(accounts=[self.config.config_name, other.config_name])
        self.save()
        self.assertNotEqual(self.directory(), self.directory(other))
        self.assertEqual(self.report(other)['evidence'], {})
        DF.save_evidence(other, 'collective', self.image, 20, 30, True, now=self.now)
        self.assertEqual(set(self.report()['evidence']), {'talisman'})
        self.assertEqual(set(self.report(other)['evidence']), {'collective'})

    def test_date_rollover_cannot_reuse_yesterdays_evidence(self):
        self.save()
        tomorrow = self.now + timedelta(days=1)
        self.assertNotEqual(self.directory(), self.directory(now=tomorrow))
        self.assertEqual(self.report(now=tomorrow)['evidence'], {})

    def test_unsafe_account_names_are_rejected(self):
        for name in ['', '../05', 'C:\\05', '05/account', '05 trailing ', '05.', 'a' * 81]:
            with self.subTest(name=name):
                config = self.make_config(name)
                self.options(accounts=[name])
                self.assertFalse(DF.save_evidence(config, 'talisman', self.image, now=self.now))

    def test_missing_capture_keeps_old_image_time_and_does_not_promote_final(self):
        old = self.save(final=False)
        later = self.now + timedelta(minutes=5)
        failed = self.save(image=None, current=None, total=None, verified=False,
                           final=True, detail='page not confirmed', now=later)
        for field in ('image', 'captured_at', 'current', 'total', 'verified'):
            self.assertEqual(failed[field], old[field])
        self.assertFalse(failed['final'])
        self.assertEqual(failed['capture_error'], 'page not confirmed')
        self.assertEqual(failed['last_attempt_at'], later.isoformat(timespec='seconds'))
        self.assertTrue((self.directory() / old['image']).is_file())

    def test_first_failed_capture_is_unavailable_not_zero(self):
        failed = self.save(image=None, final=True, detail='no image')
        self.assertEqual(failed['status'], 'unavailable')
        self.assertIsNone(failed['image'])
        self.assertIsNone(failed['current'])
        self.assertFalse(failed['verified'])
        self.assertFalse(failed['final'])

    def test_invalid_image_does_not_overwrite_old_record(self):
        old = self.save()
        for bad in [np.zeros((20, 20, 3), dtype=np.uint8),
                    np.zeros((120, 160, 4), dtype=np.uint8),
                    np.zeros((120, 160, 3), dtype=float), 'not pixels']:
            with self.subTest(type=type(bad).__name__):
                self.assertFalse(self.save(image=bad))
                self.assertEqual(self.report()['evidence']['talisman'], old)

    def test_finalize_requires_this_days_closeout_completion(self):
        self.save()
        for state in [{}, {'completed_date': '2026-10-06'},
                      {'completed_date': '2026-10-08'},
                      {'completed_date': '2026-10-07', 'unavailable': True}]:
            with self.subTest(state=state):
                self.state.return_value = state
                self.assertFalse(self.finish())
                self.assertEqual(self.report()['closeout']['phase'], 'provisional')
        self.push.assert_not_called()

    def test_finalize_stores_incomplete_proof_without_claiming_all_completed(self):
        self.save()
        self.assertTrue(self.finish())
        closeout = self.report()['closeout']
        self.assertEqual(closeout['phase'], 'final')
        self.assertEqual(closeout['required'], ['Dokan', 'CollectiveMissions'])
        self.assertEqual(closeout['waiting'], ['CollectiveMissions'])
        self.assertEqual(closeout['incomplete'][0]['outcome'], 'unconfirmed')
        self.assertEqual(len(closeout['completed']), 1)
        self.assertIn('需关注：寮集体任务', self.push.call_args.args[2])

    def test_without_final_report_never_notifies(self):
        self.save()
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now))
        self.push.assert_not_called()

    def test_notification_accepted_is_idempotent_even_after_refinalization(self):
        self.save()
        self.assertTrue(self.finish())
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(minutes=10)))
        self.assertTrue(self.finish(now=self.now + timedelta(minutes=20)))
        self.assertEqual(self.push.call_count, 1)
        self.assertEqual(self.report()['notification']['status'], 'accepted')
        self.assertEqual(self.report()['notification']['attempts'], 1)

    def test_notification_failures_have_three_minute_backoff_and_three_attempt_limit(self):
        self.push.return_value = False
        self.assertTrue(self.finish())
        self.assertEqual(self.report()['notification']['attempts'], 1)
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(seconds=179)))
        self.assertEqual(self.push.call_count, 1)
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(minutes=3)))
        self.assertEqual(self.push.call_count, 2)
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(minutes=6)))
        self.assertEqual(self.push.call_count, 3)
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(hours=1)))
        self.assertEqual(self.push.call_count, 3)
        self.assertEqual(self.report()['notification'], {
            'status': 'failed', 'at': (self.now + timedelta(minutes=6)).isoformat(timespec='seconds'),
            'attempts': 3})

    def test_notification_exception_is_bounded_failure_not_game_error(self):
        self.push.side_effect = RuntimeError('offline provider failure')
        self.assertTrue(self.finish())
        self.assertEqual(self.report()['notification']['status'], 'failed')
        self.assertEqual(self.report()['notification']['attempts'], 1)

    def test_notification_provider_disabled_does_not_repeatedly_attempt(self):
        self.push.return_value = None
        self.assertTrue(self.finish())
        self.assertEqual(self.report()['notification']['status'], 'disabled')
        self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now + timedelta(minutes=10)))
        self.assertEqual(self.push.call_count, 1)

    def test_global_notification_switch_prevents_provider_call(self):
        self.options(notify=False)
        self.assertTrue(self.finish())
        self.push.assert_not_called()
        self.assertEqual(self.report()['notification']['status'], 'disabled')
        self.assertEqual(self.report()['notification']['attempts'], 0)

    def test_disabled_feedback_never_writes_or_notifies(self):
        for changes in [{'enable': False}, {'enable': 1}, {'accounts': []}, {'accounts': '05'}]:
            with self.subTest(changes=changes):
                self.options(**changes)
                self.assertFalse(self.save())
                self.assertFalse(self.finish())
                self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now))
        self.assertFalse((DF.STATE_DIR / self.now.date().isoformat()).exists())
        self.push.assert_not_called()

    def test_notification_url_has_only_account_and_day_no_push_secret(self):
        self.config.private_token = 'secret-must-never-appear'
        self.save()
        self.assertTrue(self.finish())
        url = self.push.call_args.args[3]
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme, urlsplit(DF.PUBLIC_ORIGIN).scheme)
        self.assertEqual(parsed.netloc, urlsplit(DF.PUBLIC_ORIGIN).netloc)
        self.assertEqual(parsed.path, '/daily-feedback')
        self.assertEqual(parse_qs(parsed.query), {
            'account': [self.config.config_name], 'date': ['2026-10-07']})
        self.assertNotIn(self.config.private_token, url)
        self.assertNotIn(self.config.private_token, self.push.call_args.args[2])

    def test_invalid_public_origin_cannot_insert_credentials_or_paths(self):
        for origin in ['ftp://feedback.example.invalid', 'https://feedback.example.invalid:bad',
                       'https://feedback.example.invalid/secret-path',
                       'https://feedback.example.invalid?token=secret',
                       'https://user:secret@feedback.example.invalid',
                       'https://feedback.example.invalid#fragment',
                       'https://feedback.example.invalid\n', 'https://[broken',
                       'https://feedback.example.invalid:0', 'https://feedback.example.invalid:65536',
                       'https://@feedback.example.invalid', 'https://feedback.example.invalid:']:
            with self.subTest(origin=origin):
                self.options(public_origin=origin)
                url = DF._feedback_url(self.config, '2026-10-07')
                self.assertTrue(url.startswith(DF.PUBLIC_ORIGIN + '/daily-feedback?'))
                self.assertNotIn('secret', url)

    def test_public_origin_can_be_configured_for_the_operator(self):
        with patch.dict(DF.os.environ, {'OAS_PUBLIC_FEEDBACK_ORIGIN': 'https://feedback.example.invalid'}):
            self.options(public_origin=None)
            url = DF._feedback_url(self.config, '2026-10-07')
            self.assertEqual(urlsplit(url).netloc, 'feedback.example.invalid')
            self.assertEqual(urlsplit(url).scheme, 'https')
            self.assertEqual(parse_qs(urlsplit(url).query)['account'], [self.config.config_name])

    def test_local_settings_origin_takes_precedence_over_environment(self):
        with patch.dict(DF.os.environ, {'OAS_PUBLIC_FEEDBACK_ORIGIN': 'https://fallback.example.invalid'}):
            self.options(public_origin='https://configured.example.invalid:8443/')
            self.assertEqual(urlsplit(DF._feedback_url(self.config, '2026-10-07')).netloc,
                             'configured.example.invalid:8443')

    def test_invalid_configured_origin_uses_the_local_default(self):
        for origin in ('https://user:password@feedback.example.invalid',
                       'https://feedback.example.invalid/private',
                       'https://feedback.example.invalid:bad',
                       'https://feedback.example.invalid?secret=example'):
            with self.subTest(origin=origin), patch.dict(DF.os.environ, {'OAS_PUBLIC_FEEDBACK_ORIGIN': origin}):
                self.assertTrue(DF._feedback_url(self.config, '2026-10-07').startswith(DF.PUBLIC_ORIGIN + '/'))

    def test_concurrent_kind_writes_keep_both_metadata_entries(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.save, kind) for kind in ('talisman', 'collective')]
            results = [job.result(timeout=10) for job in jobs]
        self.assertTrue(all(isinstance(item, dict) for item in results))
        report = self.report()
        self.assertEqual(set(report['evidence']), {'talisman', 'collective'})
        for kind in DF.KINDS:
            self.assertTrue((self.directory() / report['evidence'][kind]['image']).is_file())

    def test_provider_result_save_preserves_evidence_written_during_request(self):
        def provider(*args):
            self.save('collective')
            return True
        self.push.side_effect = provider
        self.save()
        self.assertTrue(self.finish())
        report = self.report()
        self.assertEqual(set(report['evidence']), {'talisman', 'collective'})
        self.assertEqual(report['notification']['status'], 'accepted')

    def test_two_concurrent_notification_checks_send_only_one_request(self):
        # Persist a final report with notification disabled, then re-arm only
        # the local temporary notification record for this concurrency check.
        self.options(notify=False)
        self.assertTrue(self.finish())
        self.options()
        report = self.report()
        report['notification'] = {'status': 'pending', 'attempts': 0}
        DF._write(self.directory(), report, self.now)
        entered, release = Event(), Event()
        def provider(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test provider timeout')
            return True
        self.push.side_effect = provider
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(DF.maybe_notify_feedback, self.config, self.now)
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(DF.maybe_notify_feedback, self.config, self.now)
                self.assertFalse(second.result(timeout=5))
            finally:
                release.set()
            self.assertTrue(first.result(timeout=5))
        self.assertEqual(self.push.call_count, 1)
        self.assertEqual(self.report()['notification']['status'], 'accepted')

    def test_corrupted_or_wrong_owner_report_never_becomes_new_success(self):
        directory = self.directory()
        path = directory / 'report.json'
        for value in ['{', json.dumps({'schema_version': 1, 'account': 'other',
                                      'date': '2026-10-07', 'evidence': {}})]:
            with self.subTest(value=value):
                path.write_text(value, encoding='utf-8')
                self.assertFalse(self.save(image=None))
                self.assertFalse(self.finish())
                self.assertFalse(DF.maybe_notify_feedback(self.config, now=self.now))
                self.assertEqual(path.read_text(encoding='utf-8'), value)
        self.push.assert_not_called()

    def test_weekend_collective_not_required_when_config_is_weekday_linked(self):
        report = {'date': '2026-10-10', 'evidence': {}, 'closeout': {}}
        content = DF._notification_content(self.config, report)
        self.assertIn('花合战今日经验：未获取反馈图', content)
        self.assertNotIn('寮集体任务：', content)

    def test_disabled_task_is_not_reported_as_missing_screenshot(self):
        self.config.collective_missions.scheduler.enable = False
        content = DF._notification_content(self.config, {'date': '2026-10-07', 'evidence': {}})
        self.assertIn('花合战今日经验：未获取反馈图', content)
        self.assertNotIn('寮集体任务：', content)

    def test_daily_experience_can_exceed_chest_target_without_wrong_denominator(self):
        result = self.save(current=113, total=None, target=100)
        self.assertTrue(result['verified'])
        self.assertEqual(result['current'], 113)
        self.assertIsNone(result['total'])
        self.assertEqual(result['target'], 100)
        content = DF._notification_content(self.config, self.report())
        self.assertIn('113（目标100）', content)
        self.assertNotIn('113/100', content)

    def test_target_is_never_guessed_from_invalid_or_unverified_values(self):
        for target in (None, 0, True, '100', 301):
            self.assertIsNone(self.save(current=113, total=None, target=target)['target'])
        self.assertIsNone(self.save(current=None, total=None, verified=False, target=100)['target'])

    def recapture_marker(self, at):
        report = self.report()
        report['capture_only_next_run'] = at.isoformat()
        DF._write(self.directory(), report, self.now)

    def test_exact_recapture_survives_restart_and_finishes_explicitly(self):
        at = self.now - timedelta(seconds=5)
        self.recapture_marker(at)
        self.assertTrue(DF.consume_recapture_request(self.config, at, now=self.now))
        self.assertTrue(DF.consume_recapture_request(self.config, at, now=self.now + timedelta(seconds=20)))
        self.assertEqual(self.report()['capture_only_next_run'], at.isoformat())
        self.assertFalse(DF.finish_recapture_request(self.config, at + timedelta(seconds=1), now=self.now))
        self.assertTrue(DF.finish_recapture_request(self.config, at, now=self.now))
        self.assertFalse(DF.consume_recapture_request(self.config, at, now=self.now))

    def test_normal_flash_and_stale_or_future_requests_do_not_skip_claims(self):
        self.assertFalse(DF.consume_recapture_request(self.config, self.now, now=self.now))
        for at in (self.now + timedelta(seconds=1), self.now - timedelta(minutes=16), self.now - timedelta(days=1)):
            self.recapture_marker(at)
            self.assertFalse(DF.consume_recapture_request(self.config, at, now=self.now))
            self.assertEqual(self.report()['capture_only_next_run'], at.isoformat())
        self.recapture_marker(self.now)
        self.assertFalse(DF.consume_recapture_request(self.config, self.now - timedelta(seconds=1), now=self.now))

    def test_notification_waits_for_inflight_recapture(self):
        self.recapture_marker(self.now)
        self.assertTrue(self.finish())
        self.push.assert_not_called()
        self.assertTrue(DF.finish_recapture_request(self.config, self.now, now=self.now))
        self.assertTrue(DF.maybe_notify_feedback(self.config, now=self.now))
        self.push.assert_called_once()

    def test_fresh_full_collective_picture_resolves_old_unconfirmed_notice(self):
        self.save('collective', final=True)
        self.finish()
        content = self.push.call_args.args[2]
        self.assertIn('寮集体任务：30/30', content)
        self.assertNotIn('寮集体任务（未核验）', content)


if __name__ == '__main__':
    unittest.main()
