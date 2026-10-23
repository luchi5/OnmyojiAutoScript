"""Borrowing roster recognition and transitions; no live game clicks."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from tasks.RichMan.borrow_selection import borrow_cards, empty_borrow_slots, beginner_travel, borrow_only_page

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'dev_tools/fixtures/borrow_selection'


class RecognitionTests(unittest.TestCase):
    def test_actual_account_13_has_two_empty_slots_and_fourth_card_selected(self):
        image = cv2.imread(str(FIXTURES / 'partial.png'))
        cards = borrow_cards(image)
        self.assertEqual(empty_borrow_slots(image), 2)
        self.assertEqual(sum(c[2] for c in cards), 1)
        self.assertTrue(cards[3][2])
        self.assertFalse(cards[0][2])
        self.assertFalse(beginner_travel(image))

    def test_manually_filled_reference_has_three_selected_cards(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[220:590, 290:1050] = cv2.imread(str(FIXTURES / 'roster.png'))
        self.assertEqual(sum(c[2] for c in borrow_cards(image)), 3)

    def test_beginner_banner(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[:80] = cv2.imread(str(FIXTURES / 'beginner.png'))
        self.assertTrue(beginner_travel(image))
        self.assertFalse(beginner_travel(np.zeros_like(image)))

    def test_unknown_or_missing_template_uses_normal_account(self):
        self.assertFalse(beginner_travel(None))
        self.assertFalse(beginner_travel(np.zeros((10, 10, 3), np.uint8)))
        with patch('tasks.RichMan.borrow_selection.extra_template', return_value=None):
            self.assertFalse(beginner_travel(np.zeros((720, 1280, 3), np.uint8)))

    def test_actual_borrow_only_screen(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[:150] = cv2.imread(str(FIXTURES / 'borrow_only.png'))
        self.assertTrue(borrow_only_page(image))
        self.assertFalse(borrow_only_page(cv2.imread(str(FIXTURES / 'partial.png'))))


class FlowTests(unittest.TestCase):
    def harness(self, states):
        tree = ast.parse((ROOT / 'tasks/RichMan/thousand_things.py').read_text('utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                   and n.name in {'_tt_prepare_borrow', '_tt_leave_borrow', 'execute_tt'}]
        clock = SimpleNamespace(sleep=Mock())
        ns = dict(time=clock, logger=Mock(), ConfigThousandThings=object,
                  page_travel='travel', TaskEnd=RuntimeError,
                  borrow_cards=lambda state: [(100 + i * 100, 300, i < 3 - state['empty'])
                                               for i in range(6)] if state.get('cards', True) else [],
                  empty_borrow_slots=lambda state: state['empty'],
                  beginner_travel=lambda state: state.get('beginner', False),
                  borrow_only_page=lambda state: state.get('borrow_only', False))
        node = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<flow>', 'exec'), ns)
        h = ns['Subject']()
        h.device = SimpleNamespace(image=None, click=Mock())
        for name in ['I_UI_CONFIRM', 'I_TT_SHIKIGAMI_REPLACE', 'I_TT_CONFIRM', 'I_TT_ENTER',
                     'I_UI_BACK_RED', 'I_UI_BACK_YELLOW', 'I_TT_CHECK', 'I_TT_TICKET_BULE', 'I_TT_BLACK', 'I_TT_AP']:
            setattr(h, name, name)
        remaining = iter(states)
        current = states[0]
        def screenshot():
            nonlocal current
            current = next(remaining, current)
            h.device.image = current
        h.screenshot = screenshot
        h.appear = lambda marker: marker in h.device.image.get('visible', {'I_TT_CONFIRM'})
        h.click = Mock()
        h.appear_then_click = Mock()
        h.goto_page = Mock()
        h.tt_earn_money = Mock()
        return h

    def test_first_time_fills_three_slots(self):
        h = self.harness([{'empty': n} for n in [3, 2, 1, 0]])
        self.assertTrue(h._tt_prepare_borrow())
        self.assertEqual(h.device.click.call_count, 3)
        h.click.assert_not_called()

    def test_partial_roster_only_fills_missing_slots(self):
        for count in [1, 2]:
            h = self.harness([{'empty': n} for n in range(count, -1, -1)])
            self.assertTrue(h._tt_prepare_borrow())
            self.assertEqual(h.device.click.call_count, count)

    def test_full_roster_chooses_unselected_card_then_replaces_once(self):
        h = self.harness([{'empty': 0},
                          {'empty': 0, 'visible': {'I_TT_SHIKIGAMI_REPLACE'}},
                          {'empty': 0, 'visible': {'I_UI_CONFIRM'}}, {'empty': 0}])
        self.assertTrue(h._tt_prepare_borrow())
        self.assertEqual(h.device.click.call_args.args, (400, 300))
        self.assertEqual([c.args[0] for c in h.click.call_args_list],
                         ['I_TT_SHIKIGAMI_REPLACE', 'I_UI_CONFIRM'])

    def test_unchanged_card_is_never_clicked_repeatedly(self):
        h = self.harness([{'empty': 2}])
        self.assertFalse(h._tt_prepare_borrow())
        self.assertEqual(h.device.click.call_count, 1)

    def test_no_eligible_cards_stops(self):
        h = self.harness([{'empty': 3, 'cards': False}])
        self.assertFalse(h._tt_prepare_borrow())
        h.device.click.assert_not_called()

    def test_stuck_replacement_is_bounded(self):
        h = self.harness([{'empty': 0, 'visible': {'I_TT_SHIKIGAMI_REPLACE'}}])
        self.assertFalse(h._tt_prepare_borrow())
        h.click.assert_called_once_with('I_TT_SHIKIGAMI_REPLACE')

    def test_beginner_skips_lending_but_checks_shop(self):
        h = self.harness([{'empty': 3, 'beginner': True,
                           'visible': {'I_TT_CHECK', 'I_TT_ENTER'}}])
        h.execute_tt(SimpleNamespace(enable=True, earn_money=True, mystery_amulet=False,
                                     black_daruma_fragment=False, ap=False))
        h.tt_earn_money.assert_not_called()
        h.goto_page.assert_called_once_with('travel')
        h.click.assert_not_called()

    def test_unknown_account_still_lends_and_buys_configured_goods(self):
        h = self.harness([{'empty': 3, 'visible': {'I_TT_CHECK', 'I_TT_ENTER'}}])
        h.tt_buy_black_daruma_scrap = Mock()
        h.tt_buy_ap = Mock()
        h.execute_tt(SimpleNamespace(enable=True, earn_money=True, mystery_amulet=False,
                                    black_daruma_fragment=True, ap=True))
        h.tt_earn_money.assert_called_once()
        h.tt_buy_black_daruma_scrap.assert_called_once()
        h.tt_buy_ap.assert_called_once()

    def test_borrow_only_page_returns_to_map_without_lending(self):
        h = self.harness([{'empty': 3, 'borrow_only': True},
                          {'empty': 3, 'borrow_only': True},
                          {'empty': 3, 'visible': {'I_TT_ENTER', 'I_TT_CHECK'}}])
        h.execute_tt(SimpleNamespace(enable=True, earn_money=True, mystery_amulet=False, black_daruma_fragment=False, ap=False))
        h.tt_earn_money.assert_called_once()
        h.goto_page.assert_called_once_with('travel')
        h.appear_then_click.assert_called_once_with('I_UI_BACK_YELLOW', interval=1)

    def test_unrecognized_exit_stops_task(self):
        h = self.harness([{'empty': 3, 'visible': set()}])
        with self.assertRaises(RuntimeError):
            h._tt_leave_borrow()
        self.assertEqual(h.appear_then_click.call_count, 10)


if __name__ == '__main__':
    unittest.main()
