"""Offline linkage regressions using actual task/setter/queue AST and RAM saves.

No backend, logger, OCR model, device, account JSON or game process is loaded.
Run: toolkit/python.exe -B tests/test_collective_missions_link.py
"""
import ast
import copy
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
import re
import sys
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.Component import collective_missions_link as LINK


SOURCE = ROOT / 'tasks/CollectiveMissions/script_task.py'
BASE = ROOT / 'tasks/base_task.py'
CONFIG = ROOT / 'module/config/config.py'


class Clock(datetime):
    current = datetime(2026, 10, 6, 19, 30)

    @classmethod
    def now(cls):
        return cls.current


class TaskEnd(Exception):
    pass


class AccountLoggedInElsewhere(Exception):
    pass


class FakeTimer:
    def __init__(self, seconds):
        self.checks = 0

    def start(self):
        return self

    def reached(self):
        self.checks += 1
        return self.checks > 3

    def reset(self):
        self.checks = 0


def methods(source, owner, names, environment):
    tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
    nodes = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(source), 'exec'), environment)
    return {name: environment[name] for name in names}


def underscore(name):
    return {'CollectiveMissions': 'collective_missions',
            'BondlingFairyland': 'bondling_fairyland'}.get(name, name)


def deep_get(obj, keys):
    for key in keys.split('.'):
        obj = getattr(obj, key, None)
        if obj is None:
            return None
    return obj


def deep_set(obj, keys, value):
    pieces = keys.split('.')
    for key in pieces[:-1]:
        obj = getattr(obj, key)
    setattr(obj, pieces[-1], value)


class FakeConfig:
    """The real Config.task_call commits proof and queue position in one RAM save."""
    def __init__(self, linked=True, restricted=True, enabled=True, model=None):
        scheduler = S(enable=enabled, next_run=LINK.LINK_IDLE_TARGET,
                      failure_interval=timedelta(minutes=3), success_interval=timedelta(days=1),
                      server_update=time(9), delay_date=1, float_time=time(0))
        self.model = model or S(
            collective_missions=S(scheduler=scheduler, missions_config=S(
                monday_to_thursday=restricted, run_after_dokan=linked,
                dokan_finished_date='', attempted_date='', completed_date='',
                pending_kind='', pending_until='',
                missions_rule='觉醒三', missions_select='觉醒三',
            )),
            dokan=S(attack_count_config=S(attack_date='2026-10-06',
                                          remain_attack_count=0, daily_attack_count=2)),
            bondling_fairyland=S(scheduler=S(enable=True, next_run=LINK.LINK_IDLE_TARGET)),
        )
        self.model.deep_get = deep_get
        self.model.deep_set = deep_set
        self.lock_config = Mock()
        self.logger = Mock()
        self.snapshots = []
        self.queue_calls = []
        environment = {
            'datetime': Clock, 'time': time, 'timedelta': timedelta,
            'logger': self.logger, 'convert_to_underscore': underscore,
            'nearest_future': min, 'dict_to_kv': lambda values, allow_none=False: str(values),
            'random': S(randint=lambda first, last: 0),
            'parse_tomorrow_server': Mock(side_effect=AssertionError('explicit target must bypass server override')),
        }
        actual = methods(CONFIG, 'Config', ('task_call', 'task_delay'), environment)
        self.task_delay = actual['task_delay'].__get__(self)

        def task_call(name, force_call=True):
            self.queue_calls.append((name, force_call))
            return actual['task_call'](self, name, force_call)

        self.task_call = task_call

    def __getattr__(self, name):
        return getattr(self.model, name)

    def reload(self):
        pass

    def save(self):
        self.snapshots.append(copy.deepcopy(self.model))

    @property
    def state(self):
        return self.collective_missions.missions_config

    def restarted(self):
        return FakeConfig(model=copy.deepcopy(self.snapshots[-1]))


class Runtime:
    def __init__(self, config, counters=((0, 30, 30), (30, 0, 30), (30, 0, 30))):
        self.config = config
        self.start_time = Clock.current
        self.logger = Mock()
        self.device = S(image=None)
        self.screenshot = Mock()
        readings = iter(counters)
        self.O_CM_NUMBER = S(ocr=Mock(side_effect=lambda _: next(readings)))
        self.goto_page = Mock()
        self.ui_click = Mock()
        self.appear = Mock(return_value=False)
        self.appear_then_click = Mock(return_value=False)
        self.ui_reward_appear_click = Mock(return_value=False)
        for marker in ('CM_SHRINE', 'CM_CM', 'CM_RECORDS', 'CM_REWARDS',
                       'CHECK_MAIN', 'UI_BACK_RED', 'UI_BACK_YELLOW'):
            setattr(self, 'I_' + marker, marker)
        environment = {
            'datetime': Clock, 'timedelta': timedelta, 'logger': self.logger,
            'TaskEnd': TaskEnd, 'RequestHumanTakeover': RuntimeError,
            'Timer': FakeTimer, 'page_guild': 'guild',
            'collective_gate': lambda cfg: LINK.collective_gate(cfg, Clock.current),
            'begin_collective_attempt': lambda cfg: LINK.begin_collective_attempt(cfg, Clock.current),
            'complete_collective': lambda cfg: LINK.complete_collective(cfg, Clock.current),
            'linked_mode': LINK.linked_mode,
            'next_collective_target': lambda cfg, **kw: LINK.next_collective_target(cfg, now=Clock.current, **kw),
            'collective_continuation_target': lambda cfg, target: LINK.collective_continuation_target(cfg, target, Clock.current),
            'pending_collective_check': lambda cfg: LINK.pending_collective_check(cfg, Clock.current),
        }
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        mission_enum = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MC')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[mission_enum], type_ignores=[])),
                     str(SOURCE), 'exec'), dict(environment, Enum=Enum), environment)
        self.MC = environment['MC']
        self.select_mission = Mock(return_value=True)
        self.detect_best = Mock(return_value=(self.MC.AW3, 0))
        self._donate = Mock(side_effect=lambda _index:
                            self._record_attempt_before_submission())
        self._feed = Mock()
        # Legacy linkage cases use a material task. Quota/resume behavior has
        # its own actual-method regressions in test_collective_feed_quota.py.
        self._feed_resume_allowed = Mock(return_value=False)
        self._feed_to_quota = Mock(return_value=False)
        self._soul = Mock()
        self._bondling_fairyland = Mock()
        owned = ('run', '_mission_counter', '_return_to_missions', '_leave_missions',
                 '_schedule_collective', '_bondling_fairyland', '_collect_pending_bondling_reward',
                 '_collective_click_available')
        actual = methods(SOURCE, 'ScriptTask', owned, environment)
        self._actual_return = actual['_return_to_missions'].__get__(self)
        self._actual_leave = actual['_leave_missions'].__get__(self)
        self._actual_bondling = actual['_bondling_fairyland'].__get__(self)
        for name in ('run', '_mission_counter', '_schedule_collective', '_collect_pending_bondling_reward',
                     '_collective_click_available'):
            setattr(self, name, actual[name].__get__(self))
        self._return_to_missions = Mock(return_value=True)
        self._leave_missions = Mock(return_value=True)
        setter = methods(BASE, 'BaseTask', ('set_next_run',), {'datetime': Clock})['set_next_run']
        self.schedule_calls = []

        def set_next_run(**kwargs):
            self.schedule_calls.append(kwargs)
            return setter(self, **kwargs)

        self.set_next_run = set_next_run

    def _record_attempt_before_submission(self):
        if LINK.linked_mode(self.config):
            assert self.config.state.attempted_date == Clock.current.date().isoformat()
            assert self.config.snapshots[-1].collective_missions.missions_config.attempted_date == self.config.state.attempted_date

class CollectiveLinkTests(unittest.TestCase):
    def setUp(self):
        Clock.current = datetime(2026, 10, 6, 19, 30)
        self.config = FakeConfig()

    def queue(self, config=None, confirmed=True):
        return LINK.queue_collective_after_dokan(config or self.config,
                                                 confirmed=confirmed, now=Clock.current)

    def ready(self, config=None):
        config = config or self.config
        self.assertTrue(self.queue(config).queued)
        return config

    def run_task(self, task):
        with self.assertRaises(TaskEnd):
            task.run()

    def assert_parked(self, task):
        self.assertEqual(task.config.collective_missions.scheduler.next_run, LINK.LINK_IDLE_TARGET)
        self.assertEqual(len(task.schedule_calls), 1)
        call = task.schedule_calls[0]
        self.assertFalse(call['server'])
        self.assertTrue(call['finish'])
        self.assertIsNone(call['success'])

    def test_final_dokan_proof_and_queue_are_saved_together(self):
        self.assertTrue(self.queue().queued)
        self.assertEqual(self.config.queue_calls, [('CollectiveMissions', False)])
        self.assertEqual(len(self.config.snapshots), 1)
        saved = self.config.snapshots[-1].collective_missions
        self.assertEqual(saved.missions_config.dokan_finished_date, '2026-10-06')
        self.assertEqual(saved.scheduler.next_run, Clock.current)

    def test_first_of_two_challenges_cannot_queue(self):
        self.config.dokan.attack_count_config.remain_attack_count = 1
        self.assertEqual(self.queue().reason, 'dokan_challenges_pending')
        self.assertEqual(self.config.queue_calls, [])

    def test_unopened_unknown_or_skip_callback_is_not_proof(self):
        self.assertEqual(self.queue(confirmed=False).reason, 'dokan_unconfirmed')
        self.assertEqual(self.config.snapshots, [])

    def test_stale_date_and_unreadable_count_cannot_queue(self):
        self.config.dokan.attack_count_config.attack_date = '2026-10-05'
        self.assertEqual(self.queue().reason, 'dokan_date_unconfirmed')
        self.config.dokan.attack_count_config.attack_date = '2026-10-06'
        for count in (-1, True, None, '0', 2):
            with self.subTest(count=count):
                self.config.dokan.attack_count_config.remain_attack_count = count
                self.assertFalse(self.queue().queued)

    def test_configured_one_daily_challenge_can_queue_after_it_finishes(self):
        self.config.dokan.attack_count_config.daily_attack_count = 1
        self.config.dokan.attack_count_config.remain_attack_count = 1
        self.assertTrue(self.queue().queued)

    def test_disabled_or_independent_mode_never_auto_enables(self):
        for config, reason in ((FakeConfig(enabled=False), 'disabled'),
                               (FakeConfig(linked=False), 'independent')):
            self.assertEqual(self.queue(config).reason, reason)
            self.assertEqual(config.queue_calls, [])

    def test_duplicate_callback_and_worker_restart_keep_one_queue_entry(self):
        self.ready()
        self.assertEqual(self.queue().reason, 'already_queued')
        restarted = self.config.restarted()
        self.assertTrue(LINK.collective_gate(restarted, Clock.current).allowed)
        self.assertEqual(self.queue(restarted).reason, 'already_queued')
        self.assertEqual(restarted.queue_calls, [])

    def test_disabled_task_call_race_rolls_back_proof(self):
        self.config.task_call = Mock(return_value=False)
        self.assertFalse(self.queue().queued)
        self.assertEqual(self.config.state.dokan_finished_date, '')

    def test_failed_persistence_does_not_leave_in_memory_proof(self):
        self.config.task_call = Mock(side_effect=OSError('storage unavailable'))
        with self.assertRaises(OSError):
            self.queue()
        self.assertEqual(self.config.state.dokan_finished_date, '')

    def test_old_loaded_schema_fails_closed_when_link_state_is_missing(self):
        del self.config.state.attempted_date
        self.assertEqual(self.queue().reason, 'link_state_unavailable')
        self.assertEqual(self.config.snapshots, [])

    def test_submission_guard_survives_restart_and_cannot_start_twice(self):
        self.ready()
        self.assertTrue(LINK.begin_collective_attempt(self.config, Clock.current))
        restarted = self.config.restarted()
        self.assertTrue(LINK.collective_gate(restarted, Clock.current).verify_only)
        self.assertFalse(LINK.begin_collective_attempt(restarted, Clock.current))
        self.assertEqual(self.queue(restarted).reason, 'already_attempted')

    def test_completed_same_day_cannot_be_requeued_or_executed(self):
        self.ready()
        LINK.complete_collective(self.config, Clock.current)
        restarted = self.config.restarted()
        self.assertEqual(self.queue(restarted).reason, 'already_completed')
        self.assertFalse(LINK.collective_gate(restarted, Clock.current).allowed)

    def test_tomorrow_needs_its_own_dokan_proof(self):
        self.ready()
        Clock.current += timedelta(days=1)
        self.assertEqual(LINK.collective_gate(self.config, Clock.current).reason, 'waiting_dokan')

    def test_friday_saturday_sunday_never_queue_even_with_completed_dokan(self):
        for date in ('2026-10-09', '2026-10-10', '2026-10-11'):
            with self.subTest(date=date):
                Clock.current = datetime.fromisoformat(date + 'T19:30:00')
                self.config.dokan.attack_count_config.attack_date = date
                self.assertEqual(self.queue().reason, 'outside_days')
                self.assertFalse(LINK.collective_gate(self.config, Clock.current).allowed)

    def test_old_fixed_time_without_dokan_proof_parks_without_game_actions(self):
        task = Runtime(self.config)
        self.run_task(task)
        self.assert_parked(task)
        task.goto_page.assert_not_called()
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()

    def test_independent_weekend_gate_does_not_select_or_donate(self):
        Clock.current = datetime(2026, 10, 9, 19, 30)
        task = Runtime(FakeConfig(linked=False, restricted=True))
        self.run_task(task)
        self.assertEqual(task.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 12, 19, 30))
        task.goto_page.assert_not_called()
        task._donate.assert_not_called()

    def test_unrestricted_independent_mode_is_still_available_on_friday(self):
        Clock.current = datetime(2026, 10, 9, 19, 30)
        task = Runtime(FakeConfig(linked=False, restricted=False))
        self.run_task(task)
        task._donate.assert_called_once()
        self.assertEqual(task.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 10, 19, 30))

    def test_success_requires_real_counter_and_saves_guard_before_donation(self):
        task = Runtime(self.ready())
        self.run_task(task)
        task._donate.assert_called_once()
        self.assertEqual(self.config.state.completed_date, '2026-10-06')
        self.assert_parked(task)

    def test_selector_failure_does_not_consume_submission_guard_and_can_retry(self):
        task = Runtime(self.ready(), ((20, 10, 30),))
        task.select_mission.return_value = False
        self.run_task(task)
        task._donate.assert_not_called()
        task.detect_best.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 19, 33))
        self.assertFalse(task.schedule_calls[0]['server'])
        recovery = Runtime(self.config.restarted())
        self.run_task(recovery)
        recovery._donate.assert_called_once()
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(recovery.config.state.completed_date, '2026-10-06')

    def test_existing_earned_reward_finishes_quota_before_refresh_or_submission(self):
        task = Runtime(self.ready(), ((20, 10, 30), (30, 0, 30), (30, 0, 30)))
        visible = [True]
        task.appear.side_effect = lambda marker: marker == task.I_CM_REWARDS and visible[0]

        def claim(marker, **kw):
            if marker == task.I_CM_REWARDS and visible[0]:
                visible[0] = False
                return True
            return False

        task.appear_then_click.side_effect = claim
        self.run_task(task)
        task.appear_then_click.assert_any_call(task.I_CM_REWARDS, interval=1)
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.state.completed_date, '2026-10-06')
        self.assert_parked(task)

    def test_unclaimed_earned_reward_cannot_be_refreshed_or_spend_materials(self):
        task = Runtime(self.ready(), ((20, 10, 30),))
        task.appear.side_effect = lambda marker: marker == task.I_CM_REWARDS
        task.appear_then_click.side_effect = lambda marker, **kw: marker == task.I_CM_REWARDS
        self.run_task(task)
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()
        self.assertLessEqual(task.appear_then_click.call_count, 6)
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.state.completed_date, '')

    def test_unsupported_detected_task_never_consumes_submission_guard(self):
        task = Runtime(self.ready(), ((20, 10, 30),))
        task.detect_best.return_value = (task.MC.UNKNOWN, 0)
        self.run_task(task)
        task._donate.assert_not_called()
        task._feed.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')

    def test_six_star_refresh_only_card_never_begins_or_submits_any_resource(self):
        task = Runtime(self.ready(), ((20, 10, 30),))
        task.detect_best.return_value = (task.MC.SO3, 0)
        with unittest.mock.patch.object(LINK, 'begin_collective_attempt', wraps=LINK.begin_collective_attempt) as begin:
            self.run_task(task)
        begin.assert_not_called()
        task._soul.assert_not_called()
        task._feed.assert_not_called()
        task._feed_to_quota.assert_not_called()
        task._donate.assert_not_called()
        task._bondling_fairyland.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 19, 33))

    def test_disabled_task_with_six_star_card_cannot_enter_selection_or_submission(self):
        task = Runtime(FakeConfig(enabled=False), ())
        task.detect_best.return_value = (task.MC.SO3, 0)
        self.run_task(task)
        task.goto_page.assert_not_called()
        task.select_mission.assert_not_called()
        task.detect_best.assert_not_called()
        task._soul.assert_not_called()
        task._feed.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(task.config.state.attempted_date, '')

    def test_completed_today_reentry_never_claims_or_spends_again(self):
        for linked in (False, True):
            config = FakeConfig(linked=linked)
            if linked:
                self.ready(config)
            LINK.complete_collective(config, Clock.current)
            task = Runtime(config.restarted(), ())
            self.run_task(task)
            task.goto_page.assert_not_called()
            task.select_mission.assert_not_called()
            task.appear_then_click.assert_not_called()
            task._donate.assert_not_called()

    def test_reward_claim_must_precede_selection_when_more_quota_remains(self):
        task = Runtime(self.ready(), ((10, 20, 30), (20, 10, 30), (30, 0, 30), (30, 0, 30)))
        visible = [True]
        events = []
        task.appear.side_effect = lambda marker: marker == task.I_CM_REWARDS and visible[0]

        def claim(marker, **kw):
            if marker == task.I_CM_REWARDS and visible[0]:
                events.append('claim')
                visible[0] = False
                return True
            return False

        task.appear_then_click.side_effect = claim
        task.select_mission.side_effect = lambda _: events.append('select') or True
        self.run_task(task)
        self.assertEqual(events, ['claim', 'select'])
        task._donate.assert_called_once()
        self.assertEqual(self.config.state.completed_date, '2026-10-06')

    def test_selector_owner_takeover_propagates_without_consuming_submission_guard(self):
        task = Runtime(self.ready(), ((20, 10, 30),))
        task.select_mission.side_effect = AccountLoggedInElsewhere('owner login')
        with self.assertRaises(AccountLoggedInElsewhere):
            task.run()
        task._donate.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(task.schedule_calls, [])

    def test_dispatch_return_without_full_counter_is_not_completed(self):
        task = Runtime(self.ready(), ((0, 30, 30), (15, 15, 30)))
        self.run_task(task)
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.state.attempted_date, '2026-10-06')
        self.assert_parked(task)

    def test_settled_submission_reward_phase_does_not_replace_full_counter_confirmation(self):
        task = Runtime(self.ready(), ((20, 10, 30), (20, 10, 30)))
        task._donate.side_effect = None
        task._donate.return_value = True
        self.run_task(task)
        task._donate.assert_called_once()
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.state.attempted_date, '2026-10-06')
        self.assert_parked(task)

    def test_bondling_continuation_only_claims_visible_reward_then_confirms_counter(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        self.config.state.pending_kind = 'bondling_reward'
        self.config.save()
        task = Runtime(self.config.restarted(), ((0, 30, 30), (30, 0, 30), (30, 0, 30)))
        task.appear.side_effect = lambda marker: marker == task.I_CM_REWARDS
        task.appear_then_click.side_effect = lambda marker, **kw: marker == task.I_CM_REWARDS
        self.run_task(task)
        task.appear_then_click.assert_any_call(task.I_CM_REWARDS, interval=1)
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()
        task._bondling_fairyland.assert_not_called()
        self.assertEqual(task.config.queue_calls, [])
        self.assertEqual(task.config.state.completed_date, '2026-10-06')
        self.assertEqual(task.config.state.pending_kind, '')
        self.assert_parked(task)

    def test_unavailable_bondling_reward_parks_without_new_task_or_submission(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        self.config.state.pending_kind = 'bondling_reward'
        self.config.save()
        task = Runtime(self.config.restarted(), ((0, 30, 30), (0, 30, 30)))
        self.run_task(task)
        task.appear_then_click.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(task.config.queue_calls, [])
        self.assertEqual(task.config.state.completed_date, '')
        self.assert_parked(task)

    def test_unproven_previous_attempt_never_claims_visible_rewards(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        task = Runtime(self.config.restarted(), ((0, 30, 30),))
        task.appear.return_value = True
        self.run_task(task)
        task.appear_then_click.assert_not_called()
        self.assertEqual(task.config.state.completed_date, '')
        self.assert_parked(task)

    def test_initial_ocr_failure_cannot_select_or_donate_and_retries_same_day(self):
        task = Runtime(self.ready(), ((0, 0, 0),))
        self.run_task(task)
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(self.config.state.attempted_date, '')
        self.assertEqual(self.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 19, 33))

    def test_inconsistent_or_single_full_counter_is_not_completion(self):
        for readings in (((30, 1, 30),), ((30, 0, 30), (0, 0, 0))):
            with self.subTest(readings=readings):
                config = self.ready(FakeConfig())
                task = Runtime(config, readings)
                self.run_task(task)
                self.assertEqual(config.state.completed_date, '')
                task._donate.assert_not_called()

    def test_restart_after_incomplete_attempt_only_verifies_and_never_submits(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        task = Runtime(self.config.restarted(), ((0, 30, 30),))
        self.run_task(task)
        task.select_mission.assert_not_called()
        task._donate.assert_not_called()
        self.assertEqual(task.config.state.completed_date, '')
        self.assert_parked(task)

    def test_restart_after_completed_game_counter_records_completion_without_submission(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        task = Runtime(self.config.restarted(), ((30, 0, 30), (30, 0, 30)))
        self.run_task(task)
        self.assertEqual(task.config.state.completed_date, '2026-10-06')
        task._donate.assert_not_called()
        self.assert_parked(task)

    def test_owner_takeover_propagates_without_marking_complete(self):
        task = Runtime(self.ready())
        task._donate.side_effect = AccountLoggedInElsewhere('owner took over')
        with self.assertRaises(AccountLoggedInElsewhere):
            task.run()
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.state.attempted_date, '2026-10-06')

        self.assertEqual(task.schedule_calls, [])

    def test_pending_bondling_two_hour_check_is_not_overwritten_by_daily_delay(self):
        task = Runtime(self.ready(), ((0, 30, 30), (0, 30, 30)))
        task.detect_best.return_value = (task.MC.BL, 0)
        task._bondling_fairyland = task._actual_bondling
        self.run_task(task)
        self.assertEqual(self.config.queue_calls[-1], ('BondlingFairyland', False))
        self.assertEqual(self.config.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 21, 30))
        self.assertEqual(self.config.state.completed_date, '')
        self.assertEqual(self.config.state.attempted_date, '2026-10-06')
        self.assertEqual(self.config.snapshots[-1].collective_missions.missions_config.pending_kind,
                         'bondling_reward')
        self.assertEqual(self.config.snapshots[-1].collective_missions.missions_config.pending_until,
                         '2026-10-06 21:30:00')

    def test_crash_after_child_queue_save_recovers_pending_time_without_requeueing_child(self):
        task = Runtime(self.ready(), ((0, 30, 30),))
        task.detect_best.return_value = (task.MC.BL, 0)
        task._bondling_fairyland = task._actual_bondling
        task._return_to_missions.side_effect = RuntimeError('simulated crash before CM delay save')
        with self.assertRaises(RuntimeError):
            task.run()
        restarted = self.config.restarted()
        self.assertEqual(restarted.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 19, 30))
        recovery = Runtime(restarted, ())
        self.run_task(recovery)
        self.assertEqual(restarted.collective_missions.scheduler.next_run,
                         datetime(2026, 10, 6, 21, 30))
        recovery.goto_page.assert_not_called()
        recovery._donate.assert_not_called()
        self.assertEqual(restarted.queue_calls, [])

    def test_expired_pending_time_only_allows_reward_claiming(self):
        self.ready()
        LINK.begin_collective_attempt(self.config, Clock.current)
        self.config.state.pending_kind = 'bondling_reward'
        self.config.state.pending_until = '2026-10-06 21:30:00'
        self.config.save()
        Clock.current = datetime(2026, 10, 6, 21, 31)
        task = Runtime(self.config.restarted(), ((0, 30, 30), (30, 0, 30), (30, 0, 30)))
        task.appear.side_effect = lambda marker: marker == task.I_CM_REWARDS
        task.appear_then_click.side_effect = lambda marker, **kw: marker == task.I_CM_REWARDS
        self.run_task(task)
        self.assertEqual(task.config.state.completed_date, '2026-10-06')
        self.assertEqual(task.config.state.pending_until, '')
        self.assertEqual(task.config.queue_calls, [])
        task.select_mission.assert_not_called()
        self.assert_parked(task)

    def test_linked_wait_and_next_day_retry_do_not_create_daily_wakes(self):
        self.ready()
        self.assertEqual(LINK.next_collective_target(self.config, waiting=True, now=Clock.current),
                         LINK.LINK_IDLE_TARGET)
        self.config.collective_missions.scheduler.failure_interval = timedelta(days=1)
        self.assertEqual(LINK.next_collective_target(self.config, now=Clock.current), LINK.LINK_IDLE_TARGET)

    def test_linked_thursday_retry_and_continuation_cannot_wake_on_friday(self):
        Clock.current = datetime(2026, 10, 8, 23, 59)
        self.config.state.dokan_finished_date = '2026-10-08'
        self.assertEqual(LINK.next_collective_target(self.config, now=Clock.current), LINK.LINK_IDLE_TARGET)
        self.assertEqual(LINK.collective_continuation_target(
            self.config, Clock.current + timedelta(hours=2), Clock.current), LINK.LINK_IDLE_TARGET)

    def test_independent_retry_and_forced_clock_skip_friday_through_sunday(self):
        Clock.current = datetime(2026, 10, 8, 23, 59)
        config = FakeConfig(linked=False, restricted=True)
        self.assertEqual(LINK.next_collective_target(config, now=Clock.current),
                         datetime(2026, 10, 12, 0, 2))
        config.collective_missions.scheduler.server_update = time(6)
        self.assertEqual(LINK.next_collective_target(config, now=Clock.current),
                         datetime(2026, 10, 12, 6))

    def test_navigation_and_exit_remain_bounded_on_unknown_frames(self):
        task = Runtime(self.ready())
        self.assertFalse(task._actual_return())
        self.assertFalse(task._actual_leave())
        # Read-only daily feedback adds at most two fresh proof frames before
        # the existing bounded navigation/exit loops.
        self.assertLessEqual(task.screenshot.call_count, 8)


class ConfigSchemaTests(unittest.TestCase):
    def test_legacy_defaults_and_hidden_state_roundtrip(self):
        from pydantic import BaseModel, Field, SerializationInfo, field_serializer, validator
        common = ROOT / 'tasks/Component/config_base.py'
        tree = ast.parse(common.read_text(encoding='utf-8-sig'))
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('serializer_exclude', 'dynamic_hide')]
        env = {'field_serializer': field_serializer, 'SerializationInfo': SerializationInfo}
        exec(compile(ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[])),
                     str(common), 'exec'), env)
        source = ROOT / 'tasks/CollectiveMissions/config.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MissionsConfig')
        env.update(BaseModel=BaseModel, Field=Field, validator=validator, MultiLine=str)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                     str(source), 'exec'), env)
        model = env['MissionsConfig']()
        self.assertFalse(model.monday_to_thursday)
        self.assertFalse(model.run_after_dokan)
        model.dokan_finished_date = '2026-10-06'
        model.attempted_date = '2026-10-06'
        saved = model.model_dump()
        restored = env['MissionsConfig'](**saved)
        self.assertEqual(restored.attempted_date, '2026-10-06')
        hidden = restored.model_dump(context={'hide': True})
        for name in ('dokan_finished_date', 'attempted_date', 'completed_date', 'pending_kind', 'pending_until'):
            self.assertEqual(hidden[name], 0xABCDEF)
        self.assertIs(hidden['run_after_dokan'], False)


if __name__ == '__main__':
    unittest.main()
