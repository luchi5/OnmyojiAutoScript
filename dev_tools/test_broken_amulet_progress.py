"""Offline summon progress and real click-guard regressions; no device imports."""
import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock
import unittest

ROOT = Path(__file__).resolve().parents[1]


def methods(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text('utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


class Timer:
    def __init__(self, seconds):
        self.steps = 0

    def start(self):
        return self

    def reached(self):
        self.steps += 1
        return self.steps > 5


class ProgressTests(unittest.TestCase):
    def harness(self, amounts, visible=True):
        ns = methods('tasks/WeeklyTrifles/script_task.py', ['_wait_broken_amulet_result'],
                     {'Timer': Timer, 'random_click': lambda: 'SAFE_RANDOM_CLICK'})
        guard = methods('module/device/device.py',
                        ['click_record_remove', 'click_record_clear', 'click_record_check'],
                        {'logger': Mock(), 'GameTooManyClickError': RuntimeError})
        device = SimpleNamespace(image=None, click_record=deque(maxlen=15))
        for name in ('click_record_remove', 'click_record_clear', 'click_record_check'):
            setattr(device, name, MethodType(guard[name], device))
        h = SimpleNamespace(device=device, screenshot=Mock(), appear=Mock(return_value=visible),
                            I_BM_ENTER='ENTER', I_BM_AGAIN='AGAIN',
                            O_BA_AMOUNT_1=SimpleNamespace(ocr=Mock(side_effect=amounts)))
        def click(button, **kwargs):
            device.click_record.append(button)
            device.click_record_check()
            return True
        h.click = Mock(side_effect=click)
        h.wait = MethodType(ns['_wait_broken_amulet_result'], h)
        return h

    def test_many_successful_rounds_do_not_accumulate_animation_clicks(self):
        h = self.harness([100, 100, 100, 90] * 15)
        h.device.click_record.append('UNRELATED')
        for _ in range(15):
            h.device.click_record.append('AGAIN')
            self.assertEqual(h.wait(100), 10)
            self.assertEqual(list(h.device.click_record), ['UNRELATED'])

    def test_fifty_and_final_zero_are_valid_progress(self):
        for before, after in ((100, 50), (10, 0)):
            h = self.harness([after])
            self.assertEqual(h.wait(before), before-after)

    def test_unchanged_count_times_out_without_clearing(self):
        h = self.harness([100] * 5)
        h.device.click_record.append('AGAIN')
        self.assertIsNone(h.wait(100))
        self.assertIn('AGAIN', h.device.click_record)
        self.assertEqual(h.click.call_count, 5)

    def test_unreadable_or_implausible_count_does_not_clear(self):
        for value in (None, '', '90', True, -1, 101, 89):
            with self.subTest(value=value):
                h = self.harness([value] * 5)
                h.device.click_record.append('AGAIN')
                self.assertIsNone(h.wait(100))
                self.assertIn('AGAIN', h.device.click_record)

    def test_missing_result_button_does_not_accept_consumption(self):
        h = self.harness([90], visible=False)
        self.assertIsNone(h.wait(100))
        h.O_BA_AMOUNT_1.ocr.assert_not_called()

    def test_real_stuck_click_guard_still_raises(self):
        h = self.harness([100] * 5)
        h.device.click_record.extend(['SAFE_RANDOM_CLICK'] * 9)
        with self.assertRaises(RuntimeError):
            h.wait(100)

    def flow(self, consumed):
        ns = methods('tasks/WeeklyTrifles/script_task.py', ['_broken_amulet'],
                     {'logger': Mock(), 'page_summon': 'summon', 'sleep': Mock(),
                      'TaskEnd': RuntimeError})
        h = SimpleNamespace(device=SimpleNamespace(image=None, click=Mock(), click_record_clear=Mock()),
            screenshot=Mock(), goto_page=Mock(), wait_until_appear=Mock(),
            appear=Mock(return_value=False), appear_then_click=Mock(return_value=True),
            I_BM_ENTER='ENTER', I_BM_AGAIN='AGAIN', I_BM_CONFIRM='CONFIRM',
            O_BA_AMOUNT_1=SimpleNamespace(ocr=Mock(return_value=100)),
            O_BA_TIMES=SimpleNamespace(ocr=Mock(side_effect=[(780, 0, 40, 20), (874, 0, 40, 20)])),
            I_BMT_CHECK=SimpleNamespace(match=Mock(), roi_front=[850, 570, 32, 33]),
            _wait_broken_amulet_result=Mock(return_value=consumed), set_next_run=Mock())
        h.run = MethodType(ns['_broken_amulet'], h)
        return h

    def test_unconfirmed_round_schedules_failure_and_stops(self):
        h = self.flow(None)
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
            h.run(10)
        h.set_next_run.assert_called_once_with(task='WeeklyTrifles', success=False)
        h.O_BA_TIMES.ocr.assert_not_called()

    def test_confirmed_round_reaches_quota_and_exits(self):
        h = self.flow(10)
        h.run(10)
        h._wait_broken_amulet_result.assert_called_once_with(100)
        h.set_next_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
