import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

class PreferenceTests(unittest.TestCase):
    def setup_handler(self):
        source=Path(__file__).resolve().parents[1]/'tasks/Delegation/resource_preference.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        tree.body=[n for n in tree.body if not isinstance(n, ast.ImportFrom)]
        self.now=0
        self.rows=[SimpleNamespace(ocr_text=t,box=((x,y),(x+30,y),(x+30,y+20),(x,y+20)))
                   for t,x,y in [('偏向获取什么资源',260,40),('金币',100,200),('选定',330,280)]]
        ns={'monotonic':lambda:self.now,'RuleOcr':lambda **kw:SimpleNamespace(detect_and_ocr=lambda im:self.rows),
            'RuleClick':lambda **kw:SimpleNamespace(**kw),'GameStuckError':RuntimeError}
        exec(compile(tree,str(source),'exec'),ns)
        return ns['ResourcePreference'](),SimpleNamespace(device=SimpleNamespace(image=None),click=Mock(return_value=True))
    def test_gold_then_confirm_once(self):
        h,t=self.setup_handler()
        self.assertTrue(h.handle(t))
        self.assertEqual(t.click.call_args.args[0].name,'delegation_preference_choose')
        h.handle(t)
        self.assertEqual(t.click.call_count,1)
        self.now=1
        h.handle(t)
        self.assertEqual(t.click.call_args.args[0].name,'delegation_preference_confirm')
        self.now=2
        h.handle(t)
        self.assertEqual(t.click.call_count,2)
        self.rows=[]
        self.assertFalse(h.handle(t))
    def test_missing_title_never_clicks(self):
        h,t=self.setup_handler();self.rows=self.rows[1:]
        self.assertFalse(h.handle(t));t.click.assert_not_called()
    def test_missing_coin_blocks_background_without_confirming(self):
        h,t=self.setup_handler();self.rows=[self.rows[0],self.rows[2]]
        self.assertTrue(h.handle(t));t.click.assert_not_called()
        self.now=16
        with self.assertRaises(RuntimeError): h.handle(t)
