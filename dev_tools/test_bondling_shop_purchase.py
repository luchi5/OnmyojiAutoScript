"""Wrong contract-shop items and unreadable amounts must never be purchased."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods,FakeClock,FakeTimer
ROOT=Path(__file__).resolve().parents[1]

def harness(item='随机御魂',quantities=('10','10')):
    clock=FakeClock(1)
    h=SimpleNamespace(device=SimpleNamespace(image=None),O_BUY_NUMBER=SimpleNamespace(roi=(0,0,10,10)),
        I_BUY_PLUS='plus',I_BUY_ADD='add',I_BUY_SUB='sub',C_BUY_MORE='purchase')
    h.screenshot=Mock(side_effect=clock.advance);h.appear=lambda r:r=='plus'
    h.appear_then_click=Mock(return_value=True);h.click=Mock();h.ui_reward_appear_click=Mock(return_value=True)
    quantity=Mock(side_effect=quantities)
    def reader(**kw):
        return SimpleNamespace(detect_and_ocr=lambda im:[SimpleNamespace(ocr_text=item)],ocr=quantity)
    ns={'RuleOcr':reader,'RuleImage':object,'Timer':lambda t:FakeTimer(clock,t),
        'GameStuckError':RuntimeError,'logger':Mock(),'time':SimpleNamespace(sleep=lambda _:None)}
    for n,m in extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',
            ('_verify_bondling_item','_buy_bondling_checked'),ns).items():setattr(h,n,m.__get__(h))
    return h

class PurchaseTests(unittest.TestCase):
    def test_stone_in_soul_slot_is_rejected(self):
        h=harness('鸣契石')
        with self.assertRaisesRegex(RuntimeError,'item not confirmed'):h._buy_bondling_checked('entry',10,1)
        h.click.assert_not_called();h.appear_then_click.assert_not_called()
    def test_blank_quantity_never_confirms(self):
        h=harness(quantities=[''])
        with self.assertRaisesRegex(RuntimeError,'quantity unreadable'):h._buy_bondling_checked('entry',10,1)
        h.click.assert_not_called()
    def test_zero_quantity_never_confirms(self):
        h=harness(quantities=['0'])
        with self.assertRaises(RuntimeError):h._buy_bondling_checked('entry',10,1)
        h.click.assert_not_called()
    def test_verified_item_and_quantity_confirm_once(self):
        h=harness();h._buy_bondling_checked('entry',10,1);h.click.assert_called_once_with('purchase')
    def test_excess_quantity_is_reduced(self):
        h=harness(quantities=['11','10','10']);h._buy_bondling_checked('entry',10,1)
        h.appear_then_click.assert_called_once_with('sub',interval=0.8)
        h.click.assert_called_once_with('purchase')
    def test_missing_reward_does_not_repeat_purchase(self):
        h=harness();h.ui_reward_appear_click.return_value=False
        with self.assertRaisesRegex(RuntimeError,'result unknown'):h._buy_bondling_checked('entry',10,1)
        h.click.assert_called_once_with('purchase')

if __name__=='__main__':unittest.main()
