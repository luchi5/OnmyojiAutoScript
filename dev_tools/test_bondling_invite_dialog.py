"""Offline modal priority checks; no game, account or notification access."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from tasks.BondlingFairyland.invite_dialog import handle_continue_invite
from tasks.BondlingFairyland.member_invite_dialog import handle_member_invite

class Task:
    def __init__(self, visible=True, checked=False):
        self.visible, self.checked, self.clicks = visible, checked, []
    def screenshot(self):
        pass
    def appear(self, rule):
        return self.visible and (rule.name != 'UNCHECKED' or not self.checked)
    def appear_then_click(self, rule, **kwargs):
        self.clicks.append(rule.name)
        if rule.name == 'UNCHECKED':
            self.checked = True
        else:
            self.visible = False
        return True

class InviteDialogTests(TestCase):
    @patch('tasks.BondlingFairyland.invite_dialog.sleep')
    def test_leader_checks_default_before_confirm(self, _sleep):
        task = Task()
        self.assertTrue(handle_continue_invite(task))
        self.assertEqual(task.clicks, ['UNCHECKED', 'CONFIRM'])
    def test_leader_can_confirm_without_changing_default(self):
        task = Task()
        self.assertTrue(handle_continue_invite(task, False))
        self.assertEqual(task.clicks, ['CONFIRM'])
    def test_other_screens_are_untouched(self):
        task = Task(visible=False)
        self.assertIsNone(handle_continue_invite(task))
        self.assertFalse(handle_member_invite(task))
        self.assertEqual(task.clicks, [])
    def test_actual_member_entry_handles_modal_before_background_invite(self):
        source = Path(__file__).resolve().parents[1] / 'tasks/BondlingFairyland/script_task.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'check_then_accept')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        task = Task()
        self.assertTrue(namespace['check_then_accept'](task))
        self.assertEqual(task.clicks, ['CONFIRM'])
