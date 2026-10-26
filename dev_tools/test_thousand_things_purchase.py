"""Offline regressions for the failed quantity dialog and missing rewards."""
import ast
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class Mode(Enum):
    DIGIT = 1
    SINGLE = 2


class Reader:
    mode = Mode.DIGIT
    roi = [576, 415, 58, 49]
    area = roi

    def __init__(self, reads):
        self.reads = reads

    def ocr(self, frame):
        self.reads.append((self.mode, tuple(self.roi)))
        return frame.get('quantity', '')


def frame(quantity='', dialog=False, reward=False, shop=False):
    visible = {'ap'} if shop else set()
    if dialog:
        visible.update({'up', 'confirm', 'back'})
    if reward:
        visible.add('reward')
    return dict(visible=visible, quantity=quantity)


class PurchaseTests(TestCase):
    def harness(self, frames):
        tree = ast.parse((ROOT / 'tasks/RichMan/thousand_things.py').read_text('utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        names = {'tt_buy_ap', '_tt_buy_dialog_open', '_tt_read_purchase_quantity',
                 '_tt_close_buy_dialog', 'tt_get_reward'}
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        clock = SimpleNamespace(now=0)
        def sleep(seconds):
            clock.now += seconds
        clock.sleep = sleep
        clock.monotonic = lambda: clock.now
        namespace = dict(time=clock, logger=Mock())
        node = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<tt-purchase>', 'exec'), namespace)
        subject = namespace['Subject']()
        subject.device = SimpleNamespace(image=frames[0])
        pending = iter(frames)
        def screenshot():
            subject.device.image = next(pending, subject.device.image)
        subject.screenshot = Mock(side_effect=screenshot)
        subject.I_TT_AP = 'ap'
        subject.I_TT_BUY_CONFIRM = 'confirm'
        subject.I_TT_BUY_UP = SimpleNamespace(name='up', roi_front=[757, 440, 59, 57])
        subject.I_UI_BACK_RED = 'back'
        subject.I_UI_REWARD = 'reward'
        subject.O_TT_AP = 'ap-price'
        subject.reads = []
        subject.O_TT_NUMBER = Reader(subject.reads)
        def appear(rule, **kwargs):
            return (rule if isinstance(rule, str) else rule.name) in subject.device.image['visible']
        subject.appear = Mock(side_effect=appear)
        subject.click = Mock()
        def appear_then_click(rule, **kwargs):
            if appear(rule):
                subject.click(rule)
                return True
            return False
        subject.appear_then_click = Mock(side_effect=appear_then_click)
        subject.ocr_appear_click = Mock(return_value=True)
        subject.ui_reward_appear_click = Mock(side_effect=lambda: appear('reward'))
        subject.tt_check_money = Mock(return_value=True)
        return subject

    def test_shop_background_digits_cannot_trigger_purchase_or_maximum(self):
        subject = self.harness([frame('2', shop=True)])
        subject.tt_get_reward = Mock()
        self.assertFalse(subject.tt_buy_ap())
        self.assertEqual(subject.reads, [])
        subject.click.assert_not_called()
        subject.tt_get_reward.assert_not_called()
        self.assertLess(subject.screenshot.call_count, 40)

    def test_empty_quantity_is_unknown_and_never_clicks_maximum_or_confirms(self):
        subject = self.harness([frame(shop=True), frame('', dialog=True)])
        subject.tt_get_reward = Mock()
        self.assertFalse(subject.tt_buy_ap())
        self.assertNotIn(subject.I_TT_BUY_UP, [c.args[0] for c in subject.click.call_args_list])
        subject.tt_get_reward.assert_not_called()

    def test_verified_two_after_maximum_buys_once_and_does_not_mutate_shared_reader(self):
        subject = self.harness([frame(shop=True), frame('1', dialog=True),
                                frame('2', dialog=True), frame('2', dialog=True)])
        subject.tt_get_reward = Mock(return_value=True)
        self.assertTrue(subject.tt_buy_ap())
        subject.click.assert_called_once_with(subject.I_TT_BUY_UP)
        subject.tt_get_reward.assert_called_once_with('confirm')
        self.assertEqual(subject.O_TT_NUMBER.mode, Mode.DIGIT)
        self.assertEqual(subject.O_TT_NUMBER.roi, [576, 415, 58, 49])
        self.assertEqual(set(subject.reads), {(Mode.SINGLE, (566, 447, 80, 42))})

    def test_one_good_read_then_unknown_does_not_confirm(self):
        subject = self.harness([frame(shop=True), frame('2', dialog=True), frame('', dialog=True)])
        subject.tt_get_reward = Mock()
        self.assertFalse(subject.tt_buy_ap())
        subject.tt_get_reward.assert_not_called()

    def test_unreadable_or_malformed_numbers_are_rejected(self):
        for value in ['', '0', '00', '12', '2x', None, 2]:
            with self.subTest(value=value):
                subject = self.harness([frame(value, dialog=True)])
                self.assertIsNone(subject._tt_read_purchase_quantity())

    def test_quantity_roi_follows_vertical_dialog_shift(self):
        subject = self.harness([frame('2', dialog=True)])
        subject.I_TT_BUY_UP.roi_front[1] += 25
        self.assertEqual(subject._tt_read_purchase_quantity(), 2)
        self.assertEqual(subject.reads[-1][1], (566, 472, 80, 42))

    def test_missing_dialog_and_reward_exits_without_confirming(self):
        subject = self.harness([frame(shop=True)])
        self.assertFalse(subject.tt_get_reward('confirm'))
        subject.click.assert_not_called()
        self.assertLess(subject.screenshot.call_count, 55)

    def test_reward_must_be_seen_then_dismissed_to_succeed(self):
        subject = self.harness([frame(dialog=True), frame(reward=True), frame(shop=True)])
        self.assertTrue(subject.tt_get_reward('confirm'))
        subject.click.assert_called_once_with('confirm')

    def test_stuck_dialog_limits_confirmation_and_returns_failure(self):
        subject = self.harness([frame(dialog=True)])
        self.assertFalse(subject.tt_get_reward('confirm'))
        confirmations = [c for c in subject.click.call_args_list if c.args[0] == 'confirm']
        self.assertEqual(len(confirmations), 3)
        self.assertLess(subject.screenshot.call_count, 55)

    def test_failed_reward_does_not_report_ap_purchase_success(self):
        subject = self.harness([frame(shop=True), frame('2', dialog=True), frame('2', dialog=True)])
        subject.tt_get_reward = Mock(return_value=False)
        self.assertFalse(subject.tt_buy_ap())


if __name__ == '__main__':
    main()
