"""Offline one-shot submission regressions using actual task and click methods.

No backend, OCR model, account file or physical device is loaded.
Run: toolkit/python.exe -B dev_tools/test_collective_missions_rewards.py
"""
import ast
from collections import deque
from pathlib import Path
import re
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/CollectiveMissions/script_task.py'


class Clock:
    now = 0


class Timer:
    def __init__(self, seconds):
        self.limit = seconds
        self.started = 0

    def start(self):
        self.started = Clock.now
        return self

    def reached(self):
        return Clock.now - self.started >= self.limit

    def reset(self):
        return self.start()


class GameTooManyClickError(Exception):
    pass


class OwnerTakeover(Exception):
    pass


class RuleClick:
    def __init__(self, *args, name='', roi_front=(0, 0, 100, 100), roi_back=None, **kwargs):
        if args and isinstance(args[0], str):
            name = args[0]
        elif args:
            roi_front = args[0]
            roi_back = args[1] if len(args) > 1 else roi_front
        self.name = name
        self.roi_front = roi_front
        self.roi_back = roi_back or roi_front

    def coord(self):
        return 0, 0


class RuleLongClick(RuleClick):
    duration = 1500


class RuleImage(RuleClick):
    pass


class RuleOcr(RuleClick):
    pass


def load_methods(path, owner, names, env):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), env)
    return {name: env[name] for name in names}


ENV = dict(logger=Mock(), Timer=Timer, re=re, RuleClick=RuleClick,
           random=S(sample=lambda sequence, count: sequence[:count], choice=lambda sequence: sequence[0]),
           time=S(sleep=lambda seconds: setattr(Clock, 'now', Clock.now + seconds)),
           RequestHumanTakeover=AssertionError)
ACTUAL = load_methods(SOURCE, 'ScriptTask',
                      ('_open_collective_submission', '_submit_collective_once_and_claim',
                       '_collective_click_available', '_feed_selected_count', '_feed', '_donate'), ENV)
EXIT = load_methods(SOURCE, 'ScriptTask', ('_return_to_missions', '_leave_missions'), ENV)
CLICK = load_methods(ROOT / 'tasks/base_task.py', 'BaseTask', ('click',),
                     dict(Timer=Timer, Union=__import__('typing').Union,
                          RuleClick=RuleClick, RuleLongClick=RuleLongClick,
                          RuleImage=RuleImage, RuleOcr=RuleOcr))['click']
GUARD = load_methods(ROOT / 'module/device/device.py', 'Device', ('click_record_check',),
                     dict(logger=Mock(), GameTooManyClickError=GameTooManyClickError))['click_record_check']


class Runtime:
    _open_collective_submission = ACTUAL['_open_collective_submission']
    _submit_collective_once_and_claim = ACTUAL['_submit_collective_once_and_claim']
    _collective_click_available = ACTUAL['_collective_click_available']
    _feed = ACTUAL['_feed']
    _feed_selected_count = ACTUAL['_feed_selected_count']
    _donate = ACTUAL['_donate']
    _return_to_missions = EXIT['_return_to_missions']
    _leave_missions = EXIT['_leave_missions']
    click = CLICK

    def __init__(self, kind='feed', rewards=(0.5,), overview=True,
                 persistent_submit=False, stuck_reward=False, overview_during_reward=False,
                 overview_after=0, submit_visible_until=0,
                 expanded=False, expand_confirmed=True, selected_count=0,
                 selected_steps=None, n_page=True):
        self.kind = kind
        self.phase = 'ready'
        self.reward_times = list(rewards)
        self.claimed = set()
        self.overview = overview
        self.overview_during_reward = overview_during_reward
        self.persistent_submit = persistent_submit
        self.overview_after = overview_after
        self.submit_visible_until = submit_visible_until
        self.stuck_reward = stuck_reward
        self.submit_at = None
        self.submits = []
        self.reward_clicks = []
        self.card_clicks = 0
        self.selection_clicks = 0
        self.selection_long_clicks = 0
        self.expanded = expanded
        self.expand_confirmed = expand_confirmed
        self.selected_count = selected_count
        self.initial_selection = selected_count > 0
        self.selected_steps = list(selected_steps) if selected_steps is not None else None
        self.n_page = n_page
        self.expand_clicks = 0
        self.cancel_clicks = 0
        self.feed_back_clicks = 0
        self.ready_after = 2
        self.material_ready = True
        self.config = S(script=S(device=S(control_method='adb')))
        self.interval_timer = {}
        self.device = S(image=object(), click_record=deque(maxlen=15), click_record_clear=Mock())
        self.device.click = lambda **kwargs: self.native_click(kwargs['control_name'])
        self.device.long_click = lambda **kwargs: self.native_click(kwargs['control_name'], long=True)
        for name in ('CM_PRESENT', 'CM_MATTER', 'CM_RECORDS', 'FEED_HEAP', 'FEED_SUBMIT',
                     'FEED_N_PAGE', 'FEED_EXPANDED', 'UI_REWARD', 'CM_SHRINE',
                     'CHECK_MAIN', 'UI_BACK_RED', 'UI_BACK_YELLOW'):
            setattr(self, 'I_' + name, RuleImage(name))
        self.C_FEED_EXPAND = RuleClick('feed_expand')
        self.C_FEED_CANCEL = RuleClick('feed_cancel')
        self.C_FEED_BACK = RuleClick('feed_back')
        self.O_FEED_SUBMIT_COUNT = S(ocr=Mock(side_effect=lambda _:
            f'将提交{self.selected_count}次任务' if self.selected_count > 0 else ''))
        for i in range(1, 4):
            setattr(self, 'C_CM_' + str(i), RuleClick('cm_' + str(i)))
        for i in range(1, 5):
            setattr(self, 'L_FEED_CLICK_' + str(i), RuleLongClick('feed_click_' + str(i)))
            setattr(self, 'S_CM_MATTER_' + str(i), RuleClick('matter_' + str(i)))
            setattr(self, 'I_CM_ADD_' + str(i), RuleImage('add_' + str(i)))
            setattr(self, 'O_CM_' + str(i) + '_MATTER', S(ocr=Mock(return_value=(0, 100, 100))))

    @property
    def button(self):
        return self.I_FEED_SUBMIT if self.kind == 'feed' else self.I_CM_PRESENT

    def current_reward(self):
        if self.submit_at is None:
            return None
        elapsed = Clock.now - self.submit_at
        return next((i for i, at in enumerate(self.reward_times)
                     if elapsed >= at and (i not in self.claimed or self.stuck_reward)), None)

    def screenshot(self):
        Clock.now += 0.25

    def appear(self, rule, **kwargs):
        if rule is self.I_UI_REWARD:
            return self.current_reward() is not None
        if rule is self.I_CM_RECORDS:
            return self.phase == 'collective_overview' or (self.phase == 'after' and self.overview and (
                Clock.now - self.submit_at >= self.overview_after) and (
                self.overview_during_reward or self.current_reward() is None))
        if rule is self.I_CM_SHRINE:
            return self.phase == 'guild'
        if rule is self.I_CHECK_MAIN:
            return self.phase == 'main'
        if rule is self.I_FEED_HEAP:
            return self.phase == 'feed_select'
        if rule is self.I_FEED_N_PAGE:
            return self.phase == 'feed_select' and self.n_page
        if rule is self.I_FEED_EXPANDED:
            return self.phase == 'feed_select' and self.expanded and self.expand_confirmed
        if rule is self.I_CM_MATTER:
            return self.phase == 'donate_fill' and self.material_ready
        if rule is self.button:
            return self.phase == 'ready' or (
                self.phase == 'feed_select' and (self.initial_selection or
                                                self.selection_clicks >= self.ready_after)) or (
                self.phase == 'donate_fill') or (self.phase == 'after' and (
                    self.persistent_submit or Clock.now - self.submit_at < self.submit_visible_until))
        return False

    def record(self, name):
        self.device.click_record.append(name)
        GUARD(self.device)

    def native_click(self, name, long=False):
        self.record(name)
        if name.startswith('cm_'):
            self.card_clicks += 1
            self.phase = 'feed_select' if self.kind == 'feed' else 'donate_fill'
        if name == self.C_FEED_EXPAND.name:
            self.expand_clicks += 1
            self.expanded = True
        if name == self.C_FEED_CANCEL.name:
            self.cancel_clicks += 1
            self.selected_count = 0
            self.initial_selection = False
        if name == self.C_FEED_BACK.name:
            self.feed_back_clicks += 1
            self.phase = 'collective_overview'
        if name.startswith(('feed_click_', 'feed_select_', 'feed_single_')):
            self.selection_clicks += 1
            self.selection_long_clicks += int(long)
            step = (self.selected_steps.pop(0) if self.selected_steps else
                    (1 if self.expanded else 2))
            self.selected_count += step
        if long:
            Clock.now += 1.5

    def appear_then_click(self, rule, **kwargs):
        if rule is self.I_UI_BACK_YELLOW and self.phase == 'collective_overview':
            self.record(rule.name)
            self.phase = 'guild'
            return True
        if not self.appear(rule):
            return False
        if rule is self.button:
            self.record(rule.name)
            self.submits.append(Clock.now)
            self.submit_at = Clock.now
            self.phase = 'after'
            return True
        return False

    def ui_reward_appear_click(self, screenshot=False):
        reward = self.current_reward()
        if reward is None:
            return False
        self.record(self.I_UI_REWARD.name)
        self.reward_clicks.append((reward, Clock.now))
        self.claimed.add(reward)
        return True

    def swipe(self, rule, **kwargs):
        self.material_ready = True
        return True


class RewardTests(unittest.TestCase):
    def setUp(self):
        Clock.now = 0

    def claim(self, task):
        result = task._submit_collective_once_and_claim(task.button)
        self.assertLessEqual(Clock.now - task.submit_at, 15.25)
        self.assertEqual(len(task.submits), 1)
        task.device.click_record_clear.assert_not_called()
        return result

    def test_real_six_single_reward_then_overview_finishes_after_quiet(self):
        task = Runtime()
        self.assertTrue(self.claim(task))
        self.assertEqual(len(task.reward_clicks), 1)
        self.assertGreaterEqual(Clock.now - task.reward_clicks[0][1], 3)
        self.assertLess(Clock.now - task.submit_at, 5)

    def test_two_rewards_are_both_claimed_without_second_submission(self):
        task = Runtime(rewards=(0.5, 1.0))
        self.assertTrue(self.claim(task))
        self.assertEqual([i for i, _ in task.reward_clicks], [0, 1])

    def test_late_second_reward_after_overview_is_not_missed(self):
        task = Runtime(rewards=(0.5, 3.0), overview_during_reward=True)
        self.assertTrue(self.claim(task))
        self.assertEqual([i for i, _ in task.reward_clicks], [0, 1])
        self.assertGreaterEqual(Clock.now - task.reward_clicks[-1][1], 3)

    def test_animation_time_before_overview_does_not_consume_its_quiet_period(self):
        task = Runtime(rewards=(0.5, 6.0), overview_after=5, submit_visible_until=5)
        self.assertTrue(self.claim(task))
        self.assertEqual([i for i, _ in task.reward_clicks], [0, 1])
        self.assertGreaterEqual(Clock.now - task.reward_clicks[-1][1], 3)

    def test_reward_precedes_overlapping_overview_marker(self):
        task = Runtime(rewards=(0.5, 1.0), overview_during_reward=True)
        self.assertTrue(self.claim(task))
        self.assertEqual(len(task.reward_clicks), 2)

    def test_single_reward_and_disappeared_submit_is_bounded_without_overview(self):
        task = Runtime(overview=False)
        self.assertTrue(self.claim(task))
        self.assertEqual(len(task.reward_clicks), 1)

    def test_no_reward_but_confirmed_overview_can_settle_without_claiming_completion(self):
        task = Runtime(rewards=())
        self.assertTrue(self.claim(task))
        self.assertEqual(task.reward_clicks, [])

    def test_unknown_after_submit_does_not_fake_settled_state(self):
        task = Runtime(rewards=(), overview=False)
        self.assertFalse(self.claim(task))

    def test_persistent_submit_times_out_without_repeated_spending(self):
        task = Runtime(rewards=(), persistent_submit=True)
        self.assertFalse(self.claim(task))
        self.assertEqual(task.reward_clicks, [])

    def test_persistent_reward_has_click_budget_and_retains_device_protection(self):
        task = Runtime(stuck_reward=True)
        self.assertFalse(self.claim(task))
        self.assertEqual(len(task.reward_clicks), 6)

    def test_old_reward_history_and_six_refreshes_do_not_cross_two_button_guard(self):
        task = Runtime()
        history = ['UI_REWARD'] * 5 + ['CM_CM_SWITCH'] * 6
        task.device.click_record.extend(history)
        self.assertFalse(self.claim(task))
        self.assertEqual(task.reward_clicks, [])
        self.assertEqual(list(task.device.click_record), history + [task.button.name])

    def test_exhausted_submit_history_does_not_spend_again(self):
        task = Runtime()
        task.device.click_record.extend([task.button.name] * 8)
        self.assertFalse(task._submit_collective_once_and_claim(task.button))
        self.assertEqual(task.submits, [])
        task.device.click_record_clear.assert_not_called()

    def test_missing_panel_never_submits(self):
        task = Runtime()
        task.phase = 'overview'
        self.assertFalse(task._submit_collective_once_and_claim(task.button))
        self.assertEqual(task.submits, [])

    def test_unexpected_existing_reward_never_submits(self):
        task = Runtime(rewards=(0,))
        task.submit_at = 0
        self.assertFalse(task._submit_collective_once_and_claim(task.button))
        self.assertEqual(task.submits, [])

    def test_submit_interval_not_ready_does_not_fall_into_resubmit_loop(self):
        task = Runtime()
        task.appear_then_click = Mock(return_value=False)
        self.assertFalse(task._submit_collective_once_and_claim(task.button))
        task.appear_then_click.assert_called_once_with(task.button, interval=1)

    def test_owner_takeover_during_reward_wait_propagates(self):
        task = Runtime()
        original = task.screenshot

        def screenshot():
            if task.submit_at is not None:
                raise OwnerTakeover('owner logged in')
            original()

        task.screenshot = screenshot
        with self.assertRaises(OwnerTakeover):
            task._submit_collective_once_and_claim(task.button)
        self.assertEqual(len(task.submits), 1)

    def test_full_feed_one_reward_uses_actual_base_click_and_submits_once(self):
        task = Runtime()
        task.phase = 'overview'
        self.assertTrue(task._feed(0))
        self.assertEqual(task.card_clicks, 1)
        self.assertEqual(task.selection_clicks, 2)
        self.assertEqual(len(task.submits), 1)
        self.assertEqual(len(task.reward_clicks), 1)
        self.assertLess(Clock.now, 15)

    def test_full_donation_two_rewards_submits_once(self):
        task = Runtime(kind='donate', rewards=(0.5, 1.0))
        task.phase = 'overview'
        self.assertTrue(task._donate(0))
        self.assertEqual(len(task.submits), 1)
        self.assertEqual(len(task.reward_clicks), 2)

    def test_full_donation_single_reward_no_long_wait(self):
        task = Runtime(kind='donate')
        task.phase = 'overview'
        self.assertTrue(task._donate(0))
        self.assertEqual(len(task.submits), 1)
        self.assertEqual(len(task.reward_clicks), 1)
        self.assertLess(Clock.now, 10)

    def test_material_ocr_failure_and_insufficient_materials_do_not_spend(self):
        for counter in ((0, 20, 20), None, (0, 100), (0, True, 100)):
            task = Runtime(kind='donate')
            task.phase = 'overview'
            for i in range(1, 5):
                getattr(task, 'O_CM_' + str(i) + '_MATTER').ocr.return_value = counter
            self.assertFalse(task._donate(0))
            self.assertEqual(task.submits, [])
            Clock.now = 0

    def test_unconfirmed_n_selection_and_material_fill_are_bounded_without_submit(self):
        task = Runtime()
        task.phase = 'overview'
        task.ready_after = 100
        self.assertFalse(task._feed(0))
        self.assertEqual(task.submits, [])
        self.assertLessEqual(task.selection_clicks, 8)
        self.assertLess(Clock.now, 25)
        Clock.now = 0
        task = Runtime(kind='donate')
        task.phase = 'overview'
        task.material_ready = False
        task.swipe = Mock(return_value=False)
        self.assertFalse(task._donate(0))
        self.assertEqual(task.submits, [])
        self.assertLess(Clock.now, 25)


if __name__ == '__main__':
    unittest.main()
