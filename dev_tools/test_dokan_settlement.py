import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

class SettlementTests(unittest.TestCase):
    def test_reward_and_warning_choose_safe_actions(self):
        path = Path(__file__).resolve().parents[1]/'tasks/Dokan/settlement_recovery.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
        ns = {'RuleImage':lambda **kw:SimpleNamespace(**kw), 'RuleClick':lambda **kw:SimpleNamespace(**kw)}
        exec(compile(tree,str(path),'exec'), ns)
        for visible, expected in (([],None),(['WARNING'],None),(['CONTINUE'],'ADVANCE'),(['CONTINUE','WARNING'],'CANCEL')):
            task = SimpleNamespace(appear=lambda r:any(r is ns[name] for name in visible),click=Mock())
            self.assertEqual(ns['handle_settlement'](task), expected is not None)
            if expected:
                task.click.assert_called_once_with(ns[expected],interval=1.5)
            else:
                task.click.assert_not_called()
