"""Offline ticket-event guards and real UI crops; no device or service imports."""

import ast
from contextlib import nullcontext
from pathlib import Path
import re
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

import cv2

from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/ActivityShikigami/current_event.py'
SCRIPT = ROOT / 'tasks/ActivityShikigami/script_task.py'
FIXTURES = ROOT / 'dev_tools/fixtures/activity_202610'


class GameStuckError(Exception):
    pass


class RequestHumanTakeover(Exception):
    pass


def load_event(clock):
    logger = ModuleType('module.logger')
    logger.logger = FakeLogger()
    with patch.dict(sys.modules, {'module.logger': logger}):
        from module.atom.image import RuleImage
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    context = dict(re=re, RuleImage=RuleImage, RuleOcr=lambda **kwargs: SimpleNamespace(**kwargs),
                   Timer=lambda *a, **k: FakeTimer(clock, *a, **k), logger=FakeLogger(),
                   GameStuckError=GameStuckError, RequestHumanTakeover=RequestHumanTakeover,
                   battle_wait_strategy=lambda **kwargs: nullcontext(),
                   battle_wait_options=lambda **kwargs: nullcontext())
    exec(compile(tree, str(SOURCE), 'exec'), context)
    return context


class TicketEventTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(step=0.5)
        self.ns = load_event(self.clock)
        self.task = self.ns['CurrentEventTicket']()
        self.task.device = SimpleNamespace(image=None)
        self.task.screenshot = self.clock.advance
        self.task.click = Mock()
        self.task.put_status = Mock()
        self.task.count_map = {'pass': 0}

    def image(self, name):
        return cv2.cvtColor(cv2.imread(str(FIXTURES / (name + '.png'))), cv2.COLOR_BGR2RGB)

    def test_real_ticket_and_stamina_pages_are_distinct(self):
        for name, expected in [('ticket_before', True), ('ticket_after', True),
                               ('ticket_unlock', True), ('stamina', False),
                               ('event_main', False), ('event_list', False), ('event_map', False), ('reward', False)]:
            with self.subTest(name=name):
                frame = self.image(name)
                self.task.appear = lambda rule: rule.match(frame)
                self.assertEqual(self.task.ticket_page_visible(), expected)

    def test_real_navigation_markers(self):
        for marker, frame in [('I_EVENT_MAIN', 'event_main'), ('I_EVENT_TICKET_ENTRY', 'event_main'),
                              ('I_EVENT_VERSION', 'event_list'), ('I_EVENT_LIST_ART', 'event_list'),
                              ('I_EVENT_MAP', 'event_map'), ('I_EVENT_MAP_BATTLE', 'event_map'),
                              ('I_EVENT_MAP_BACK', 'event_map')]:
            self.assertTrue(getattr(self.task, marker).match(self.image(frame)))

    def test_real_lock_and_unlock_do_not_overlap(self):
        for name, locked in [('ticket_before', True), ('ticket_unlock', False)]:
            frame = self.image(name)
            self.assertEqual(self.task.I_TICKET_LOCK.match(frame), locked)
            self.assertEqual(self.task.I_TICKET_UNLOCK.match(frame), not locked)

    def test_parse_explicit_zero_and_cost(self):
        parse = self.ns['parse_ticket_budget']
        for count in ['0', '4', '5', '230', '235']:
            for cost in ['x5', '×5', 'X 5']:
                self.assertEqual(parse(count, cost), (int(count), 5))

    def test_unknown_and_other_resource_cost_are_not_zero(self):
        parse = self.ns['parse_ticket_budget']
        for count, cost in [('', 'x5'), (None, 'x5'), ('23?', 'x5'), ('235/300', 'x5'),
                            ('235', ''), ('235', '5'), ('235', 'x1'), ('235', '6+1')]:
            self.assertIsNone(parse(count, cost))

    def budget_frames(self, counts, visible=None):
        frames = iter(counts)
        last = counts[-1]
        self.task.ticket_page_visible = Mock(side_effect=visible) if visible else lambda: True
        def read(_):
            return next(frames, last)
        self.task.O_TICKET_BALANCE = SimpleNamespace(ocr_single=read)
        self.task.O_TICKET_COST = SimpleNamespace(ocr_single=lambda _: 'x5')

    def test_read_retries_unknown_then_confirms_two_equal_values(self):
        self.budget_frames(['', '235', '230', '230'])
        self.assertEqual(self.task.ticket_read_budget(), (230, 5))

    def test_persistent_blank_raises_without_any_click(self):
        self.budget_frames([''])
        with self.assertRaises(GameStuckError):
            self.task.ticket_read_budget()
        self.task.click.assert_not_called()

    def test_wrong_page_never_reads_or_clicks(self):
        self.task.ticket_page_visible = lambda: False
        self.task.O_TICKET_BALANCE = SimpleNamespace(ocr_single=Mock())
        with self.assertRaises(GameStuckError):
            self.task.ticket_read_budget()
        self.task.O_TICKET_BALANCE.ocr_single.assert_not_called()
        self.task.click.assert_not_called()

    def prepare_battle(self, before=235, after=230, win=True):
        self.task.ticket_read_budget = Mock(side_effect=[(before, 5), (after, 5)])
        self.task.I_REWARD = 'reward'
        self.task.I_WIN = 'win'
        self.task.is_in_battle = lambda _: self.clock.now >= 103
        self.task.appear = lambda _: False
        self.task.loadout_from_config = lambda _: ({}, {})
        self.task.get_general_battle_conf = lambda: None
        self.task.battle_wait = Mock(return_value=win)
        self.task.ticket_wait_return = Mock()

    def test_insufficient_tickets_never_start(self):
        for before in (0, 4):
            self.prepare_battle(before=before)
            self.assertFalse(self.task.ticket_battle_once())
        self.task.click.assert_not_called()

    def test_loading_only_clicks_once_and_counts_after_return(self):
        self.prepare_battle()
        self.assertTrue(self.task.ticket_battle_once())
        self.task.click.assert_called_once_with(self.task.I_TICKET_CHALLENGE)
        self.task.ticket_wait_return.assert_called_once()
        self.assertEqual(self.task.count_map['pass'], 1)

    def test_exactly_five_tickets_can_complete_last_battle(self):
        self.prepare_battle(before=5, after=0)
        self.assertTrue(self.task.ticket_battle_once())
        self.assertEqual(self.task.count_map['pass'], 1)

    def test_entry_timeout_does_not_repeat_click_or_count(self):
        self.prepare_battle()
        self.task.is_in_battle = lambda _: False
        with self.assertRaises(GameStuckError):
            self.task.ticket_battle_once()
        self.task.click.assert_called_once()
        self.task.battle_wait.assert_not_called()
        self.assertEqual(self.task.count_map['pass'], 0)

    def test_loss_stops_without_retry_or_count(self):
        self.prepare_battle(win=False)
        with self.assertRaises(RequestHumanTakeover):
            self.task.ticket_battle_once()
        self.assertEqual(self.task.count_map['pass'], 0)
        self.task.ticket_wait_return.assert_not_called()

    def test_unconfirmed_return_does_not_count(self):
        self.prepare_battle()
        self.task.ticket_wait_return.side_effect = GameStuckError()
        with self.assertRaises(GameStuckError):
            self.task.ticket_battle_once()
        self.assertEqual(self.task.count_map['pass'], 0)

    def test_unexpected_ticket_change_does_not_count(self):
        self.prepare_battle(after=235)
        with self.assertRaises(GameStuckError):
            self.task.ticket_battle_once()
        self.assertEqual(self.task.count_map['pass'], 0)

    def test_return_timeout_is_bounded(self):
        self.task.ticket_page_visible = lambda: False
        with self.assertRaises(GameStuckError):
            self.task.ticket_wait_return()
        self.task.click.assert_not_called()

    def test_already_locked_does_not_toggle(self):
        self.task.ticket_page_visible = lambda: True
        self.task.appear = lambda rule: rule == self.task.I_TICKET_LOCK
        self.task.appear_then_click = Mock()
        self.task.ticket_lock_team(False)
        self.task.appear_then_click.assert_not_called()

    def test_legacy_sequence_cannot_run_stamina_or_boss(self):
        class TaskEnd(Exception):
            pass
        ns = dict(logger=FakeLogger(), RequestHumanTakeover=RequestHumanTakeover,
                  LimitCountOut=type('LimitCountOut', (Exception,), {}),
                  LimitTimeOut=type('LimitTimeOut', (Exception,), {}), TaskEnd=TaskEnd,
                  game=SimpleNamespace(page_main='courtyard'))
        methods = extract_methods(SCRIPT, 'ScriptTask', ['run'], ns)
        conf = SimpleNamespace(general_climb=SimpleNamespace(
            limit_time_v=None, run_sequence_v=['ap100', 'pass', 'boss', 'ap'], active_souls_clean=False))
        task = SimpleNamespace(conf=conf, put_status=Mock(), _run_pass=Mock(),
                               _run_ap=Mock(), _run_boss=Mock(), _run_ap100=Mock(),
                               goto_page=Mock(), set_next_run=Mock())
        with self.assertRaises(TaskEnd):
            methods['run'](task)
        task._run_pass.assert_called_once()
        task._run_ap.assert_not_called()
        task._run_ap100.assert_not_called()
        task._run_boss.assert_not_called()
        self.assertEqual(task.run_idx, 1)


if __name__ == '__main__':
    unittest.main()
