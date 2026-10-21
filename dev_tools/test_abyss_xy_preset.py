"""Offline checks for the Abyss-only XY preset override."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from dev_tools.test_demon_preset_transition import extract, Timer, Marker
ROOT=Path(__file__).resolve().parents[1]

class XYPresetTests(unittest.TestCase):
    def run_case(self, mode, enable=True):
        clock=NS(now=0)
        log=Mock()
        fn=extract(ROOT/'tasks/AbyssShadows/xy_preset.py','XYPresetMixin',('switch_preset_team',),
            {'Timer':lambda n:Timer(clock,n),'logger':log,'time':NS(sleep=lambda n:None),
             'get_color':lambda *a: (0,0,0),'color_similar':lambda *a:False})['switch_preset_team']
        h=NS(device=NS(image=None)); h.clicks=[]; h.confirmed=False; h.frames=0
        for name in ('I_PRESET_ENSURE','I_PRESENT_LESS_THAN_5','I_PRESET','I_PRESET_WIT_NUMBER',
                     'O_PRESET','O_PRESET_FULL','C_PRESET_GROUP_1','C_PRESET_TEAM_5'):
            setattr(h,name,Marker(name))
        def screenshot():
            clock.now+=.25; h.frames+=1
        def appear(marker, **kw):
            return marker==h.I_PRESET_ENSURE and mode!='open_timeout' and not h.confirmed and not (mode=='vanished' and h.frames>1)
        def click(marker, **kw):
            h.clicks.append(marker); return True
        def appear_click(marker, **kw):
            if mode=='success' and appear(marker):
                click(marker); h.confirmed=True; return True
            return False
        h.screenshot=screenshot; h.appear=appear; h.click=click; h.appear_then_click=appear_click
        fn(h,enable,1,5)
        return h,log

    def test_fifth_team_and_confirm(self):
        h,log=self.run_case('success')
        self.assertIn(h.C_PRESET_TEAM_5,h.clicks)
        self.assertTrue(h.confirmed)
        log.info.assert_any_call('Click preset ensure success')

    def test_missing_button_without_click_is_not_success(self):
        h,log=self.run_case('vanished')
        log.warning.assert_called_once()
        self.assertFalse(h.confirmed)
        self.assertNotIn(unittest.mock.call('Click preset ensure success'),log.info.call_args_list)

    def test_open_timeout_returns(self):
        h,log=self.run_case('open_timeout');log.warning.assert_called_once();self.assertEqual(h.clicks,[])

    def test_disabled(self):
        h,log=self.run_case('success',False);self.assertEqual(h.frames,0)

    def test_abyss_override_is_first(self):
        tree=ast.parse((ROOT/'tasks/AbyssShadows/script_task.py').read_text('utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask')
        self.assertEqual(cls.bases[0].id,'XYPresetMixin')

if __name__=='__main__':unittest.main()
