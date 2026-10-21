"""Offline sidebar navigation tests; no game inputs or backend imports."""
import ast
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SidebarTests(unittest.TestCase):
    def harness(self, found_after=0, folded=False, destination=True, guild=True):
        tree = ast.parse((ROOT / 'tasks/WeeklyTrifles/script_task.py').read_text('utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        ns = {'sleep': Mock(), 'logger': Mock(), 'TaskEnd': RuntimeError, 'page_guild': 'guild'}
        nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('_open_touch_fish', '_save_touch_fish')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<sidebar>', 'exec'), ns)
        state = {'expanded': not folded, 'entered': False, 'swipes': 0}
        h = SimpleNamespace(screenshot=Mock(), goto_page=Mock(), set_next_run=Mock(),
                            device=SimpleNamespace(), I_CHECK_TOUCH_FISH='fish',
                            I_CHECK_GUILD='guild', I_WT_FOLD_WINDOW='expanded',
                            I_WT_OPEN_FOLD_WINDOW='expand', I_GUILD_GOTO_TF='entry')
        h.appear = Mock(side_effect=lambda target: {
            'fish': state['entered'], 'guild': guild, 'expanded': state['expanded']}[target])
        def click(target, **kwargs):
            if target == 'expand':
                state['expanded'] = True
                return True
            if found_after is not None and state['swipes'] >= found_after:
                state['entered'] = destination
                return True
            return False
        h.appear_then_click = Mock(side_effect=click)
        def swipe(**kwargs):
            state['swipes'] += 1
        h.device.swipe = Mock(side_effect=swipe)
        for name in ('_open_touch_fish', '_save_touch_fish'):
            setattr(h, name, MethodType(ns[name], h))
        return h

    def test_visible_entry_needs_no_scroll(self):
        h = self.harness()
        self.assertTrue(h._open_touch_fish())
        h.device.swipe.assert_not_called()

    def test_temporary_events_require_scrolling_inside_sidebar(self):
        for needed in (1, 2, 4):
            h = self.harness(found_after=needed)
            self.assertTrue(h._open_touch_fish())
            self.assertEqual(h.device.swipe.call_count, needed)
            for call in h.device.swipe.call_args_list:
                self.assertEqual(call.kwargs['p1'], (150, 450))
                self.assertEqual(call.kwargs['p2'], (150, 210))

    def test_folded_sidebar_is_expanded_first(self):
        h = self.harness(found_after=1, folded=True)
        self.assertTrue(h._open_touch_fish())
        self.assertEqual(h.appear_then_click.call_args_list[0].args, ('expand',))

    def test_missing_entry_is_bounded_and_never_navigates_back(self):
        h = self.harness(found_after=None)
        self.assertFalse(h._open_touch_fish())
        self.assertEqual(h.device.swipe.call_count, 4)
        h.goto_page.assert_not_called()

    def test_wrong_page_never_swipes(self):
        h = self.harness(guild=False)
        self.assertFalse(h._open_touch_fish())
        h.device.swipe.assert_not_called()
        h.appear_then_click.assert_not_called()

    def test_failed_expansion_is_bounded(self):
        h = self.harness(folded=True)
        h.appear_then_click.side_effect = None
        h.appear_then_click.return_value = False
        self.assertFalse(h._open_touch_fish())
        self.assertEqual(h.appear_then_click.call_count, 3)
        h.device.swipe.assert_not_called()

    def test_failed_transition_does_not_click_entry_repeatedly(self):
        h = self.harness(destination=False)
        self.assertFalse(h._open_touch_fish())
        h.appear_then_click.assert_called_once_with('entry', interval=1)
        h.device.swipe.assert_not_called()

    def test_failure_ends_task_before_storage_actions(self):
        h = self.harness(found_after=None)
        with self.assertRaisesRegex(RuntimeError, 'stop this attempt'):
            h._save_touch_fish()
        h.goto_page.assert_called_once_with('guild')
        h.set_next_run.assert_called_once_with(task='WeeklyTrifles', success=False)


if __name__ == '__main__':
    unittest.main()
