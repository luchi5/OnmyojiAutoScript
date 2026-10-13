"""Smart-fill compatibility with a cached old parent task."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer
ROOT=Path(__file__).resolve().parents[1]

class OldParent:
    def _check_max_lv_manual(self, rarity):
        self.manual=rarity

def harness(frames):
    clock=FakeClock(1)
    tree=ast.parse((ROOT/'tasks/KekkaiActivation/script_task.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='check_max_lv']
    assert len(methods)==1, 'Duplicate check_max_lv definitions shadow smart fill'
    cls.bases=[ast.Name(id='OldParent',ctx=ast.Load())];cls.body=methods;cls.decorator_list=[]
    module=ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[]))
    ns={'OldParent':OldParent,'ShikigamiClass':SimpleNamespace(N='N'),'logger':Mock(),
        'Timer':lambda t:FakeTimer(clock,t),'GameStuckError':RuntimeError,
        'RuleImage':lambda **kw:Path(kw['file']).stem}
    exec(compile(module,'smart_fill','exec'),ns)
    h=ns['ScriptTask']();h.index=0;h.clicks=[];h.realm_goto_grown=Mock()
    h.I_REALM_SHIN='realm';h.I_SHI_GROWN='growth';h.I_UI_BACK_BLUE='back'
    def screenshot():
        h.frame=frames[min(h.index,len(frames)-1)];h.index+=1;clock.advance()
    h.screenshot=screenshot;h.appear=lambda r:r in h.frame
    def click(r,**kw):
        if r in h.frame:h.clicks.append(r);return True
        return False
    h.appear_then_click=click
    return h

class SmartFillTests(unittest.TestCase):
    def test_task_has_no_shadowed_methods(self):
        tree=ast.parse((ROOT/'tasks/KekkaiActivation/script_task.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask')
        names=[n.name for n in cls.body if isinstance(n,ast.FunctionDef)]
        self.assertEqual(len(names),len(set(names)))
        self.assertIn('_check_max_lv_manual',names)

    def test_old_parent_manual_signature_preserved(self):
        h=harness([set()]);h.check_max_lv('N');self.assertEqual(h.manual,'N')
    def test_smart_fill_does_not_call_old_parent(self):
        h=harness([{'realm_auto_fill'},{'realm_remove_all'},{'back'},{'realm'}])
        h.check_max_lv('N',auto_fill=True)
        self.assertEqual(h.clicks,['realm_auto_fill','back'])
        self.assertFalse(hasattr(h,'manual'))
    def test_already_filled_does_not_remove_all(self):
        h=harness([{'realm_remove_all'},{'realm'}]);h.check_max_lv(auto_fill=True)
        self.assertEqual(h.clicks,[])
    def test_missing_button_times_out(self):
        h=harness([set()])
        with self.assertRaisesRegex(RuntimeError,'20 seconds'):h.check_max_lv(auto_fill=True)

if __name__=='__main__':unittest.main()
