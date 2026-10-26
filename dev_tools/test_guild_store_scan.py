import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

class GuildScanTests(unittest.TestCase):
    def task(self, skin=False):
        tree=ast.parse(Path('tasks/RichMan/guild.py').read_text(encoding='utf-8'))
        methods=[n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name in {'execute_guild','_guild_buy_more','check_remain'}]
        ns={'re':re,'GuildStore':object,'RuleImage':object,'logger':Mock(),'time':SimpleNamespace(sleep=Mock()),'page_guild':'guild','ButtonException':RuntimeError}
        cls=ast.ClassDef(name='Harness',bases=[],keywords=[],body=methods,decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'test','exec'),ns)
        t=ns['Harness']()
        for name in ['I_GUILD_CLOSE_RED','I_GUILD_SHRINE','I_GUILD_STORE','I_GUILD_BLUE','I_GUILD_SCRAP','I_GUILD_SKIN','I_UI_BACK_YELLOW','S_GUILD_STORE']:
            setattr(t,name,SimpleNamespace(name=name))
        t.goto_page=Mock();t.screenshot=Mock();t.swipe=Mock(return_value=None);t.wait_until_stable=Mock()
        t.appear=lambda marker: marker is t.I_GUILD_CLOSE_RED or marker is t.I_GUILD_SHRINE or (skin and marker is t.I_GUILD_SKIN)
        t.appear_then_click=Mock();t._guild_mystery_amulet=Mock();t._guild_black_daruma_scrap=Mock();t._guild_skin_ticket=Mock(return_value=True)
        return t,ns
    def test_eight_actual_swipes_even_when_swipe_returns_none(self):
        t,ns=self.task()
        t.execute_guild(SimpleNamespace(enable=True,mystery_amulet=True,black_daruma_scrap=True,skin_ticket=5))
        self.assertEqual(t.swipe.call_count,8)
        self.assertEqual(sum(c.args==(2,) for c in ns['time'].sleep.call_args_list),8)
        self.assertTrue(all(not c.kwargs for c in t.swipe.call_args_list))
    def test_skin_receives_configured_quantity(self):
        t,ns=self.task(skin=True)
        t.execute_guild(SimpleNamespace(enable=True,mystery_amulet=False,black_daruma_scrap=False,skin_ticket=5))
        t._guild_skin_ticket.assert_called_once_with(5)
        t.swipe.assert_not_called()
    def test_locked_skin_is_processed_once_without_repeated_swipes(self):
        t,ns=self.task(skin=True)
        t._guild_skin_ticket.return_value=False
        t.execute_guild(SimpleNamespace(enable=True,mystery_amulet=False,black_daruma_scrap=False,skin_ticket=5))
        t._guild_skin_ticket.assert_called_once_with(5)
        t.swipe.assert_not_called()
    def test_level_locked_products_are_unavailable_and_unknown_text_still_raises(self):
        t,ns=self.task(skin=True)
        t.I_GUILD_SKIN.roi_front=[570,438,71,72]
        t.O_GUILD_REMAIN=SimpleNamespace(roi=[0,0,154,33],ocr=Mock())
        t.device=SimpleNamespace(image=None)
        for text in ['5级寮解锁','6级寮解锁','6 级寮 解锁','本周剩余数量0']:
            with self.subTest(text=text):
                t.O_GUILD_REMAIN.ocr.return_value=text
                self.assertEqual(t.check_remain(t.I_GUILD_SKIN),0)
        t.O_GUILD_REMAIN.ocr.return_value=''
        with self.assertRaises(RuntimeError):t.check_remain(t.I_GUILD_SKIN)
        t.O_GUILD_REMAIN.ocr.return_value='本周剩余数量5'
        self.assertEqual(t.check_remain(t.I_GUILD_SKIN),5)
    def test_quantity_rejects_low_score_and_confirms_exact_target_twice(self):
        t,ns=self.task()
        clock=[0]
        ns['time'].monotonic=lambda:clock[0]
        ns['time'].sleep=lambda delay:clock.__setitem__(0,clock[0]+delay)
        for name in ['I_BUY_PLUS','I_BUY_ADD','I_BUY_SUB','C_BUY_MORE','C_BUY_CANCEL']:
            setattr(t,name,name)
        t.appear=lambda marker:True
        t.click=Mock();t.ui_reward_appear_click=Mock(return_value=True)
        t.O_BUY_NUMBER=SimpleNamespace(roi=[],crop=lambda *a:None,pre_process=lambda x:x,
            model=SimpleNamespace(ocr_single_line=Mock(side_effect=[('9',.59),('1',.99),('1',.99),('2',.99),('2',.99)])))
        t.device=SimpleNamespace(image=None)
        self.assertTrue(t._guild_buy_more('product',2))
        self.assertEqual([c.args[0] for c in t.click.call_args_list],['product','I_BUY_ADD','C_BUY_MORE'])

    def test_uncertain_quantity_cancels_without_purchase(self):
        t,ns=self.task()
        clock=[0]
        ns['time'].monotonic=lambda:clock[0]
        ns['time'].sleep=lambda delay:clock.__setitem__(0,clock[0]+delay)
        t.I_BUY_PLUS='plus';t.C_BUY_CANCEL='cancel'
        t.appear=lambda marker:True;t.click=Mock();t.device=SimpleNamespace(image=None)
        t.O_BUY_NUMBER=SimpleNamespace(roi=[],crop=lambda *a:None,pre_process=lambda x:x,
            model=SimpleNamespace(ocr_single_line=Mock(return_value=('9',.59))))
        self.assertFalse(t._guild_buy_more('product',5))
        self.assertEqual([c.args[0] for c in t.click.call_args_list],['product','cancel'])

if __name__=='__main__': unittest.main()
