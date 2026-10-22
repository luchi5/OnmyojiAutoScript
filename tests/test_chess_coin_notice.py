"""600 coin notices are per invocation, with isolated receipts and no real HTTP."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from requests import Response

from module.exception import TaskEnd
from tests import test_chess_port as port


class FakeBarkNotifier:
    def __init__(self):
        self.enable = True
        self.provider_name = 'bark'
        self.notifier = SimpleNamespace(request=None)
        self.calls = []

    def __deepcopy__(self, memo):
        return self

    def push(self, **kwargs):
        self.calls.append(kwargs)
        response = self.notifier.request('POST', 'https://example.invalid/test-only', json=kwargs)
        return response.status_code == 200 and response.json().get('code') == 200


def receipt(code=200):
    response = Response()
    response.status_code = 200
    response._content = json.dumps({'code': code}).encode()
    return response


class ChessCoinNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch('tasks.Chess.script_task.__file__',
                        str(self.root / 'tasks' / 'Chess' / 'script_task.py'))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('requests.request', return_value=receipt())
        self.http = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('tasks.Chess.script_task.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, coin_full_exit=True):
        task = port.ChessPortTests().make_task()
        task.config.config_name = '17'
        task.config.notifier = FakeBarkNotifier()
        task.config.chess.chess_config.coin_full_exit = coin_full_exit
        task._recover_interrupted_chess_game = Mock()
        task.goto_page = Mock()
        task.screenshot = Mock()
        task._coin_is_full = Mock(return_value=True)
        task.run_one_game = Mock(return_value=5)
        task.return_to_chess_lobby = Mock()
        task.set_next_run = Mock()
        task.set_next_run_next_monday = Mock()
        return task

    def run_task(self, task):
        with self.assertRaises(TaskEnd):
            task.run()

    def records(self):
        return [json.loads(p.read_text('utf-8')) for p in
                (self.root / 'config' / 'chess_weekly_notify').glob('*/*.json')]

    def test_without_verified_600_no_request_or_receipt(self):
        task = self.task()
        self.assertFalse(task._notify_weekly_coin_full())
        self.http.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_same_run_only_notifies_once_and_keeps_bounded_http_timeout(self):
        task = self.task()
        self.assertTrue(task._notify_weekly_coin_full(confirmed=True))
        self.assertFalse(task._notify_weekly_coin_full(confirmed=True))
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(self.http.call_args.kwargs['timeout'], (3, 5))
        self.assertEqual(self.records()[0]['notification']['status'], 'accepted')

    def test_two_instances_of_same_script_in_same_week_both_notify(self):
        now = datetime(2026, 10, 22, 15)
        first, second = self.task(), self.task()
        self.assertTrue(first._notify_weekly_coin_full(confirmed=True, now=now))
        self.assertTrue(second._notify_weekly_coin_full(confirmed=True, now=now))
        self.assertEqual(self.http.call_count, 2)
        records = self.records()
        self.assertEqual(len({r['run_id'] for r in records}), 2)
        self.assertTrue(all(r['account'] == '17' and r['version'] == 2 for r in records))

    def test_reusing_task_instance_resets_notice_for_each_actual_run(self):
        task = self.task()
        self.run_task(task)
        first_id = task._coin_notice_run_id
        self.run_task(task)
        self.assertNotEqual(first_id, task._coin_notice_run_id)
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(task.run_one_game.call_count, 0)
        self.assertEqual(task.set_next_run_next_monday.call_count, 2)

    def test_legacy_weekly_accepted_receipt_cannot_suppress_new_run(self):
        now = datetime(2026, 10, 22, 15)
        directory = self.root / 'config' / 'chess_weekly_notify' / '2026-10-19'
        directory.mkdir(parents=True)
        legacy = directory / (hashlib.sha256(b'17').hexdigest() + '.json')
        original = json.dumps({'version': 1, 'account': '17', 'week': '2026-10-19',
                               'notification': {'status': 'accepted', 'attempts': 1}})
        legacy.write_text(original, encoding='utf-8')
        self.assertTrue(self.task()._notify_weekly_coin_full(confirmed=True, now=now))
        self.http.assert_called_once()
        self.assertEqual(legacy.read_text('utf-8'), original)

    def test_failed_run_has_bounded_retries_but_new_run_can_notify_again(self):
        task = self.task()
        self.http.return_value = receipt(500)
        self.assertFalse(task._notify_weekly_coin_full(confirmed=True))
        self.assertFalse(task._notify_weekly_coin_full(confirmed=True))
        self.assertEqual(self.http.call_count, 3)
        self.http.return_value = receipt()
        self.run_task(task)
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual({r['notification']['status'] for r in self.records()}, {'failed', 'accepted'})

    def test_disabled_notifications_in_previous_run_do_not_suppress_next_run(self):
        task = self.task()
        task.config.notifier.enable = False
        self.run_task(task)
        self.http.assert_not_called()
        task.config.notifier.enable = True
        self.run_task(task)
        self.http.assert_called_once()

    def test_notification_does_not_require_coin_full_exit_option(self):
        task = self.task(coin_full_exit=False)
        self.run_task(task)
        self.http.assert_called_once()
        task.run_one_game.assert_called_once()
        task.set_next_run_next_monday.assert_not_called()
        task.set_next_run.assert_called_once_with(task='Chess', success=True, finish=True)

    def test_reaching_600_after_game_notifies_and_preserves_next_monday_schedule(self):
        task = self.task()
        task._coin_is_full.side_effect = [False, True]
        self.run_task(task)
        self.http.assert_called_once()
        task.run_one_game.assert_called_once()
        task.set_next_run_next_monday.assert_called_once()

    def test_below_600_or_unrecognized_does_not_notify(self):
        task = self.task()
        task._coin_is_full.return_value = False
        self.run_task(task)
        self.http.assert_not_called()
        self.assertEqual(self.records(), [])
        task.set_next_run.assert_called_once_with(task='Chess', success=True, finish=True)


if __name__ == '__main__':
    unittest.main()
