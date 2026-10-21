"""Run the actual Abyss checkpoint/closeout flow without emulator IO."""
import ast
import copy
from datetime import datetime, timedelta
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock, patch
from dev_tools.test_abyss_workflow import workflow, TIMES

ROOT = Path(os.environ.get('ABYSS_TEST_ROOT', Path(__file__).resolve().parents[1]))
SOURCE = ROOT / 'tasks/AbyssShadows/script_task.py'

class TaskEnd(Exception):pass
class Clock(datetime):
    current=datetime(2026,10,16,19,10)
    @classmethod
    def now(cls):return cls.current

class Marker(str):
    @property
    def name(self):return str(self)

AREAS=S(DRAGON=Marker('dragon'),PEACOCK=Marker('peacock'),
        FOX=Marker('fox'),LEOPARD=Marker('leopard'))
CLICKS=S(BOSS=Marker('boss'),GENERAL_1=Marker('general1'),GENERAL_2=Marker('general2'),
         ELITE_1=Marker('elite1'),ELITE_2=Marker('elite2'),ELITE_3=Marker('elite3'))

class Timer:
    def __init__(self,*args):self.checks=0
    def start(self):return self
    def reached(self):
        self.checks+=1
        return self.checks>8

def load_task():
    tree=ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    selected=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask'))
    selected.bases=[]
    keep={'run','init_target_plan','refresh_target_counts','save_target_plan','target_area',
          'target_click','execute_target','schedule_abyss','defer_abyss','finish_abyss',
          'run_general_battle_back','_preset_selection_interrupted','wait_abyss_attack'}
    selected.body=[n for n in selected.body if isinstance(n,ast.FunctionDef) and n.name in keep]
    env={'datetime':Clock,'timedelta':timedelta,'logger':Mock(),'TaskEnd':TaskEnd,
         'AbyssShadows':object,'AreaType':AREAS,'CilckArea':CLICKS,'Path':Path,
         '__file__':str(SOURCE),'page_shikigami_records':'records','Timer':Timer,
         'GamePageUnknownError':RuntimeError,'time':S(time=Mock(return_value=100.0))}
    for name in ('TargetPlan','QUOTAS','enemy_type','legacy_order','next_run','opening_slots','parse_order'):
        env[name]=getattr(workflow,name)
    env['ProgressStore']=lambda root,account,day:workflow.ProgressStore(env['test_root'],account,day)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[selected],type_ignores=[])),str(SOURCE),'exec'),env)
    return env['ScriptTask'],env

ACTUAL,ENV=load_task()

def runtime(*,smart=False,enemies=True):
    task=ACTUAL()
    task.config=S(config_name='01-test',abyss_shadows=S(
        switch_soul_config=S(enable=False,enable_switch_by_name=False),
        abyss_shadows_combat_time=S(CombatTime_enable=smart,boss_combat_time=0,general_combat_time=0,elite_combat_time=0),
        abyss_shadows_time=TIMES,scheduler=S(failure_interval=timedelta(minutes=3)),
        general_battle_config=S(preset_enable=False,lock_team_enable=False)))
    task.device=Mock();task.screenshot=Mock();task.goto_page=Mock();task.goto_main=Mock()
    task.goto_abyss_shadows=Mock();task.set_next_run=Mock();task.wait_until_appear=Mock(return_value=True)
    task.wait_abyss_attack=Mock(return_value='attack')
    task.current_area=AREAS.DRAGON;task.sealed=set()
    def change(area):
        if area in task.sealed:return False
        task.current_area=area
        return True
    task.change_area=Mock(side_effect=change);task.select_boss=Mock(side_effect=change)
    task.is_area_selection=Mock(return_value=True);task.is_area_sealed=lambda area:area in task.sealed
    task.battles=[]
    def available(marker):
        if not enemies:return False
        task.battles.append((task.current_area,marker));return True
    task.click_emeny_area=Mock(side_effect=available);task.run_general_battle_back=Mock(return_value=True)
    task.appear=Mock(return_value=False);task.appear_then_click=Mock(return_value=False)
    task.ui_reward_appear_click=Mock(return_value=False)
    for name in ('IS_ATTACK','ABYSS_ATTACK_XY','ABYSS_FINISHED_XY','EQUIPPING','WIN','EXIT','EXIT_ENSURE','ABYSS_NAVIGATION'):
        setattr(task,'I_'+name,Marker(name))
    return task

class AbyssCloseoutEvents(unittest.TestCase):
    def setUp(self):
        Clock.current=datetime(2026,10,16,19,10)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        ENV['test_root']=Path(self.temp.name);self.report=Mock()
        patcher=patch.dict(sys.modules,{'tasks.Component.daily_closeout':S(report_closeout_outcome=self.report)})
        patcher.start();self.addCleanup(patcher.stop)
    def run_to_end(self,task):
        with self.assertRaises(TaskEnd):task.run()
    def outcome(self,outcome):
        self.report.assert_called_once()
        self.assertEqual(self.report.call_args.args[1:],('AbyssShadows',outcome))

    def test_normal_and_smart_complete_twelve_unique_targets(self):
        for smart in (False,True):
            self.report.reset_mock();task=runtime(smart=smart);self.run_to_end(task)
            self.outcome('completed');self.assertEqual(task._closeout_fight_counts,workflow.QUOTAS)
            self.assertEqual(len(set(task.battles)),12)
            task.set_next_run.assert_called_once_with(task='AbyssShadows',server=False,target=datetime(2026,10,17,20))
            self.assertEqual(task.progress_store.load(),([],[]))

    def test_missing_targets_fill_later_regions(self):
        task=runtime(smart=True);available=task.click_emeny_area.side_effect
        task.click_emeny_area.side_effect=lambda marker: (
            False if task.current_area==AREAS.DRAGON and marker in (CLICKS.GENERAL_2,CLICKS.ELITE_3)
            else available(marker))
        self.run_to_end(task);self.outcome('completed')
        self.assertEqual(task.target_plan.counts,workflow.QUOTAS);self.assertIn('C-2',task.target_plan.done)

    def test_sealed_first_two_regions_are_skipped(self):
        task=runtime();task.sealed={AREAS.DRAGON,AREAS.PEACOCK}
        self.run_to_end(task);self.outcome('completed')
        self.assertTrue(all(area in (AREAS.FOX,AREAS.LEOPARD) for area,_ in task.battles))

    def test_exhausted_targets_finish_as_skipped(self):
        task=runtime(enemies=False);self.run_to_end(task);self.outcome('skipped')
        self.assertEqual(task.target_plan.counts,{'BOSS':0,'GENERAL':0,'ELITE':0})
        self.assertEqual(task.click_emeny_area.call_count,24)

    def test_unopened_returns_and_schedules_three_minutes(self):
        task=runtime();task.select_boss.side_effect=None;task.select_boss.return_value=False
        self.run_to_end(task);self.report.assert_not_called();task.run_general_battle_back.assert_not_called()
        task.set_next_run.assert_called_once_with(task='AbyssShadows',server=False,target=datetime(2026,10,16,19,13))
        task.goto_main.assert_called_once()

    def test_unknown_opening_page_defers_instead_of_stopping(self):
        task=runtime();task.select_boss.side_effect=RuntimeError('Cannot identify opening page')
        self.run_to_end(task);self.report.assert_not_called()
        self.assertEqual(task.set_next_run.call_args.kwargs['target'],datetime(2026,10,16,19,13))

    def test_assembly_timeout_defers_without_battle(self):
        task=runtime();task.wait_abyss_attack.return_value='waiting'
        self.run_to_end(task);self.report.assert_not_called()
        task.run_general_battle_back.assert_not_called();task.device.stuck_record_clear.assert_called_once()

    def test_finished_activity_exits_without_battling(self):
        task=runtime();task.wait_abyss_attack.return_value='finished'
        self.run_to_end(task);self.outcome('skipped');task.run_general_battle_back.assert_not_called()

    def test_activity_finishes_between_targets_without_starting_another(self):
        task=runtime();task._abyss_closeout_date=Clock.current.date();task.init_target_plan()
        task.execute_target('A-1')
        task.appear.side_effect=lambda marker:marker==task.I_ABYSS_FINISHED_XY
        with self.assertRaises(TaskEnd):task.execute_target('A-2')
        self.outcome('skipped');self.assertEqual(task.target_plan.done,['A-1'])
        self.assertEqual(task.run_general_battle_back.call_count,1)

    def test_finished_map_after_battle_returns_before_saving_target(self):
        task=runtime();task.wait_until_appear.return_value=False
        task.appear.side_effect=lambda marker:marker==task.I_ABYSS_FINISHED_XY
        self.assertTrue(ACTUAL.run_general_battle_back(task,'BOSS'))
        task.appear_then_click.assert_not_called()

    def test_attack_wait_recognizes_xy_and_finished_states(self):
        task=runtime()
        for marker,result in ((task.I_ABYSS_ATTACK_XY,'attack'),(task.I_IS_ATTACK,'attack'),
                              (task.I_ABYSS_FINISHED_XY,'finished')):
            task.appear.side_effect=lambda shown:shown==marker
            self.assertEqual(ACTUAL.wait_abyss_attack(task),result)
        task.appear.side_effect=None;task.appear.return_value=False
        self.assertEqual(ACTUAL.wait_abyss_attack(task),'waiting')

    def test_non_activity_day_and_before_start_do_not_enter(self):
        for now in (datetime(2026,10,14,19),datetime(2026,10,16,18)):
            Clock.current=now;task=runtime();self.run_to_end(task);task.goto_abyss_shadows.assert_not_called()
        self.report.assert_not_called()

    def test_expired_opening_reports_expired_without_entering(self):
        Clock.current=datetime(2026,10,16,20)
        task=runtime();self.run_to_end(task);task.goto_abyss_shadows.assert_not_called()
        self.outcome('expired')

    def test_battle_exception_retains_progress_and_resume_does_not_repeat(self):
        first=runtime(smart=True);first.run_general_battle_back.side_effect=[True,True,RuntimeError('emulator interrupted')]
        with self.assertRaisesRegex(RuntimeError,'interrupted'):first.run()
        self.report.assert_not_called();self.assertEqual(first.progress_store.load()[0],['A-1','B-1'])
        second=runtime(smart=True);self.run_to_end(second);self.outcome('completed')
        self.assertEqual(len(second.battles),10)
        self.assertTrue(all(marker!=CLICKS.BOSS for _,marker in second.battles))

    def test_resume_after_opening_window_keeps_started_run(self):
        store=workflow.ProgressStore(ENV['test_root'],'01-test',Clock.current.date())
        plan=workflow.TargetPlan([],done=['A-1','B-1']);store.save(plan)
        Clock.current=datetime(2026,10,16,20,5)
        task=runtime(smart=True);self.run_to_end(task);self.outcome('completed');self.assertEqual(len(task.battles),10)

    def test_xy_two_attempt_limit_records_one_target(self):
        task=runtime();task._abyss_closeout_date=Clock.current.date();task.init_target_plan()
        task.run_general_battle_back.return_value=False;task.execute_target('A-1')
        self.assertEqual(task.run_general_battle_back.call_count,2);self.assertEqual(task.target_plan.done,['A-1'])

    def test_target_disappears_after_first_attempt_is_done_once(self):
        task=runtime();task._abyss_closeout_date=Clock.current.date();task.init_target_plan()
        task.click_emeny_area.side_effect=[True,False];task.run_general_battle_back.return_value=False
        task.execute_target('A-1');self.assertEqual(task.target_plan.done,['A-1'])
        self.assertEqual(task.run_general_battle_back.call_count,1)

    def test_unavailable_target_does_not_count_as_completed(self):
        task=runtime(enemies=False);task._abyss_closeout_date=Clock.current.date();task.init_target_plan()
        task.execute_target('A-1');self.assertEqual(task.target_plan.done,[])
        self.assertIn('A-1',task.target_plan.unavailable)

    def test_return_or_schedule_failure_keeps_checkpoint_and_no_closeout(self):
        for method in ('goto_main','set_next_run'):
            task=runtime();getattr(task,method).side_effect=RuntimeError('interrupted finish')
            with self.assertRaisesRegex(RuntimeError,'interrupted finish'):task.run()
            self.report.assert_not_called();self.assertEqual(len(task.progress_store.load()[0]),12)

    def test_midnight_does_not_mark_new_day_completed(self):
        task=runtime();task.goto_main.side_effect=lambda:setattr(Clock,'current',datetime(2026,10,17,0,1))
        self.run_to_end(task);self.report.assert_not_called()

    def test_existing_process_without_new_config_fields_uses_defaults(self):
        task=runtime();self.assertFalse(hasattr(task.config.abyss_shadows,'process_manage'))
        self.run_to_end(task);self.outcome('completed')

    def test_switch_off_completion_honors_custom_order(self):
        task=runtime();task.config.abyss_shadows.process_manage=S(attack_order='A-1',try_complete_enemy_count=False)
        self.run_to_end(task);self.assertEqual(len(task.battles),1);self.outcome('skipped')

    def test_smart_victory_is_drained_before_return_without_exit_click(self):
        task=runtime(smart=True);task.config.abyss_shadows.abyss_shadows_combat_time.boss_combat_time=60
        task.wait_until_appear.return_value=False;shown=[task.I_WIN];clicks=[]
        task.appear.side_effect=lambda marker:marker==shown[0]
        def click(marker,**kwargs):
            if marker==shown[0]:
                clicks.append(marker);shown[0]=task.I_ABYSS_NAVIGATION;return True
            return False
        task.appear_then_click.side_effect=click
        self.assertTrue(ACTUAL.run_general_battle_back(task,'BOSS'));self.assertEqual(clicks,[task.I_WIN])

if __name__=='__main__':unittest.main()
