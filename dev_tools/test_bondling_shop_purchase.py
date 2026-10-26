"""Wrong contract-shop items and unreadable amounts must never be purchased."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import re
from dev_tools.test_bondling_battle_entry import extract_methods,FakeClock,FakeTimer
ROOT=Path(__file__).resolve().parents[1]

def harness(item='随机御魂',quantities=('10','10')):
    clock=FakeClock(1)
    h=SimpleNamespace(device=SimpleNamespace(image=None,click_record_remove=Mock()),O_BUY_NUMBER=SimpleNamespace(roi=(0,0,10,10)),
        I_BUY_PLUS='plus',I_BUY_ADD='add',I_BUY_SUB='sub',C_BUY_MORE='purchase')
    h.screenshot=Mock(side_effect=clock.advance);h.appear=lambda r:r=='plus'
    h.appear_then_click=Mock(return_value=True);h._click_bondling_quantity_control=Mock(return_value=True);h.click=Mock();h.ui_reward_appear_click=Mock(return_value=True)
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
        h._click_bondling_quantity_control.assert_called_once_with('sub')
        h.click.assert_called_once_with('purchase')
    def test_missing_reward_does_not_repeat_purchase(self):
        h=harness();h.ui_reward_appear_click.return_value=False
        with self.assertRaisesRegex(RuntimeError,'result unknown'):h._buy_bondling_checked('entry',10,1)
        h.click.assert_called_once_with('purchase')

    def test_maximum_arrow_replaces_repeated_adds(self):
        h=harness(quantities=['1','25','25'])
        h._buy_bondling_checked('entry',25,1)
        h._click_bondling_quantity_control.assert_called_once_with('plus')
        h.click.assert_called_once_with('purchase')

    def test_maximum_over_target_is_reduced_before_purchase(self):
        h=harness(quantities=['1','12','11','10','10'])
        h._buy_bondling_checked('entry',10,1)
        self.assertEqual([c.args[0] for c in h._click_bondling_quantity_control.call_args_list],['plus','sub','sub'])
        h.click.assert_called_once_with('purchase')

    def test_long_adjustment_only_clears_guard_for_verified_progress(self):
        h=harness(quantities=['1']+[str(x) for x in range(25,9,-1)]+['10'])
        h._buy_bondling_checked('entry',10,1)
        self.assertEqual(h.device.click_record_remove.call_count,15)
        self.assertTrue(all(c.args==('sub',) for c in h.device.click_record_remove.call_args_list))

    def test_unresponsive_quantity_keeps_click_guard_and_never_buys(self):
        h=harness(quantities=['1']*60)
        with self.assertRaisesRegex(RuntimeError,'quantity not confirmed'):
            h._buy_bondling_checked('entry',25,1)
        h.device.click_record_remove.assert_not_called()
        h.click.assert_not_called()

class QuantityClickTests(unittest.TestCase):
    def test_only_center_of_verified_control_is_clicked_with_same_guard_name(self):
        from module.atom.click import RuleClick
        method=extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',
            ('_click_bondling_quantity_control',),{'RuleClick':RuleClick})['_click_bondling_quantity_control']
        h=SimpleNamespace(appear=Mock(return_value=True),click=Mock())
        control=SimpleNamespace(name='BUY_BUY_SUB',roi_front=[467,429,50,48])
        self.assertTrue(method(h,control))
        h.appear.assert_called_once_with(control,interval=0.8)
        target=h.click.call_args.args[0]
        self.assertEqual(target.roi_front,(487,448,10,10))
        self.assertEqual(target.name,control.name)
    def test_missing_control_is_never_clicked(self):
        from module.atom.click import RuleClick
        method=extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',
            ('_click_bondling_quantity_control',),{'RuleClick':RuleClick})['_click_bondling_quantity_control']
        h=SimpleNamespace(appear=Mock(return_value=False),click=Mock())
        self.assertFalse(method(h,SimpleNamespace(name='minus')))
        h.click.assert_not_called()


class LocationTests(unittest.TestCase):
    def locate(self, sold=False):
        # Random soul moves to the fourth column; don't reuse the first button.
        row=SimpleNamespace(ocr_text='随机御魂',box=[[936,25],[1031,25],[1031,52],[936,52]])
        def reader(**kw):
            return SimpleNamespace(detect_and_ocr=lambda im:
                [row] if kw['name']=='bondling_shop_names' else [SimpleNamespace(ocr_text='每周限购',box=[[50,10],[72,10],[72,100],[50,100]])], ocr=lambda im:'100')
        ns={'RuleOcr':reader,'RuleImage':lambda **kw:kw,'RuleClick':lambda **kw:SimpleNamespace(**kw),
            'GameStuckError':RuntimeError,'re':re,'logger':Mock()}
        h=SimpleNamespace(device=SimpleNamespace(image=None),appear=lambda r:sold)
        f=extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',('_locate_bondling_product_once',),ns)['_locate_bondling_product_once']
        return f(h,1)
    def test_sold_out_soul_is_skipped(self):
        self.assertIsNone(self.locate(True))
    def test_reordered_soul_uses_fourth_column(self):
        target,count=self.locate()
        self.assertGreater(target.roi_front[0],1000)
        self.assertEqual(count,100)

if __name__=='__main__':unittest.main()

class StockRecoveryTests(unittest.TestCase):
    def test_insufficient_balance_skips_stock(self):
        ns={'RuleImage':object,'RuleOcr':object,'logger':Mock()}
        f=extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',('_bondlings_base',),ns)['_bondlings_base']
        h=SimpleNamespace(screenshot=Mock(),device=SimpleNamespace(image=None),
            O_BL_CHECK_MONEY=SimpleNamespace(ocr=lambda im:(19,3981,4000)),_locate_bondling_product=Mock())
        f(h,SimpleNamespace(name='soul'),None,1,100,25,20)
        h._locate_bondling_product.assert_not_called()
    def test_unknown_stock_retries_then_skips(self):
        ns={'GameStuckError':RuntimeError,'logger':Mock(),'time':SimpleNamespace(sleep=lambda _:None)}
        f=extract_methods(ROOT/'tasks/RichMan/mall/bondlings.py','Bondlings',('_locate_bondling_product',),ns)['_locate_bondling_product']
        h=SimpleNamespace(screenshot=Mock(),_locate_bondling_product_once=Mock(side_effect=RuntimeError('unknown')))
        self.assertIsNone(f(h,1));self.assertEqual(h.screenshot.call_count,3)
