"""Offline purchase regressions; never connects to OAS, ADB or an emulator."""
import copy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filelock import FileLock

from dev_tools.test_bondling_battle_entry import (
    FakeClock, FakeLogger, FakeTimer, GameStuckError, extract_methods,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/DailyTrifles/script_task.py'


class RequestHumanTakeover(Exception):
    pass


class AccountLoggedInElsewhere(RequestHumanTakeover):
    pass


class SushiPurchaseSkipped(Exception):
    pass


class TaskEnd(Exception):
    pass


class Rule:
    def __init__(self, name=None, roi_front=(0, 0, 20, 20), roi_back=(0, 0, 20, 20),
                 file='', threshold=0.8, method='Template matching'):
        self.name = name or Path(file).stem.upper()
        self.roi_front = list(roi_front)
        self.roi_back = roi_back
        self.file = file
        self.threshold = threshold
        self.method = method


class Reader:
    score = 0.3
    name = 'STORE_SUSHI_PRICE'
    roi = (0, 0, 0, 0)

    def __init__(self, harness):
        self.harness = harness

    def detect_text(self, image):
        self.harness.reads.append((self.name, tuple(self.roi), self.score))
        key = 'quantity' if self.name == 'STORE_SUSHI_QUANTITY' else 'price'
        return image.get(key, '1' if key == 'quantity' else '')


class PurchaseHarness:
    def __init__(self, frames, directory, goal=1, account='01-test'):
        self.clock = FakeClock(step=0.33)
        self.logger = FakeLogger()
        self.frames = frames
        self.frame = {}
        self.screenshots = 0
        self.clicks = []
        self.reads = []
        self.confirmations = []
        self.recoveries = []
        self.delays = []
        self.closed = False
        self.close_works = True
        self.config = SimpleNamespace(config_name=account, daily_trifles=SimpleNamespace(
            trifles_config=SimpleNamespace(buy_sushi_count=goal)), task_call=self.recoveries.append)
        self.set_next_run = lambda *args, **kwargs: self.delays.append((args, kwargs))
        self.device = SimpleNamespace(image={})
        self.ledger = Path(directory) / f'{account}-sushi.json'
        self.I_STORE_COST_TYPE_JADE = Rule('STORE_COST_TYPE_JADE', (600, 490, 50, 60), (600, 490, 50, 60))
        self.I_SPECIAL_SUSHI = Rule('STORE_SUSHI', (260, 160, 51, 142))
        self.I_UI_REWARD = Rule('UI_UI_REWARD')
        self.C_UI_REWARD = Rule('UI_REWARD_ACTION', (919, 160, 208, 368))
        self.O_STORE_SUSHI_PRICE = Reader(self)
        self.on_click = None
        namespace = {'copy': copy, 're': re, 'json': json, 'os': os, 'Path': Path,
                     'datetime': datetime, 'FileLock': FileLock, 'RuleImage': Rule,
                     '__file__': str(SOURCE), 'logger': self.logger,
                     'Timer': lambda limit: FakeTimer(self.clock, limit),
                     'RequestHumanTakeover': RequestHumanTakeover,
                     'SushiPurchaseSkipped': SushiPurchaseSkipped, 'TaskEnd': TaskEnd}
        names = ('_sushi_price_info', '_read_sushi_price', '_read_sushi_quantity',
                 '_sushi_purchase_state', '_close_sushi_confirmation', '_run_buy_sushi', 'run_buy_sushi')
        methods = extract_methods(SOURCE, 'ScriptTask', names, namespace)
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))
        self._sushi_purchase_state_path = lambda: self.ledger

    def screenshot(self):
        if self.screenshots > 300:
            raise AssertionError('Purchase flow has no effective timeout')
        self.clock.advance()
        frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        if isinstance(frame, Exception):
            raise frame
        if self.closed:
            frame = dict(frame, confirmation=False)
        self.frame = frame
        self.device.image = frame

    def appear(self, rule, **kwargs):
        names = {'STORE_COST_TYPE_JADE': 'confirmation', 'STORE_SUSHI': 'item',
                 'UI_UI_REWARD': 'reward', 'SPECIAL_PAGE': 'shop'}
        if rule.name == 'STORE_SUSHI_PURCHASE_TITLE':
            return self.frame.get('title', True)
        present = bool(self.frame.get(names.get(rule.name, rule.name), False))
        if present and rule.name == 'STORE_COST_TYPE_JADE':
            rule.roi_front = [604, 537, 42, 52]
        return present

    def appear_then_click(self, rule, **kwargs):
        return False

    def click(self, rule, **kwargs):
        self.clicks.append(rule.name)
        if rule.name == 'SUSHI_CONFIRMATION_CLOSE' and self.close_works:
            self.closed = True
        if rule.name == 'STORE_COST_TYPE_JADE':
            record = json.loads(self.ledger.read_text(encoding='utf-8'))
            assert record['status'] == 'pending', 'Paid confirmation must be preceded by a durable claim'
            assert record['submitted_at'] and record['date']
            self.confirmations.append(record)
        if self.on_click:
            self.on_click(rule)

    def ui_reward_appear_click(self):
        self.clicks.append('REWARD_CLOSE')

    def run(self):
        assets = SimpleNamespace(I_SIDE_CHECK_SPECIAL=Rule('SPECIAL_PAGE'),
                                 I_MALL_SUNDRY=Rule('SUNDRY'), I_SIDE_SURE_SPECIAL=Rule('SPECIAL_TAB'))
        with patch.dict(sys.modules, {'tasks.RichMan.assets': SimpleNamespace(RichManAssets=assets)}):
            return self.run_buy_sushi()


def shopping(price='60', **extra):
    return {'shop': True, 'item': True, 'price': price, **extra}


def confirmation(price='60', **extra):
    return {'confirmation': True, 'price': price, 'quantity': '1', **extra}


def success_frames(initial='60', next_price='80'):
    return ([shopping(initial)] * 3 + [confirmation(initial)] * 2
            + [{'reward': True}] * 2 + [shopping(next_price)] * 2)


class SushiPurchaseTests(unittest.TestCase):
    def harness(self, frames, goal=1, directory=None):
        if directory is None:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            directory = temporary.name
        return PurchaseHarness(frames, directory, goal)

    def test_first_purchase_once_and_price_change_confirms(self):
        harness = self.harness(success_frames())
        original_roi = tuple(harness.I_STORE_COST_TYPE_JADE.roi_back)
        harness.run()
        self.assertEqual(len(harness.confirmations), 1)
        self.assertEqual(harness.confirmations[0]['price'], 60)
        self.assertEqual(json.loads(harness.ledger.read_text())['status'], 'confirmed')
        self.assertEqual(tuple(harness.I_STORE_COST_TYPE_JADE.roi_back), original_roi)
        self.assertIn('今日体力购买目标1次，已购买1次', str(harness.logger.messages))
        self.assertIn(('STORE_SUSHI_PRICE', (646, 559, 60, 30), 0.85), harness.reads)
        self.assertIn(('STORE_SUSHI_QUANTITY', (549, 437, 100, 55), 0.85), harness.reads)

    def test_already_bought_repeated_run_does_not_confirm(self):
        harness = self.harness([shopping('80')] * 3)
        harness.run(); harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertNotIn('STORE_SUSHI', harness.clicks)

    def test_existing_confirmation_already_bought_is_closed_without_purchase(self):
        harness = self.harness([confirmation('80')] * 4 + [shopping('80')])
        harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertIn('SUSHI_CONFIRMATION_CLOSE', harness.clicks)

    def test_existing_correct_confirmation_can_submit_once(self):
        harness = self.harness([confirmation()] * 3 + [shopping('80')] * 2)
        harness.run()
        self.assertEqual(len(harness.confirmations), 1)

    def test_other_jade_item_is_not_confirmed(self):
        harness = self.harness([confirmation(title=False)] * 4)
        self.assertIs(harness.run(), False)
        self.assertEqual(harness.confirmations, [])

    def test_unknown_or_invalid_prices_never_spend(self):
        for price in (None, '', '0', '40', '65', '9999', '6O', '1/60', '勾玉60'):
            with self.subTest(price=price):
                harness = self.harness([confirmation(price)] * 4)
                self.assertIs(harness.run(), False)
                self.assertEqual(harness.confirmations, [])

    def test_quantity_must_be_one(self):
        for quantity in ('2', '10', '', None):
            with self.subTest(quantity=quantity):
                harness = self.harness([confirmation(quantity=quantity)] * 4)
                self.assertIs(harness.run(), False)
                self.assertEqual(harness.confirmations, [])

    def test_unstable_price_does_not_confirm(self):
        harness = self.harness([confirmation('60' if index % 2 else '80') for index in range(110)])
        self.assertIs(harness.run(), False)
        self.assertEqual(harness.confirmations, [])

    def test_confirmation_timeout_does_not_retry(self):
        harness = self.harness([confirmation()] * 4)
        self.assertIs(harness.run(), False)
        self.assertEqual(len(harness.confirmations), 1)
        self.assertEqual(json.loads(harness.ledger.read_text())['status'], 'pending')
        self.assertIs(harness.run(), False)
        self.assertEqual(len(harness.confirmations), 1, 'Process reentry must not resubmit an unresolved purchase')

    def test_reentry_resolves_pending_only_from_same_day_higher_price(self):
        harness = self.harness([shopping('80')] * 3)
        harness._sushi_purchase_state('pending', 60, 1)
        harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertEqual(json.loads(harness.ledger.read_text())['status'], 'confirmed')

    def test_cross_day_pending_does_not_authorize_a_second_purchase_today(self):
        harness = self.harness([shopping('80')] * 3)
        record = harness._sushi_purchase_state('pending', 60, 1)
        record['date'] = (datetime.now() - timedelta(days=1)).date().isoformat()
        harness.ledger.write_text(json.dumps(record), encoding='utf-8')
        harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertEqual(json.loads(harness.ledger.read_text())['status'], 'pending')

    def test_confirmed_previous_day_allows_price_reset(self):
        harness = self.harness(success_frames())
        harness._sushi_purchase_state('pending', 60, 1)
        record = harness._sushi_purchase_state('confirmed', 80, 1)
        record['date'] = (datetime.now() - timedelta(days=1)).date().isoformat()
        harness.ledger.write_text(json.dumps(record), encoding='utf-8')
        harness.run()
        self.assertEqual(len(harness.confirmations), 1)

    def test_paid_click_or_screenshot_error_skips_task_and_requests_recovery(self):
        for fail_in_click in (False, True):
            harness = self.harness([confirmation()] * 3 + [GameStuckError('offline failure')])
            if fail_in_click:
                harness.on_click = lambda rule: (_ for _ in ()).throw(GameStuckError('send failure'))
            with self.assertRaises(TaskEnd):
                harness.run()
            self.assertEqual(len(harness.confirmations), 1)
            self.assertEqual(json.loads(harness.ledger.read_text())['status'], 'pending')
            self.assertEqual(harness.recoveries, ['Restart'])
            self.assertEqual(len(harness.delays), 1)

    def test_restored_pending_navigation_error_recovers_without_buying_again(self):
        harness = self.harness([GameStuckError('read failure')])
        harness._sushi_purchase_state('pending', 60, 1)
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertEqual(harness.recoveries, ['Restart'])

    def test_account_conflict_exception_is_preserved(self):
        conflict = AccountLoggedInElsewhere('account logged in elsewhere')
        harness = self.harness([conflict])
        harness._sushi_purchase_state('pending', 60, 1)
        with self.assertRaises(AccountLoggedInElsewhere) as caught:
            harness.run()
        self.assertIs(caught.exception, conflict)

    def test_concurrent_claims_and_stale_confirmed_record_are_rejected(self):
        first = self.harness([shopping()])
        second = self.harness([shopping()], directory=first.ledger.parent)
        before_first = first._sushi_purchase_state()
        before_second = second._sushi_purchase_state()
        first._sushi_purchase_state('pending', 60, 1, expected=before_first)
        with self.assertRaises(SushiPurchaseSkipped):
            second._sushi_purchase_state('pending', 60, 1, expected=before_second)
        first._sushi_purchase_state('confirmed', 80, 1)
        with self.assertRaises(SushiPurchaseSkipped):
            second._sushi_purchase_state('pending', 60, 1, expected=before_second)
        latest = second._sushi_purchase_state()
        with self.assertRaises(SushiPurchaseSkipped):
            second._sushi_purchase_state('pending', 60, 1, expected=latest)
        with FileLock(str(first.ledger) + '.lock'):
            with self.assertRaises(SushiPurchaseSkipped):
                second._sushi_purchase_state('pending', 80, 2, expected=latest)

    def test_state_write_failure_prevents_paid_confirmation(self):
        harness = self.harness([confirmation()] * 3)
        original = harness._sushi_purchase_state
        def fail_claim(action='read', *args, **kwargs):
            if action == 'pending':
                raise SushiPurchaseSkipped('cannot save claim')
            return original(action, *args, **kwargs)
        harness._sushi_purchase_state = fail_claim
        self.assertIs(harness.run(), False)
        self.assertEqual(harness.confirmations, [])

    def test_only_first_60_jade_purchase_is_allowed_for_goal_one(self):
        for price in ('80', '100', '120', '140'):
            with self.subTest(price=price):
                harness = self.harness([confirmation(price)] * 4)
                harness.run()
                self.assertEqual(harness.confirmations, [])

    def test_next_day_pending_recovers_automatically_only_after_price_reset(self):
        harness = self.harness(success_frames())
        record = harness._sushi_purchase_state('pending', 60, 1)
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        record['date'] = yesterday
        harness.ledger.write_text(json.dumps(record), encoding='utf-8')
        harness.run()
        self.assertEqual(len(harness.confirmations), 1)
        self.assertEqual(harness.confirmations[0]['price'], 60)
        latest = json.loads(harness.ledger.read_text(encoding='utf-8'))
        self.assertEqual(latest['status'], 'confirmed')
        self.assertEqual(latest['previous_attempt']['date'], yesterday)

    def test_skip_returns_to_courtyard_and_finishes_daily_without_stopping_account(self):
        harness = self.harness([confirmation(price='unknown')] * 4)
        flags = harness.config.daily_trifles.trifles_config
        for name in ('one_summon', 'guild_wish', 'friend_love', 'luck_msg', 'store_sign'):
            setattr(flags, name, False)
        pages = []
        harness.goto_page = lambda page, **kwargs: pages.append(page)
        harness.ui_click = lambda *args, **kwargs: None
        harness.I_UI_BACK_YELLOW = Rule('BACK')
        harness.I_CHECK_MALL = Rule('MALL')
        methods = extract_methods(SOURCE, 'ScriptTask', ('run_store', 'run'),
                                  {'page_mall': 'mall', 'page_main': 'courtyard', 'TaskEnd': TaskEnd})
        harness.run_store = methods['run_store'].__get__(harness)
        daily = methods['run'].__get__(harness)
        assets = SimpleNamespace(I_SIDE_CHECK_SPECIAL=Rule('SPECIAL_PAGE'),
                                 I_MALL_SUNDRY=Rule('SUNDRY'), I_SIDE_SURE_SPECIAL=Rule('SPECIAL_TAB'))
        with patch.dict(sys.modules, {'tasks.RichMan.assets': SimpleNamespace(RichManAssets=assets)}):
            with self.assertRaises(TaskEnd):
                daily()
        self.assertEqual(pages, ['mall', 'courtyard'])
        self.assertEqual(len(harness.delays), 1)
        self.assertEqual(harness.recoveries, [])
        self.assertEqual(harness.confirmations, [])

    def test_failed_popup_cleanup_advances_daily_and_requests_one_restart(self):
        harness = self.harness([confirmation(price='unknown')] * 4)
        harness.close_works = False
        with self.assertRaises(TaskEnd):
            harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertEqual(harness.recoveries, ['Restart'])
        self.assertEqual(len(harness.delays), 1)

    def test_takeover_during_skip_cleanup_still_stops_account(self):
        harness = self.harness([confirmation(quantity='2')] * 2 + [AccountLoggedInElsewhere('owner online')])
        with self.assertRaises(AccountLoggedInElsewhere):
            harness.run()
        self.assertEqual(harness.confirmations, [])
        self.assertEqual(harness.recoveries, [])

    @unittest.skipUnless(
        (ROOT / 'log/error/1791255638853/2026-10-06_11-00-38-777053.png').is_file(),
        'The optional local incident screenshot has been cleaned up',
    )
    def test_real_error_frame_matches_new_local_confirmation_and_product_title(self):
        import cv2
        frame = cv2.imread(str(ROOT / 'log/error/1791255638853/2026-10-06_11-00-38-777053.png'))
        self.assertIsNotNone(frame)
        jade = cv2.imread(str(ROOT / 'tasks/DailyTrifles/store/store_cost_type_jade.png'))
        title = cv2.imread(str(ROOT / 'tasks/DailyTrifles/store/store_sushi_purchase_title.png'))
        def score(image, template, roi):
            x, y, width, height = roi
            return cv2.minMaxLoc(cv2.matchTemplate(image[y:y+height, x:x+width], template,
                                                 cv2.TM_CCOEFF_NORMED))[1]
        self.assertLess(score(frame, jade, (600, 490, 50, 60)), 0.8)
        self.assertGreater(score(frame, jade, (500, 480, 300, 170)), 0.97)
        self.assertGreater(score(frame, title, (480, 100, 320, 160)), 0.99)
        other_item = frame.copy()
        other_item[151:185, 588:697] = frame[151, 588]
        self.assertGreater(score(other_item, jade, (500, 480, 300, 170)), 0.97)
        self.assertLess(score(other_item, title, (480, 100, 320, 160)), 0.9,
                        'A generic jade icon must not authorize a different product')


if __name__ == '__main__':
    unittest.main()
