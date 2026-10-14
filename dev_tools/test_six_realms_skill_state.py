import ast
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer
ROOT=Path(__file__).resolve().parents[1]

class SkillStateTests(unittest.TestCase):
    def test_level_persists_and_unknown_does_not_spend(self):
        tree=ast.parse((ROOT/'tasks/SixRealms/moon_sea/skills.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
        cls.bases=[]
        cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('cnt_skill101','restore_skill101','needs_skill101')]
        with tempfile.TemporaryDirectory() as folder:
            ns={'Path':lambda _:Path(folder),'json':json,'logger':Mock()}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'fixture','exec'),ns)
            kind=ns['MoonSeaSkills'];h=kind();h.config=SimpleNamespace(config_name='test')
            h.restore_skill101();self.assertFalse(h.needs_skill101())
            h.cnt_skill101=5
            resumed=kind();resumed.config=h.config;resumed.restore_skill101()
            self.assertEqual(resumed.cnt_skill101,5);self.assertFalse(resumed.needs_skill101())
            h.cnt_skill101=1;self.assertTrue(h.needs_skill101())

    def test_summon_confirms_dialog_instead_of_returning_to_cancel(self):
        clock=FakeClock(1)
        h=SimpleNamespace(needs_skill101=lambda:True,device=SimpleNamespace(image=None),
                          map_ocr=SimpleNamespace(detect_and_ocr=Mock(side_effect=[
                              *[ [SimpleNamespace(ocr_text='1回合后迎战月读')] for _ in range(3)],
                              [SimpleNamespace(ocr_text='是否消耗300万相铃召唤宁息之屿')],
                              [SimpleNamespace(ocr_text='月之海')]])))
        h.I_LAND_SHOP='shop'
        h.screenshot=clock.advance;h.appear=lambda rule:rule=='confirm';h.click=Mock();h.in_main=lambda:True
        h.appear_then_click=Mock()
        ns={'Timer':lambda n:FakeTimer(clock,n),'logger':Mock(),'GameStuckError':RuntimeError,
            'RuleImage':lambda **kw:'confirm', 're':re}
        f=extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py','MoonSeaMap',('activate_store',),ns)['activate_store']
        module=ModuleType('tasks.GlobalGame.dialog_buttons');module.DialogButtonRule=lambda **kw:'confirm'
        with patch.dict('sys.modules',{'tasks.GlobalGame.dialog_buttons':module}):self.assertTrue(f(h))
        self.assertEqual(h.map_ocr.detect_and_ocr.call_count, 5)
        h.map_ocr.detect_and_ocr.assert_called_with(image=None)
        h.click.assert_called_once_with('confirm',interval=1.5)
        h.appear_then_click.assert_not_called()

    def test_misread_seventeen_as_one_never_clicks_summon(self):
        h=SimpleNamespace(needs_skill101=lambda:True, screenshot=Mock(),
                          device=SimpleNamespace(image=None),
                          map_ocr=SimpleNamespace(detect_and_ocr=Mock(side_effect=[
                              [SimpleNamespace(ocr_text='1回合后迎战月读')],
                              [SimpleNamespace(ocr_text='17回合后迎战月读')]])))
        h.I_LAND_SHOP='shop';h.appear=lambda _:False
        h.click=Mock();h.appear_then_click=Mock()
        ns={'re':re,'logger':Mock()}
        f=extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py','MoonSeaMap',('activate_store',),ns)['activate_store']
        self.assertFalse(f(h))
        h.click.assert_not_called();h.appear_then_click.assert_not_called()

    def test_existing_shop_never_clicks_summon(self):
        h=SimpleNamespace(needs_skill101=lambda:True, screenshot=Mock(),
                          I_LAND_SHOP='shop', appear=lambda rule:rule=='shop',
                          device=SimpleNamespace(image=None),
                          map_ocr=SimpleNamespace(detect_and_ocr=lambda **kw:[
                              SimpleNamespace(ocr_text='1回合后迎战月读')]))
        h.click=Mock();h.appear_then_click=Mock()
        f=extract_methods(ROOT/'tasks/SixRealms/moon_sea/map.py','MoonSeaMap',
                          ('activate_store',),{'re':re,'logger':Mock()})['activate_store']
        self.assertFalse(f(h))
        h.click.assert_not_called();h.appear_then_click.assert_not_called()

    def test_map_ocr_interface_matches_real_base(self):
        # Inspect the real implementation without loading OCR or a device.
        tree=ast.parse((ROOT/'module/ocr/base_ocr.py').read_text(encoding='utf-8'))
        methods={node.name for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)}
        self.assertIn('detect_and_ocr',methods)
        tree=ast.parse((ROOT/'tasks/SixRealms/moon_sea/map.py').read_text(encoding='utf-8'))
        calls=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call)
               and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Attribute)
               and node.func.value.attr=='map_ocr']
        self.assertTrue(calls)
        self.assertEqual(set(calls),{'detect_and_ocr'})

if __name__=='__main__':unittest.main()
