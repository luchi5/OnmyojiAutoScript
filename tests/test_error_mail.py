"""Offline only: all native credential, SMTP and child-launch APIs are blocked."""
import ast
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'error_mail.py' if (HERE / 'error_mail.py').exists() else HERE.parent / 'module' / 'notify' / 'error_mail.py'
spec = importlib.util.spec_from_file_location('oas_error_mail_test_target', SOURCE)
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)


class ErrorMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config' / 'error_mail').mkdir(parents=True)
        (self.root / 'tasks' / 'TestTask').mkdir(parents=True)
        self.settings = {'enabled': False, 'sender': mail.ADDRESS, 'recipient': mail.ADDRESS,
                         'credential_target': mail.CREDENTIAL_TARGET}
        self.save_settings()
        for target in ('ctypes.WinDLL', 'smtplib.SMTP_SSL', 'subprocess.run', 'socket.create_connection'):
            blocked = patch(target, side_effect=AssertionError('Offline API blocked'))
            blocked.start()
            self.addCleanup(blocked.stop)
        self.report = {'account': '01', 'task': 'TestTask', 'exception': 'ValueError', 'frames': []}

    def save_settings(self):
        (self.root / 'config' / 'error_mail' / 'settings.json').write_text(json.dumps(self.settings), encoding='utf-8')

    def enable(self):
        self.settings['enabled'] = True
        self.save_settings()

    def error(self, filename='tasks/TestTask/script_task.py', depth=0):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        # Distinct secret sentinels must never appear in any report or email.
        source = 'def fail():\n    local_token = "LOCAL_SECRET_SENTINEL"\n    raise ValueError("MESSAGE_SECRET_SENTINEL device=127.0.0.1:1234")\nfail()'
        try:
            exec(compile(source, str(path), 'exec'), {})
        except Exception as error:
            return error

    def test_disabled_has_no_side_effects(self):
        with patch.object(mail, '_read_password') as credential, patch.object(mail, '_reserve') as reserve:
            self.assertEqual(mail.notify_error_mail('01-person', 'TestTask', self.error(), self.root), 'disabled')
            self.assertEqual(mail._deliver(self.report, self.root), 'disabled')
            credential.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(list((self.root / 'config' / 'error_mail').iterdir()), [self.root / 'config' / 'error_mail' / 'settings.json'])

    def test_missing_settings_disabled(self):
        (self.root / 'config' / 'error_mail' / 'settings.json').unlink()
        self.assertFalse(mail._settings_enabled(self.root))

    def test_malformed_settings_disabled(self):
        (self.root / 'config' / 'error_mail' / 'settings.json').write_text('{', encoding='utf-8')
        self.assertFalse(mail._settings_enabled(self.root))

    def test_enabled_requires_literal_true(self):
        for value in ('true', 1, None):
            self.settings['enabled'] = value
            self.save_settings()
            self.assertFalse(mail._settings_enabled(self.root))

    def test_addresses_and_target_pinned(self):
        self.enable()
        for key in ('sender', 'recipient', 'credential_target'):
            with self.subTest(key=key):
                old = self.settings[key]
                self.settings[key] = 'other'
                self.save_settings()
                self.assertFalse(mail._settings_enabled(self.root))
                self.settings[key] = old

    def test_report_never_contains_secrets(self):
        report = mail.build_report('01-PRIVATE_ACCOUNT_NAME', 'TestTask', self.error(), self.root)
        text = json.dumps(report) + mail._message(report).as_string()
        for secret in ('LOCAL_SECRET_SENTINEL', 'MESSAGE_SECRET_SENTINEL', 'PRIVATE_ACCOUNT_NAME', '127.0.0.1', str(self.root)):
            self.assertNotIn(secret, text)
        self.assertEqual(report['account'], '01')
        self.assertEqual(report['frames'][-1]['file'], 'tasks/TestTask/script_task.py')
        self.assertEqual(set(report), {'account', 'task', 'exception', 'frames'})

    def test_source_lines_and_locals_not_accessed(self):
        error = self.error()
        with patch('linecache.getline', side_effect=AssertionError('no source')):
            self.assertTrue(mail._validate_report(mail.build_report('01', 'TestTask', error, self.root)))

    def test_external_frames_excluded(self):
        error = self.error('../outside_mail_test.py')
        self.assertEqual(mail.build_report('01', 'TestTask', error, self.root)['frames'], [])

    def test_account_names_never_exported(self):
        self.assertEqual(mail.build_report('02-name-token', 'TestTask', ValueError(), self.root)['account'], '02')
        for name in ('personal', '1-token', 'token-01', '01\nInjected'):
            self.assertIsNone(mail.build_report(name, 'TestTask', ValueError(), self.root))

    def test_task_header_injection_filtered(self):
        for task in ('TestTask\nBcc: other', 'SECRET_SENTINEL', '../../secret'):
            self.assertEqual(mail.build_report('01', task, ValueError(), self.root)['task'], 'Unknown')

    def test_initialization_supported(self):
        self.assertEqual(mail.build_report('01', 'Initialize', ValueError(), self.root)['task'], 'Initialize')

    def test_frame_limit(self):
        code = 'def recurse(n):\n    if n: return recurse(n-1)\n    raise ValueError("SECRET")\nrecurse(20)'
        try:
            exec(compile(code, str(self.root / 'script.py'), 'exec'), {})
        except Exception as error:
            report = mail.build_report('01', 'TestTask', error, self.root)
        self.assertEqual(len(report['frames']), mail.MAX_FRAMES)

    def test_invalid_reports_rejected(self):
        bad = [None, {}, dict(self.report, password='SECRET'), dict(self.report, account='01-name'),
               dict(self.report, task='Test\nBcc: other'), dict(self.report, exception='Type\rBad'),
               dict(self.report, frames=[{'file': '../secret.py', 'function': 'f', 'line': 1}]),
               dict(self.report, frames=[{'file': 'module/../secret.py', 'function': 'f', 'line': 1}]),
               dict(self.report, frames=[{'file': 'script.py', 'function': 'f', 'line': True}]),
               dict(self.report, frames=[{'file': 'script.py', 'function': 'f', 'line': 1, 'locals': 'SECRET'}])]
        for value in bad:
            with self.subTest(value=value):
                self.assertFalse(mail._validate_report(value))

    def test_message_has_fixed_recipient_no_attachments(self):
        msg = mail._message(self.report)
        self.assertEqual(msg['From'], mail.ADDRESS)
        self.assertEqual(msg['To'], mail.ADDRESS)
        self.assertTrue(msg['Subject'].startswith('[OAS-ERROR]'))
        self.assertEqual(list(msg.iter_attachments()), [])

    def test_dedup_and_account_limit(self):
        self.assertTrue(mail._reserve(self.report, self.root, 10000))
        self.assertFalse(mail._reserve(self.report, self.root, 10300))
        self.assertFalse(mail._reserve(dict(self.report, exception='RuntimeError'), self.root, 10010))
        self.assertTrue(mail._reserve(dict(self.report, account='02'), self.root, 10010))
        self.assertTrue(mail._reserve(self.report, self.root, 10901))

    def test_global_hourly_limit(self):
        for number in range(mail.HOURLY_LIMIT):
            self.assertTrue(mail._reserve(dict(self.report, account=f'{number:02}'), self.root, 10000))
        self.assertFalse(mail._reserve(dict(self.report, account='99'), self.root, 10001))
        self.assertTrue(mail._reserve(dict(self.report, account='99'), self.root, 13601))

    def test_state_contains_no_report_body(self):
        mail._reserve(self.report, self.root, 10000)
        state = (self.root / 'config' / 'error_mail' / 'rate_state.json').read_text()
        self.assertNotIn('TestTask', state)
        self.assertNotIn('ValueError', state)
        self.assertEqual(set(json.loads(state)[0]), {'time', 'account', 'fingerprint'})

    def test_corrupt_state_fails_closed(self):
        self.enable()
        (self.root / 'config' / 'error_mail' / 'rate_state.json').write_text('invalid')
        with patch.object(mail, '_read_password') as credential:
            self.assertEqual(mail._deliver(self.report, self.root), 'failed')
            credential.assert_not_called()

    def test_child_timeout_isolated(self):
        self.enable()
        with patch.object(mail.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test', 15)) as run:
            self.assertEqual(mail.notify_error_mail('01-name', 'TestTask', self.error(), self.root), 'failed')
            self.assertEqual(run.call_args.kwargs['timeout'], mail.DELIVERY_TIMEOUT)

    def test_child_only_receives_safe_payload(self):
        self.enable()
        with patch.object(mail.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(mail.notify_error_mail('01-PRIVATE_ACCOUNT_NAME', 'TestTask', self.error(), self.root), 'sent')
            args = run.call_args
            self.assertNotIn('password', str(args))
            self.assertNotIn('SECRET_SENTINEL', str(args))
            self.assertNotIn('PRIVATE_ACCOUNT_NAME', str(args))
            self.assertTrue(mail._validate_report(json.loads(args.kwargs['input'])))

    def test_child_failure_isolated(self):
        self.enable()
        for status in (1, -1, 999):
            with patch.object(mail.subprocess, 'run', return_value=Mock(returncode=status)):
                self.assertEqual(mail.notify_error_mail('01', 'TestTask', self.error(), self.root), 'failed')

    def test_delivery_uses_mock_smtp_only(self):
        self.enable()
        client = Mock()
        smtp = Mock()
        smtp.__enter__ = Mock(return_value=client)
        smtp.__exit__ = Mock(return_value=False)
        with patch.object(mail, '_read_password', return_value='FAKEPASSWORDONLYX'), patch.object(mail.smtplib, 'SMTP_SSL', return_value=smtp) as factory:
            self.assertEqual(mail._deliver(self.report, self.root), 'sent')
            self.assertEqual(factory.call_args.args, ('smtp.gmail.com', 465))
            self.assertEqual(factory.call_args.kwargs['timeout'], mail.SMTP_TIMEOUT)
            self.assertEqual(client.send_message.call_args.kwargs['to_addrs'], [mail.ADDRESS])

    def test_delivery_failure_isolated_and_limited(self):
        self.enable()
        with patch.object(mail, '_read_password', side_effect=RuntimeError('SECRET')):
            self.assertEqual(mail._deliver(self.report, self.root), 'failed')
            self.assertEqual(mail._deliver(self.report, self.root), 'skipped')

    def test_invalid_report_does_not_read_credential(self):
        self.enable()
        with patch.object(mail, '_read_password') as credential:
            self.assertEqual(mail._deliver(dict(self.report, token='SECRET'), self.root), 'failed')
            credential.assert_not_called()

    def mark_saved(self):
        (self.root / 'config' / 'error_mail' / 'setup_status.json').write_text('{"status":"saved"}', encoding='utf-8-sig')

    def test_setup_test_waits_without_reading_credentials(self):
        with patch.object(mail, '_read_password') as credential:
            self.assertEqual(mail.run_test_once(self.root), 'awaiting_local_save')
            credential.assert_not_called()
        self.assertFalse((self.root / 'config' / 'error_mail' / 'test_status.json').exists())

    def test_setup_test_claims_only_one_attempt(self):
        self.mark_saved()
        with patch.object(mail.subprocess, 'run', side_effect=RuntimeError('offline')) as run:
            self.assertEqual(mail.run_test_once(self.root), 'failed_before_send')
            self.assertEqual(mail.run_test_once(self.root), 'already_attempted_no_retry')
            self.assertEqual(run.call_count, 1)

    def test_setup_test_timeout_after_submission_is_uncertain(self):
        self.mark_saved()
        def timeout(*args, **kwargs):
            mail._test_status(self.root, 'submitting')
            raise subprocess.TimeoutExpired('test', 15)
        with patch.object(mail.subprocess, 'run', side_effect=timeout):
            self.assertEqual(mail.run_test_once(self.root), 'uncertain')
            self.assertEqual(mail.run_test_once(self.root), 'already_attempted_no_retry')

    def test_setup_test_preserves_accepted_after_child_timeout(self):
        self.mark_saved()
        def timeout(*args, **kwargs):
            mail._test_status(self.root, 'accepted')
            raise subprocess.TimeoutExpired('test', 15)
        with patch.object(mail.subprocess, 'run', side_effect=timeout):
            self.assertEqual(mail.run_test_once(self.root), 'accepted')

    def test_setup_worker_only_synthetic_message(self):
        self.mark_saved()
        mail._test_status(self.root, 'starting')
        client = Mock()
        client.send_message.return_value = {}
        with patch.object(mail, '_read_password', return_value='FAKEPASSWORDONLYX'), patch.object(mail.smtplib, 'SMTP_SSL', return_value=client):
            self.assertEqual(mail._test_worker(self.root), 'accepted')
            msg = client.send_message.call_args.args[0]
            self.assertTrue(msg['Subject'].startswith('[OAS-ERROR] TEST'))
            self.assertEqual(msg['To'], mail.ADDRESS)
            self.assertEqual(list(msg.iter_attachments()), [])
            self.assertNotIn('FAKEPASSWORDONLYX', msg.as_string())
            self.assertFalse(mail._settings_enabled(self.root))

    def test_setup_worker_error_never_retried(self):
        self.mark_saved()
        mail._test_status(self.root, 'starting')
        client = Mock()
        client.send_message.side_effect = RuntimeError('SECRET_SERVER_RESPONSE')
        with patch.object(mail, '_read_password', return_value='FAKEPASSWORDONLYX'), patch.object(mail.smtplib, 'SMTP_SSL', return_value=client):
            self.assertEqual(mail._test_worker(self.root), 'uncertain')
            self.assertEqual(mail._test_status(self.root), 'uncertain')
            self.assertEqual(mail._test_worker(self.root), 'failed_before_send')
            self.assertEqual(client.send_message.call_count, 1)
            self.assertNotIn('SECRET', (self.root / 'config' / 'error_mail' / 'test_status.json').read_text())

    def test_existing_script_semantics_preserved(self):
        old_file = HERE / 'script.original.py'
        if not old_file.exists():
            self.skipTest('Pre-change snapshot is kept in staging only')
        old = ast.parse(old_file.read_text(encoding='utf-8'))
        new = ast.parse((HERE / 'script.py').read_text(encoding='utf-8'))
        class Strip(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                return None if node.name == '_notify_error_email' else self.generic_visit(node)
            def visit_Expr(self, node):
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == '_notify_error_email':
                    return None
                return self.generic_visit(node)
            def visit_ExceptHandler(self, node):
                if node.name == 'error' and isinstance(node.type, ast.Name) and node.type.id == 'RequestHumanTakeover':
                    # Only the config property originally omitted the binding.
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Call) and isinstance(node.body[0].value.func, ast.Attribute) and node.body[0].value.func.attr == '_notify_error_email':
                        call = node.body[0].value
                        if isinstance(call.args[0], ast.Constant) and call.args[0].value == 'Initialize' and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'exit' for n in ast.walk(node)):
                            node.name = None
                return self.generic_visit(node)
        self.assertEqual(ast.dump(old), ast.dump(Strip().visit(new)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
