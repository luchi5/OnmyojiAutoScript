"""Regression checks using incident screenshots and bounded purchase flows."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import cv2
import numpy as np
from module.atom.image import RuleImage
from tasks.GameUi.assets import GameUiAssets
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.borrow_selection import beginner_travel
from tasks.RichMan.mall.tab_state import special_tab_state

ROOT=Path(__file__).resolve().parents[1]
def frame(name):
    return cv2.cvtColor(cv2.imread(str(ROOT/'dev_tools/fixtures'/name)),cv2.COLOR_BGR2RGB)
def subject(path, methods):
    tree=ast.parse((ROOT/path).read_text(encoding='utf-8'))
    nodes=[n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    ns={'special_tab_state':special_tab_state,'RuleImage':RuleImage,'logger':Mock(),'time':SimpleNamespace(sleep=lambda _:None),'SpecialRoom':object}
    cls=ast.ClassDef(name='Subject',bases=[],keywords=[],body=nodes,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'<production>','exec'),ns)
    return ns['Subject']()
class ShopRegressions(unittest.TestCase):
    def navbar(self,image):
        h=subject('tasks/RichMan/mall/navbar.py',{'_enter_special','_enter_bondlings','_enter_optional_tab'})
        h.device=SimpleNamespace(image=image);h.screenshot=Mock();h.click=Mock();h._enter_sundry=Mock();h._enter_scales=Mock()
        h.appear=lambda rule:rule.match(image)
        h.I_MALL_BONDLINGS_SURE=RichManAssets.I_MALL_BONDLINGS_SURE
        h.I_MALL_BONDLINGS_ON=RichManAssets.I_MALL_BONDLINGS_ON
        return h
    def test_returning_travel_title_and_restriction(self):
        im=frame('borrow_selection/returning.png')
        self.assertTrue(GameUiAssets.I_CHECK_TRAVEL_TITLE.match(im))
        self.assertFalse(beginner_travel(im))
        for name in ['shop8_locked.png','shop9_newcomer.png']:
            self.assertFalse(GameUiAssets.I_CHECK_TRAVEL_TITLE.match(frame(name)))
            self.assertFalse(beginner_travel(frame(name)))
    def test_locked_bondlings_stops_without_clicking(self):
        h=self.navbar(frame('shop8_locked.png'))
        self.assertFalse(h._enter_bondlings());h.click.assert_not_called()
        self.assertEqual(h.screenshot.call_count,4)
    def test_newcomer_special_is_not_falsely_selected_and_clicks_actual_location(self):
        h=self.navbar(frame('shop9_newcomer.png'))
        self.assertFalse(h._enter_special())
        self.assertEqual(h.click.call_count,3)
        for call in h.click.call_args_list:
            self.assertEqual(call.args[0].roi_front[:2],[1199,316])
    def test_special_selected_at_shifted_position(self):
        im=frame('shop9_newcomer.png')
        selected=cv2.cvtColor(cv2.imread(str(ROOT/'tasks/RichMan/mall/navbar/navbar_side_sure_special.png')),cv2.COLOR_BGR2RGB)
        im[292:366,1172:1242]=selected
        self.assertTrue(special_tab_state(im)[2])
        h=self.navbar(im);self.assertTrue(h._enter_special());h.click.assert_not_called()
    def special(self,**options):
        h=subject('tasks/RichMan/mall/special.py',{'execute_special'})
        h._enter_special=Mock(return_value=True);h.screenshot=Mock();h.appear=Mock(return_value=False);h.swipe=Mock(return_value=True)
        for n in ['I_SP_BUY_TOTEM','I_SP_BUY_MEDIUM','I_SP_BUY_LOW','I_SP_SWIPE_CHECK','S_SP_DOWN']:setattr(h,n,n)
        con=SimpleNamespace(enable=True,totem_pass=True,medium_bondling_discs=0,low_bondling_discs=0)
        for k,v in options.items():setattr(con,k,v)
        h._special_totom=Mock();h._special_medium=Mock();h._special_low=Mock()
        return h,con
    def test_missing_products_have_bounded_scrolls(self):
        h,c=self.special();h.execute_special(c);self.assertEqual(h.swipe.call_count,6)
    def test_disabled_products_do_not_require_search(self):
        h,c=self.special();h.appear=lambda rule:rule=='I_SP_BUY_TOTEM'
        h.execute_special(c);h.swipe.assert_not_called();h._special_totom.assert_called_once()
    def test_failed_entry_never_purchases_or_scrolls(self):
        h,c=self.special();h._enter_special.return_value=False;h.execute_special(c)
        h.swipe.assert_not_called();h._special_totom.assert_not_called()
    def test_all_products_disabled_skip_entry(self):
        h,c=self.special(totem_pass=False);h.execute_special(c);h._enter_special.assert_not_called()
if __name__=='__main__':unittest.main()
