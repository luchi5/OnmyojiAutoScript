"""Offline purchase guards; no device, network, or real currency used."""
import ast
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PurchaseTests(unittest.TestCase):
    def harness(self):
        tree = ast.parse((ROOT / 'tasks/RichMan/itachi_shop.py').read_text('utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        ns = {'time': SimpleNamespace(monotonic=Mock(side_effect=range(100))),
              'logger': Mock(), 'ItachiCoinShopConfig': object,
              'page_entertainment': 'park', 'page_itachi_shop': 'shop',
              'ChessAssets': SimpleNamespace(I_SKIP='skip')}
        module = ast.Module(body=cls.body, type_ignores=[])
        exec(compile(module, '<purchase-tests>', 'exec'), ns)
        h = SimpleNamespace()
        for key, value in ns.items():
            if key.startswith('__'): continue
            setattr(h, key, MethodType(value, h) if callable(value) and key.startswith(('_', 'execute')) else value)
        h.screenshot = Mock()
        h.goto_page = Mock()
        h.click = Mock()
        h.C_BUY_MORE = 'buy'
        h.I_UI_REWARD = 'reward'
        h.I_BUY_PLUS = 'dialog'
        h._read_itachi_coin = Mock(return_value=(100, 1000))
        h._read_buy_cost = Mock(return_value=50)
        h._verify_coin_cost = Mock()
        return h

    def test_disabled_and_old_schema_do_nothing(self):
        for config in (None, SimpleNamespace(itachi_coin_buy_jade=False)):
            h = self.harness(); h.execute_itachi_coin_shop(config)
            h.goto_page.assert_not_called(); h.click.assert_not_called()

    def test_unreadable_or_insufficient_balance_never_confirms(self):
        for balance, cost in [(None, 50), ((100,1000), None), ((20,1000),50)]:
            h=self.harness(); h._read_itachi_coin.return_value=balance; h._read_buy_cost.return_value=cost
            self.assertFalse(h._buy_opened_gift()); h.click.assert_not_called()

    def test_success_requires_reward(self):
        h=self.harness(); h.appear=Mock(return_value=True)
        h._dismiss_purchase_reward=Mock(return_value=True)
        self.assertTrue(h._buy_opened_gift())
        h.click.assert_called_once_with('buy'); h._verify_coin_cost.assert_called_once()

    def test_missing_reward_stops_without_repeated_confirm(self):
        h=self.harness(); h.appear=Mock(return_value=False)
        self.assertFalse(h._buy_opened_gift()); h.click.assert_called_once_with('buy')
        h._verify_coin_cost.assert_not_called()

    def test_limit_and_early_stop(self):
        for opened, bought, expected in [(True,True,3),(False,True,0),(True,False,1)]:
            h=self.harness(); h._wait_entertainment_overlay_closed=Mock(return_value=True)
            h._open_front_gift_once=Mock(return_value=opened); h._buy_opened_gift=Mock(return_value=bought)
            h.execute_itachi_coin_shop(SimpleNamespace(itachi_coin_buy_jade=True))
            self.assertEqual(h._buy_opened_gift.call_count, expected)
            self.assertEqual(h.goto_page.call_args.args, ('park',))

    def test_overlay_timeout_does_not_enter_shop(self):
        h=self.harness(); h._wait_entertainment_overlay_closed=Mock(return_value=False)
        h.execute_itachi_coin_shop(SimpleNamespace(itachi_coin_buy_jade=True))
        h.goto_page.assert_called_once_with('park')

if __name__ == '__main__': unittest.main()
