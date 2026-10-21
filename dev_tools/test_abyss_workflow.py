"""Offline XY scheduling, quota planning and crash-resume regression checks."""
from datetime import date, datetime, time, timedelta
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as S
import unittest


ROOT = Path(os.environ.get('ABYSS_TEST_ROOT', Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('abyss_workflow_under_test',
                                            ROOT / 'tasks/AbyssShadows/workflow.py')
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
TargetPlan = workflow.TargetPlan
TIMES = S(custom_run_time_friday=time(19), custom_run_time_saturday=time(20),
          custom_run_time_sunday=time(18))


class OpeningTests(unittest.TestCase):
    def next(self, now, **kwargs):
        return workflow.next_run(now, TIMES, timedelta(minutes=3), **kwargs)

    def test_before_start_and_weekdays_wait_for_correct_slot(self):
        self.assertEqual(self.next(datetime(2026,10,16,18,59)), datetime(2026,10,16,19))
        self.assertEqual(self.next(datetime(2026,10,14,19)), datetime(2026,10,16,19))

    def test_unopened_retries_from_now_not_original_start(self):
        self.assertEqual(self.next(datetime(2026,10,16,19,7,12)), datetime(2026,10,16,19,10,12))

    def test_one_hour_and_success_advance_calendar(self):
        self.assertEqual(self.next(datetime(2026,10,16,20)), datetime(2026,10,17,20))
        self.assertEqual(self.next(datetime(2026,10,16,19,20),success=True),datetime(2026,10,17,20))
        self.assertEqual(self.next(datetime(2026,10,17,21)),datetime(2026,10,18,18))
        self.assertEqual(self.next(datetime(2026,10,18,19)),datetime(2026,10,23,19))

    def test_legacy_day_interval_becomes_three_minutes(self):
        for interval in (timedelta(days=1), '01 00:00:00', timedelta(0)):
            self.assertEqual(workflow.next_run(datetime(2026,10,16,19), TIMES, interval),
                             datetime(2026,10,16,19,3))
        self.assertEqual(workflow.retry_interval('00 00:05:00'),timedelta(minutes=5))


class PlanningTests(unittest.TestCase):
    def complete(self, plan, unavailable=()):
        seen=[]
        for _ in range(25):
            code=plan.next_target()
            if code is None:
                return seen
            self.assertNotIn(code,seen)
            seen.append(code)
            plan.current_type=workflow.enemy_type(code)
            if code in unavailable:
                plan.mark_unavailable(code)
            else:
                plan.mark_done(code)
        self.fail('planner repeated or exceeded all 24 targets')

    def test_old_orders_fill_two_four_six(self):
        for smart in (False,True):
            plan=TargetPlan(workflow.legacy_order(smart))
            self.assertEqual(len(self.complete(plan)),12)
            self.assertEqual(plan.counts,workflow.QUOTAS)

    def test_partial_types_are_filled_from_later_regions(self):
        plan=TargetPlan(workflow.legacy_order(True))
        missing={'A-2','A-4','B-5'}
        self.complete(plan,missing)
        self.assertEqual(plan.counts,workflow.QUOTAS)
        self.assertTrue(any(code.startswith('C-') for code in plan.done))

    def test_sealed_regions_do_not_prevent_later_quotas(self):
        plan=TargetPlan(workflow.legacy_order(False))
        plan.seal_area('A');plan.seal_area('B')
        self.complete(plan)
        self.assertEqual(plan.counts,workflow.QUOTAS)
        self.assertTrue(all(code[0] in 'CD' for code in plan.done))

    def test_resume_finishes_current_general_group_before_elites(self):
        plan=TargetPlan(['A-2','A-4'],done=['A-2','A-3','B-2'])
        self.assertEqual(plan.next_target(),'B-3')
        plan.mark_done('B-3')
        self.assertEqual(plan.next_target(),'A-4')

    def test_disable_completion_only_executes_requested_order(self):
        plan=TargetPlan(['A-1'],complete=False)
        self.assertEqual(self.complete(plan),['A-1'])

    def test_exhaustion_stops_without_fabricating_targets(self):
        plan=TargetPlan(['A-1'],unavailable=workflow.ALL_TARGETS)
        self.assertIsNone(plan.next_target())
        self.assertEqual(plan.counts,{'BOSS':0,'GENERAL':0,'ELITE':0})

    def test_repeated_target_is_counted_once(self):
        plan=TargetPlan(['A-1','A-1'],done=['A-1','A-1'])
        plan.mark_done('A-1')
        self.assertEqual(plan.counts['BOSS'],1)

    def test_xy_order_expansion_and_validation(self):
        self.assertEqual(workflow.parse_order('A-1;B;A-1')[:4],['A-1','B-4','B-5','B-6'])
        self.assertEqual(workflow.parse_order('2'),['A-2','B-2','C-2','D-2'])
        with self.assertRaises(ValueError):workflow.parse_order('A-7')


class CheckpointTests(unittest.TestCase):
    def test_interrupted_run_resumes_only_missing_targets(self):
        with tempfile.TemporaryDirectory() as root:
            store=workflow.ProgressStore(root,'01-test',date(2026,10,16))
            plan=TargetPlan(workflow.legacy_order(True))
            for code in ['A-1','B-1','A-2']:plan.mark_done(code)
            plan.mark_unavailable('A-3');store.save(plan)
            done,unavailable=workflow.ProgressStore(root,'01-test',date(2026,10,16)).load()
            resumed=TargetPlan(workflow.legacy_order(True),done,unavailable)
            self.assertEqual(resumed.next_target(),'B-2')
            self.assertEqual(resumed.counts,{'BOSS':2,'GENERAL':1,'ELITE':0})
            self.assertEqual(workflow.ProgressStore(root,'02-test',date(2026,10,16)).load(),([],[]))
            self.assertEqual(workflow.ProgressStore(root,'01-test',date(2026,10,17)).load(),([],[]))
            store.clear();self.assertEqual(store.load(),([],[]))

    def test_invalid_account_cannot_escape_state_directory(self):
        with self.assertRaises(ValueError):
            workflow.ProgressStore('.', '../other',date.today())


if __name__=='__main__':unittest.main()
