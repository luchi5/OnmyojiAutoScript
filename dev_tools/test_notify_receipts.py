"""Offline notification receipt checks: no HTTP, credentials, or OAS imports."""
import ast
import json
from pathlib import Path
from smtplib import SMTPResponseException
from types import SimpleNamespace
import unittest

from requests import Response
import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'module/notify/notify.py'
SECRET = 'offline-test-secret-must-not-appear-in-logs'


class CaptureLogger:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        return lambda message, *args, **kwargs: self.messages.append((name, str(message)))

    @property
    def text(self):
        return '\n'.join(message for _, message in self.messages)


class FakeOnePushException(Exception):
    pass


class FakeCustom:
    pass


class FakeProvider:
    params = {'required': ['token', 'content']}

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append(kwargs.copy())
        if self.error is not None:
            raise self.error
        return self.response


def load_classes(provider, logger):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    namespace = {
        'logger': logger, 'yaml': yaml, 'get_notifier': lambda name: provider,
        'Provider': FakeProvider, 'Custom': FakeCustom, 'Response': Response,
        'SMTPResponseException': SMTPResponseException,
        'OnePushException': FakeOnePushException,
    }
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return SimpleNamespace(**namespace)


def response(payload=None, status=200, raw=None):
    result = Response()
    result.status_code = status
    result.url = 'https://example.invalid/send?token=' + SECRET
    result._content = (raw if raw is not None else json.dumps(payload)).encode('utf-8')
    return result


class NotifyReceiptTests(unittest.TestCase):
    def make(self, provider_name='pushplus', result=None, error=None, enabled=True):
        logger = CaptureLogger()
        provider = FakeProvider(result, error)
        classes = load_classes(provider, logger)
        notifier = classes.Notifier(yaml.safe_dump({'provider': provider_name, 'token': SECRET}), enabled)
        notifier.config_name = '02'
        return notifier, provider, logger

    def send(self, notifier):
        return notifier.push(title='Test title', content='Test content')

    def assert_private(self, logger):
        self.assertNotIn(SECRET, logger.text)
        self.assertNotIn('example.invalid', logger.text)

    def test_pushplus_200_is_accepted_without_claiming_delivery(self):
        notifier, provider, logger = self.make(result=response({'code': 200, 'data': SECRET}))
        self.assertTrue(self.send(notifier))
        self.assertEqual(len(provider.calls), 1)
        self.assertIn('请求已受理，最终送达未确认', logger.text)
        self.assertNotIn('Push notify success', logger.text)
        self.assert_private(logger)

    def test_pushplus_business_rejections(self):
        for code in (900, 903, 905):
            with self.subTest(code=code):
                notifier, _, logger = self.make(result=response({'code': code, 'msg': SECRET}))
                self.assertFalse(self.send(notifier))
                self.assertIn('code=' + str(code), logger.text)
                self.assert_private(logger)

    def test_bark_accepted_receipt_does_not_claim_phone_delivery(self):
        notifier, _, logger = self.make('bark', result=response({'code': 200, 'message': 'success'}))
        self.assertTrue(self.send(notifier))
        self.assertIn('Bark 请求已受理，最终送达未确认', logger.text)
        self.assertNotIn('Push notify success', logger.text)
        self.assert_private(logger)

    def test_bark_rejects_invalid_or_failed_receipts(self):
        for result in (None, response({'code': 400, 'message': SECRET}),
                       response({'code': 500, 'message': SECRET}), response({'code': '200'}),
                       response({}), response(raw='<html>' + SECRET), {'code': 200}):
            with self.subTest(result_type=type(result).__name__):
                notifier, _, logger = self.make('bark', result=result)
                self.assertFalse(self.send(notifier))
                self.assertNotIn('Push notify success', logger.text)
                self.assert_private(logger)

    def test_pushplus_invalid_json(self):
        notifier, _, logger = self.make(result=response(raw='<html>' + SECRET + '</html>'))
        self.assertFalse(self.send(notifier))
        self.assertNotIn('Push notify success', logger.text)
        self.assert_private(logger)

    def test_pushplus_requires_a_json_object(self):
        for payload in ([], None, 200, SECRET):
            with self.subTest(payload_type=type(payload).__name__):
                notifier, _, logger = self.make(result=response(payload))
                self.assertFalse(self.send(notifier))
                self.assert_private(logger)

    def test_pushplus_requires_an_integer_success_code(self):
        for payload in ({}, {'code': False}, {'code': True}, {'code': '200'}, {'code': 200.0}, {'code': None}):
            with self.subTest(payload=payload):
                notifier, _, logger = self.make(result=response(payload))
                self.assertFalse(self.send(notifier))
                self.assert_private(logger)

    def test_pushplus_rejects_non_http_receipts(self):
        for receipt in (True, {'code': 200}, 'OK', 200):
            with self.subTest(receipt_type=type(receipt).__name__):
                notifier, _, logger = self.make(result=receipt)
                self.assertFalse(self.send(notifier))
                self.assert_private(logger)

    def test_no_response_is_failure_for_all_providers(self):
        for name in ('pushplus', 'smtp', 'gocqhttp', 'bark'):
            with self.subTest(provider=name):
                notifier, _, logger = self.make(name)
                self.assertFalse(self.send(notifier))
                self.assertIn('no response', logger.text)
                self.assertNotIn('success', logger.text)

    def test_http_failures_do_not_log_response_body_or_url(self):
        for status in (201, 400, 429, 500):
            with self.subTest(status=status):
                notifier, _, logger = self.make(result=response({'code': 200, 'msg': SECRET}, status))
                self.assertFalse(self.send(notifier))
                self.assertIn(str(status), logger.text)
                self.assert_private(logger)

    def test_smtp_exception_is_failure(self):
        notifier, _, logger = self.make('smtp', error=SMTPResponseException(550, SECRET.encode()))
        self.assertFalse(self.send(notifier))
        self.assertIn('SMTPResponseException', logger.text)
        self.assertNotIn('success', logger.text)
        self.assert_private(logger)

    def test_provider_exceptions_only_log_the_type(self):
        for error in (FakeOnePushException(SECRET), RuntimeError(SECRET)):
            with self.subTest(kind=type(error).__name__):
                notifier, _, logger = self.make(error=error)
                self.assertFalse(self.send(notifier))
                self.assertIn(type(error).__name__, logger.text)
                self.assert_private(logger)

    def test_gocqhttp_business_failure_compatibility(self):
        notifier, _, logger = self.make('gocqhttp', response({'status': 'failed', 'wording': SECRET}))
        self.assertFalse(self.send(notifier))
        self.assertIn('status=failed', logger.text)
        self.assert_private(logger)

    def test_gocqhttp_success_compatibility(self):
        notifier, _, logger = self.make('gocqhttp', response({'status': 'ok'}))
        self.assertTrue(self.send(notifier))
        self.assertIn('Push notify success', logger.text)

    def test_other_providers_keep_existing_non_http_success_semantics(self):
        notifier, _, logger = self.make('smtp', result={})
        self.assertTrue(self.send(notifier))
        self.assertIn('Push notify success', logger.text)

    def test_disabled_notifications_do_not_call_the_provider(self):
        notifier, provider, logger = self.make(result=response({'code': 200}), enabled=False)
        self.assertFalse(self.send(notifier))
        self.assertEqual(provider.calls, [])

    def test_missing_provider_fails_without_an_attribute_error(self):
        logger = CaptureLogger()
        classes = load_classes(FakeProvider(), logger)
        notifier = classes.Notifier('token: offline-only', True)
        self.assertFalse(self.send(notifier))

    def test_underlying_provider_logging_cannot_leak_response_or_request(self):
        logger = CaptureLogger()
        classes = load_classes(FakeProvider(), logger)
        safe_log = classes._SafeProviderLogger()
        safe_log.debug('Response: ' + SECRET)
        safe_log.error(RuntimeError('https://example.invalid/send?token=' + SECRET))
        safe_log.error('Request failed with token=' + SECRET)
        self.assertIn('RuntimeError', logger.text)
        self.assertIn('ProviderError', logger.text)
        self.assert_private(logger)


if __name__ == '__main__':
    unittest.main()
