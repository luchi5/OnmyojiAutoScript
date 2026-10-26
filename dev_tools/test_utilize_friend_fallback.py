import ast
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


Friend = Enum('Friend', 'SAME_SERVER DIFFERENT_SERVER')
class Empty(Exception):
    pass


source = ast.parse(Path('tasks/KekkaiUtilize/script_task.py').read_text(encoding='utf-8'))
task = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')


def harness(*names):
    ns = dict(logger=Mock(), SelectFriendList=Friend, UtilizeListsEmpty=Empty,
              UtilizeNavigationError=RuntimeError,
              Timer=lambda _: SimpleNamespace(start=lambda: SimpleNamespace(reached=lambda: False)),
              time=SimpleNamespace(sleep=Mock()))
    nodes = [n for n in task.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'isolated-task', 'exec'), ns)
    h = SimpleNamespace(ap_max_num=0, jade_max_num=0)
    for node in nodes:
        setattr(h, node.name, ns[node.name].__get__(h))
    return h


class FallbackTests(unittest.TestCase):
    def make(self, outcomes):
        h = harness('_select_with_friend_fallback')
        h._reset_utilize_friend_list = Mock()
        pending = iter(outcomes)
        def select():
            selected, empty = next(pending)
            h._scan_no_cards = empty
            return selected
        h._select_optimal_resource_card = Mock(side_effect=select)
        return h

    def test_same_has_card_no_switch(self):
        h = self.make([(True, False)])
        self.assertTrue(h._select_with_friend_fallback(Friend.SAME_SERVER))
        h._reset_utilize_friend_list.assert_not_called()

    def test_other_has_card(self):
        h = self.make([(False, True), (True, False)])
        self.assertTrue(h._select_with_friend_fallback(Friend.SAME_SERVER))
        h._reset_utilize_friend_list.assert_called_once_with(Friend.DIFFERENT_SERVER)

    def test_both_empty_ends_round(self):
        h = self.make([(False, True), (False, True)])
        with self.assertRaises(Empty):
            h._select_with_friend_fallback(Friend.SAME_SERVER)

    def test_incomplete_or_nonperfect_does_not_switch(self):
        h = self.make([(False, False)])
        self.assertFalse(h._select_with_friend_fallback(Friend.SAME_SERVER))
        h._reset_utilize_friend_list.assert_not_called()

    def test_other_nonperfect_keeps_group_for_confirmation(self):
        h = self.make([(False, True), (False, False), (True, False)])
        self.assertFalse(h._select_with_friend_fallback(Friend.SAME_SERVER))
        self.assertEqual(h._utilize_friend, Friend.DIFFERENT_SERVER)
        self.assertTrue(h._select_with_friend_fallback(h._utilize_friend))
        self.assertEqual(h._reset_utilize_friend_list.call_count, 1)

    def test_no_card_scan_requires_four_frames(self):
        h = harness('_current_select_best')
        h.screenshot = Mock()
        h.in_utilize_list = Mock(return_value=True)
        h.device = SimpleNamespace(image=None)
        h.order_targets = SimpleNamespace(find_everyone=Mock(return_value=[]))
        h.perform_swipe_action = Mock()
        self.assertIsNone(h._current_select_best())
        self.assertTrue(h._scan_no_cards)
        self.assertEqual(h.screenshot.call_count, 4)
        self.assertEqual(h.perform_swipe_action.call_count, 3)

    def test_wrong_page_is_not_empty(self):
        h = harness('_current_select_best')
        h.screenshot = Mock()
        h.in_utilize_list = Mock(return_value=False)
        with self.assertRaises(RuntimeError):
            h._current_select_best()
        self.assertFalse(h._scan_no_cards)

    def test_end_round_schedules_regular_interval(self):
        h = harness('check_utilize_add')
        con = SimpleNamespace(select_friend_list=Friend.SAME_SERVER,
                              shikigami_class='默认/全部', shikigami_order=1)
        h.config = SimpleNamespace(kekkai_utilize=SimpleNamespace(utilize_config=con))
        h.realm_goto_grown = Mock()
        h.screenshot = Mock()
        h.I_UTILIZE_ADD = object()
        h.appear = Mock(return_value=True)
        h.grown_goto_utilize = Mock(return_value=True)
        h.run_utilize = Mock(side_effect=Empty)
        h.back_realm = Mock()
        h.set_next_run = Mock()
        h.check_utilize_add()
        h.run_utilize.assert_called_once()
        h.back_realm.assert_called_once()
        h.set_next_run.assert_called_once_with(task='KekkaiUtilize', finish=True,
                                              success=False, server=False)


if __name__ == '__main__':
    unittest.main()
