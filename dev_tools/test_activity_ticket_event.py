"""Offline ticket-event guards and real UI crops; no device or service imports."""

import ast
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
import re
import random
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
    class FakeRuleOcr(SimpleNamespace):
        pass

    logger = ModuleType('module.logger')
    logger.logger = FakeLogger()
    with patch.dict(sys.modules, {'module.logger': logger}):
        from module.atom.image import RuleImage
        from module.atom.click import RuleClick, RuleClickExclude
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    context = dict(re=re, random=random, RuleClick=RuleClick, RuleClickExclude=RuleClickExclude, RuleImage=RuleImage, RuleOcr=FakeRuleOcr,
                   Timer=lambda *a, **k: FakeTimer(clock, *a, **k), logger=FakeLogger(),
                   GameStuckError=GameStuckError, RequestHumanTakeover=RequestHumanTakeover,
                   battle_wait_strategy=lambda **kwargs: nullcontext(),
                   battle_wait_options=lambda **kwargs: nullcontext())
    exec(compile(tree, str(SOURCE), 'exec'), context)
    return context


def load_settlement(clock, RuleClickExclude):
    """Exercise the real shared hooks without importing a device or OCR."""
    class Timer(FakeTimer):
        def __init__(self, *args, **kwargs):
            super().__init__(clock, *args, **kwargs)
        def reached_and_reset(self):
            if self.reached():
                self.reset()
                return True
            return False
    path = ROOT / 'tasks/Component/GeneralBattle/battle_wait.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'OptionSuccessDefault', 'OptionCompletionDefault', 'PerBattleSuccess',
             'PerBattleCompletion', 'HookSignal', 'BattleResult'}
    tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names]
    ns = dict(dataclass=dataclass, field=field, Enum=Enum, auto=auto, Timer=Timer,
              RuleClickExclude=RuleClickExclude, RuleImage=object,
              GameStuckError=GameStuckError, logger=FakeLogger(),
              PublicContext=object, PrivateContext=object,
              runtime=SimpleNamespace(hook_enabled_update=Mock(return_value='completion')))
    exec(compile(tree, str(path), 'exec'), ns)
    ns.update(extract_methods(path, 'BattleWait', ('_bw_success_soul', '_bw_completion_default'), ns))
    return ns


class TicketEventTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(step=0.5)
        self.ns = load_event(self.clock)
        self.task = self.ns['CurrentEventTicket']()
        self.task.device = SimpleNamespace(image=None)
        self.task.screenshot = self.clock.advance
        self.task.click = Mock()
        self.task.appear = lambda _: False
        self.task.put_status = Mock()
        self.task.count_map = {'pass': 0}
        self.task.conf = SimpleNamespace(general_climb=SimpleNamespace(pass_difficulty='ticket_keep_current'))

    def image(self, name):
        return cv2.cvtColor(cv2.imread(str(FIXTURES / (name + '.png'))), cv2.COLOR_BGR2RGB)

    def test_real_ticket_and_stamina_pages_are_distinct(self):
        for name, expected in [('simple', True), ('hard_current', True), ('ticket_before', True), ('ticket_after', True),
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
        for count in [0, '0', '4', '5', '230', '235']:
            for cost, expected in [('x5', 5), ('×5', 5), ('X 5', 5), ('x1', 1), ('×1', 1)]:
                self.assertEqual(parse(count, cost), (int(count), expected))

    def test_unknown_and_other_resource_cost_are_not_zero(self):
        parse = self.ns['parse_ticket_budget']
        for count, cost in [('', 'x5'), (None, 'x5'), ('23?', 'x5'), (':255', 'x5'), ('：385', 'x1'),
                            ('_385', 'x1'), ('235/300', 'x5'),
                            ('235', ''), ('235', '5'), ('235', 'x2'), ('235', '6+1')]:
            self.assertIsNone(parse(count, cost))

    def budget_frames(self, counts, visible=None):
        frames = iter(counts)
        last = counts[-1]
        self.task.ticket_page_visible = Mock(side_effect=visible) if visible else lambda: True
        def read(_):
            return self.ns['TicketBalanceOcr']().after_process(next(frames, last))
        self.task.O_TICKET_BALANCE = SimpleNamespace(ocr_single=read)
        self.task.O_TICKET_COST = SimpleNamespace(ocr_single=lambda _: 'x5')

    def test_read_retries_unknown_then_confirms_two_equal_values(self):
        self.budget_frames(['', '235', '230', '230'])
        self.assertEqual(self.task.ticket_read_budget(), (230, 5))

    def test_digit_filters_punctuation_before_confirming_two_equal_values(self):
        for counts, expected in [([':255', '255'], 255), (['：385', '_385'], 385)]:
            self.budget_frames(counts)
            self.assertEqual(self.task.ticket_read_budget(), (expected, 5))
        self.task.click.assert_not_called()

    def test_no_digits_retries_then_explicit_zero_is_confirmed(self):
        self.budget_frames(['：_', '未识别', None, ':0', '0'])
        self.assertEqual(self.task.ticket_read_budget(), (0, 5))
        self.task.click.assert_not_called()

    def test_non_digit_only_result_stays_unknown_without_any_click(self):
        self.budget_frames(['：_未识别'])
        with self.assertRaises(GameStuckError):
            self.task.ticket_read_budget()
        self.task.click.assert_not_called()

    def test_digit_zero_is_not_replaced_with_one(self):
        self.budget_frames(['00', '0'])
        self.assertEqual(self.task.ticket_read_budget(), (0, 5))

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

    def prepare_battle(self, before=235, after=230, win=True, cost=5):
        self.task.ticket_read_budget = Mock(side_effect=[(before, cost), (after, cost)])
        self.task.I_REWARD = 'reward'
        self.task.I_WIN = 'win'
        self.task.is_in_battle = lambda _: self.clock.now >= 103
        self.task.appear = lambda _: False
        self.options = {'success': SimpleNamespace(), 'completion': SimpleNamespace()}
        self.task.loadout_from_config = lambda _: ({}, self.options)
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

    def test_simple_with_one_or_four_tickets_still_runs(self):
        for balance in (1, 4):
            self.prepare_battle(before=balance, after=balance - 1, cost=1)
            self.assertTrue(self.task.ticket_battle_once())
        self.assertEqual(self.task.count_map['pass'], 2)

    def test_simple_with_zero_tickets_stops(self):
        self.prepare_battle(before=0, cost=1)
        self.assertFalse(self.task.ticket_battle_once())
        self.task.click.assert_not_called()

    def test_difficulty_drift_cannot_start_wrong_battle(self):
        self.prepare_battle()
        self.task._ticket_expected_cost = 1
        with self.assertRaises(GameStuckError):
            self.task.ticket_battle_once()
        self.task.click.assert_not_called()

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

    def test_reward_continue_then_return_without_challenging_again(self):
        self.task.ticket_page_visible = Mock(side_effect=[False, True])
        self.task.appear = lambda rule: rule is self.task.I_TICKET_REWARD_CONTINUE
        self.task.ticket_wait_return()
        self.task.click.assert_called_once()
        self.assertIn(self.task.click.call_args.args[0], self.task.C_TICKET_REWARD_BLANKS)

    def test_reward_regions_stay_outside_loot_grid_and_banner(self):
        for rule in self.task.C_TICKET_REWARD_BLANKS:
            x, y, w, h = rule.roi_front
            self.assertTrue(x+w < 260 or x > 1050 or y > 625)
            self.assertGreater(y, 100)
            self.assertTrue(0 <= x < x+w <= 1280 and 0 <= y < y+h <= 720)
            for _ in range(50):
                px, py = rule.coord()
                self.assertTrue(x <= px <= x+w and y <= py <= y+h)

    def test_rotating_regions_share_click_delay_and_require_visible_prompt(self):
        self.task.ticket_page_visible = lambda: False
        self.task.appear = lambda rule: rule is self.task.I_TICKET_REWARD_CONTINUE
        clicks = []
        self.task.click = lambda rule: clicks.append((self.clock.now, rule))
        regions = self.task.C_TICKET_REWARD_BLANKS
        with patch.object(self.ns['random'], 'choice', side_effect=lambda options: regions[len(clicks) % 3]):
            with self.assertRaises(GameStuckError):
                self.task.ticket_wait_return()
        self.assertEqual([rule for _, rule in clicks[:3]], list(regions))
        self.assertTrue(all(b[0]-a[0] >= 1.5 for a,b in zip(clicks,clicks[1:])))

    def test_real_reward_footer_matches_but_other_pages_do_not(self):
        import numpy as np
        image = np.zeros((720,1280,3), dtype=np.uint8)
        image[660:720,500:780] = self.image('reward_footer')
        self.assertTrue(self.task.I_TICKET_REWARD_CONTINUE.match(image))
        for name in ('simple','hard_current','event_main','stamina','ticket_after'):
            self.assertFalse(self.task.I_TICKET_REWARD_CONTINUE.match(self.image(name)))

    def configured_settlement(self):
        self.prepare_battle()
        shared = load_settlement(self.clock, self.ns['RuleClickExclude'])
        self.options.update(success=shared['OptionSuccessDefault'](),
                            completion=shared['OptionCompletionDefault']())
        strategies = {}
        self.task.loadout_from_config = lambda _: (strategies, self.options)
        self.task.ticket_battle_once()
        self.assertEqual(strategies['success'], 'soul')
        self.assertEqual(self.options['completion'].check_imgs, [self.task.I_TICKET_PAGE])
        return shared

    def test_all_shared_reward_clicks_exclude_the_entire_actual_grid(self):
        shared = self.configured_settlement()
        factory = shared['PerBattleSuccess'].reward_exclude_click
        for exclusions in (self.options['success'].excludes_1,
                           self.options['success'].excludes_2,
                           self.options['completion'].excludes):
            rule = factory(self.task, exclusions)
            self.assertTrue(rule._is_excluded(320,449))  # Actual bad click on 12th loot slot.
            self.assertTrue(rule._is_excluded(783,502))  # First-stage stale-frame click.
            for strategy in ('rejection', 'complement'):
                for distribution in ('uniform', 'normal'):
                    rule.strategy, rule.distribution = strategy, distribution
                    for _ in range(250):
                        x,y = rule.coord()
                        self.assertFalse(220 <= x < 1090 and 70 <= y < 645)
                        self.assertGreaterEqual(y,180)

    def test_success_clicks_victory_once_without_an_immediate_reward_click(self):
        shared = self.configured_settlement()
        self.task.click.reset_mock()
        self.task.I_WIN = 'win'
        self.task.appear_then_click = Mock(return_value=True)
        pub = SimpleNamespace(per_battle=SimpleNamespace(success=None))
        pri = SimpleNamespace(per_battle=shared['PerBattleSuccess'](), options=self.options['success'])
        shared['_bw_success_soul'](self.task,pub,pri)
        self.task.appear_then_click.assert_called_once_with('win', interval=0.8)
        self.task.click.assert_not_called()

    def test_completion_closes_details_at_side_then_waits_for_challenge_page(self):
        shared = self.configured_settlement()
        pub = SimpleNamespace(per_task=SimpleNamespace(count=0))
        pri = SimpleNamespace(per_battle=shared['PerBattleCompletion'](), options=self.options['completion'])
        points=[]
        self.task.device.click = lambda **kw: points.append((kw['x'],kw['y']))
        hook = shared['_bw_completion_default']
        self.task.appear = lambda _: False  # Tooltip covers the continue label.
        self.assertEqual(hook(self.task,pub,pri),shared['HookSignal'].CONTINUE)
        self.assertEqual(points,[])
        self.clock.now += 3.1
        hook(self.task,pub,pri)
        self.assertEqual(len(points),1)
        self.assertTrue(points[0][0] < 200 or points[0][0] >= 1100)
        self.assertEqual(pub.per_task.count,0)
        self.task.appear = lambda marker: marker is self.task.I_TICKET_PAGE
        self.assertEqual(hook(self.task,pub,pri),shared['HookSignal'].DONE)
        self.assertEqual(pub.per_task.count,1)
        self.assertEqual(len(points),1)

    def test_reward_stage_uses_safe_clicks_without_item_detail_branch(self):
        shared = self.configured_settlement()
        self.task.click.reset_mock()
        self.task.device.click = Mock()
        self.task.appear_then_click = lambda *a, **k: False
        for name in ('I_GREED_GHOST','I_REWARD_GOLD','I_REWARD_GOLD_SNAKE_SKIN'):
            setattr(self.task,name,name)
        visible={'reward'}
        self.task.appear=lambda marker, **kw: marker in visible
        captures=0
        def screenshot():
            nonlocal captures
            captures+=1
            self.clock.advance()
            if captures>1:
                visible.clear()
        self.task.screenshot=screenshot
        pub=SimpleNamespace(per_battle=SimpleNamespace(success=None))
        pri=SimpleNamespace(per_battle=shared['PerBattleSuccess'](),options=self.options['success'])
        self.assertEqual(shared['_bw_success_soul'](self.task,pub,pri),'completion')
        self.task.click.assert_called_once()
        self.task.device.click.assert_not_called()
        rule=self.task.click.call_args.args[0]
        self.assertTrue(rule._is_excluded(320,449))
        self.assertEqual(pub.per_battle.success,shared['BattleResult'].SUCCESS)

    def test_exhausted_completion_retries_do_not_count_success(self):
        shared = self.configured_settlement()
        pub = SimpleNamespace(per_task=SimpleNamespace(count=0))
        pri = SimpleNamespace(per_battle=shared['PerBattleCompletion'](), options=self.options['completion'])
        self.task.device.click=Mock()
        self.task.appear=lambda _: False
        hook=shared['_bw_completion_default']
        hook(self.task,pub,pri)
        for _ in range(5):
            self.clock.now += 3.1
            hook(self.task,pub,pri)
        self.clock.now += 3.1
        with self.assertRaises(GameStuckError):
            hook(self.task,pub,pri)
        self.assertEqual(self.task.device.click.call_count,5)
        self.assertEqual(pub.per_task.count,0)

    def test_other_tasks_keep_existing_completion_defaults(self):
        shared = load_settlement(self.clock, self.ns['RuleClickExclude'])
        options=shared['OptionCompletionDefault']()
        self.assertIsNone(options.check_imgs)
        self.assertEqual(options.fallback_interval,8)
        self.assertIsNone(options.fallback_limit)

    def test_already_locked_does_not_toggle(self):
        self.task.ticket_page_visible = lambda: True
        self.task.appear = lambda rule: rule == self.task.I_TICKET_LOCK
        self.task.appear_then_click = Mock()
        self.task.ticket_lock_team(False)
        self.task.appear_then_click.assert_not_called()

    def test_real_difficulty_buttons(self):
        self.assertTrue(self.task.I_TICKET_SIMPLE.match(self.image('hard_current')))
        self.assertTrue(self.task.I_TICKET_HARD.match(self.image('simple')))

    def test_keep_current_accepts_both_difficulties_without_clicking(self):
        for cost in (1, 5):
            self.task.ticket_read_budget = Mock(return_value=(230, cost))
            self.task.ticket_select_difficulty()
            self.assertEqual(self.task._ticket_expected_cost, cost)
        self.task.click.assert_not_called()

    def test_select_difficulty_clicks_once_and_checks_actual_cost(self):
        for choice, before, after, button in [
                ('ticket_simple', 5, 1, self.task.I_TICKET_SIMPLE),
                ('ticket_hard', 1, 5, self.task.I_TICKET_HARD)]:
            self.task.conf.general_climb.pass_difficulty = choice
            self.task.ticket_read_budget = Mock(side_effect=[(230, before), (230, after)])
            self.task.appear = lambda _: True
            self.task.click.reset_mock()
            self.task.ticket_select_difficulty()
            self.task.click.assert_called_once_with(button)
            self.assertEqual(self.task._ticket_expected_cost, after)
            self.task.ticket_read_budget.assert_called_with(expected_cost=after)

    def test_already_selected_difficulty_never_clicks(self):
        self.task.conf.general_climb.pass_difficulty = 'ticket_simple'
        self.task.ticket_read_budget = Mock(return_value=(230, 1))
        self.task.ticket_select_difficulty()
        self.task.click.assert_not_called()

    def test_missing_button_does_not_click(self):
        self.task.conf.general_climb.pass_difficulty = 'ticket_simple'
        self.task.ticket_read_budget = Mock(return_value=(230, 5))
        self.task.appear = lambda _: False
        with self.assertRaises(GameStuckError):
            self.task.ticket_select_difficulty()
        self.task.click.assert_not_called()

    def test_unconfirmed_switch_stops_after_one_click(self):
        self.task.conf.general_climb.pass_difficulty = 'ticket_simple'
        self.task.ticket_read_budget = Mock(side_effect=[(230, 5), GameStuckError()])
        self.task.appear = lambda _: True
        with self.assertRaises(GameStuckError):
            self.task.ticket_select_difficulty()
        self.task.click.assert_called_once()

    def test_stale_cost_cannot_confirm_switch(self):
        self.budget_frames(['230'])
        with self.assertRaises(GameStuckError):
            self.task.ticket_read_budget(expected_cost=1)

    def test_switch_waits_for_two_frames_with_new_cost(self):
        self.budget_frames(['230'])
        costs = iter(['x5', 'x1', 'x1'])
        self.task.O_TICKET_COST = SimpleNamespace(ocr_single=lambda _: next(costs, 'x1'))
        self.assertEqual(self.task.ticket_read_budget(expected_cost=1), (230, 1))

    def test_gui_schema_exposes_per_account_choice_and_legacy_default(self):
        logger = ModuleType('module.logger')
        logger.logger = FakeLogger()
        with patch.dict(sys.modules, {'module.logger': logger}):
            from tasks.ActivityShikigami.config import ActivityShikigami
        import inflection
        ns = dict(convert_to_underscore=inflection.underscore, inflection=inflection, re=re, logger=FakeLogger())
        method = extract_methods(ROOT / 'module/config/config_model.py', 'ConfigModel', ['script_task'], ns)['script_task']
        for choice in ('ticket_keep_current', 'ticket_simple', 'ticket_hard'):
            task = ActivityShikigami(general_climb={'pass_difficulty': choice})
            args = method(SimpleNamespace(activity_shikigami=task), 'ActivityShikigami')
            field = next(x for x in args['general_climb'] if x['name'] == 'pass_difficulty')
            self.assertEqual(field['type'], 'enum')
            self.assertEqual(field['enumEnum'], ['ticket_keep_current', 'ticket_simple', 'ticket_hard'])
            self.assertEqual(field['value'], choice)
        self.assertEqual(ActivityShikigami().general_climb.pass_difficulty, 'ticket_keep_current')

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
