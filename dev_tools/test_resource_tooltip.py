import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

class ResourceTooltipTests(unittest.TestCase):
    def test_requires_title_and_description(self):
        path = Path(__file__).resolve().parents[1]/'tasks/GlobalGame/resource_tooltip.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
        ns = {'RuleImage': lambda **kwargs: SimpleNamespace(**kwargs), 'RuleClick': lambda **kwargs: SimpleNamespace(**kwargs)}
        exec(compile(tree, str(path), 'exec'), ns)
        title, back = ns['TITLE'], ns['DESCRIPTION']
        for visible, expected in (([],False),([title],False),([back],False),([title,back],True)):
            task = SimpleNamespace(appear=lambda r:any(r is v for v in visible),click=Mock(return_value=True))
            self.assertEqual(ns['close_coin_tooltip'](task), expected)
            if expected:
                task.click.assert_called_once_with(ns['DISMISS'], interval=1.5)
            else:
                task.click.assert_not_called()
