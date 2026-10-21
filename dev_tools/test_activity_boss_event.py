"""Real UI/OCR samples plus boss entry, settlement and completed-counter guards."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

import cv2

from dev_tools.test_activity_ticket_event import (
    load_event, FakeClock, FakeLogger, GameStuckError, RequestHumanTakeover,
    extract_methods, FIXTURES, SCRIPT,
)

ROOT = Path(__file__).resolve().parents[1]


class BossEventTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(step=0.5)
        self.ns = load_event(self.clock)
        tree = ast.parse((ROOT/'tasks/ActivityShikigami/current_boss.py').read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        exec(compile(tree, '<boss_event>', 'exec'), self.ns)
        self.task = self.ns['CurrentEventBoss']()
        self.task.device = NS(image=None,stuck_timer_long=object(),stuck_record_clear=Mock())
        self.task.screenshot = self.clock.advance
        self.task.appear = lambda _: False
        self.task.click = Mock()
        self.task.put_status = Mock()
        self.task.count_map = {'boss': 0}

    def image(self, name):
        return cv2.cvtColor(cv2.imread(str(FIXTURES/(name+'.png'))), cv2.COLOR_BGR2RGB)

    def test_boss_page_and_entry_are_distinct_from_ticket(self):
        for name in ('boss_page','boss_main','ticket_before','simple','stamina','reward'):
            self.task.appear = lambda rule: rule.match(self.image(name))
            self.assertEqual(self.task.boss_page_visible(), name == 'boss_page')
        self.assertTrue(self.task.I_EVENT_BOSS_ENTRY.match(self.image('boss_main')))
        self.assertTrue(self.task.I_EVENT_BOSS_RECORDS.match(self.image('boss_page')))
        self.assertFalse(self.task.I_EVENT_BOSS_ENTRY.match(self.image('boss_page')))
        self.assertTrue(self.task.I_EVENT_MAIN.match(self.image('boss_main')))

    def test_strict_counter_including_explicit_zero(self):
        parse = self.ns['parse_boss_bonus']
        for value in (0,1,65,100):
            for text in (f'{value}/100', f'额外奖励 {value} / 100'):
                self.assertEqual(parse(text),value)
        for text in ('',None,'0','100','65/60','/100','-1/100','101/100',
                     '6?/100','O/100','65/1000','额外奖励/100','065/100'):
            self.assertIsNone(parse(text), text)

    def frames(self, values, backup=None):
        it = iter(values)
        self.task.boss_page_visible = lambda: True
        self.task.O_EVENT_BOSS_BONUS = NS(ocr_single=Mock(side_effect=lambda _: next(it,values[-1])))
        backup = [''] if backup is None else backup
        secondary = iter(backup)
        self.task.O_EVENT_BOSS_BONUS_BACKUP = NS(ocr_single=Mock(side_effect=lambda _: next(secondary,backup[-1])))

    def test_two_consistent_frames_required_after_unknown(self):
        self.frames(['','66/100','65/100','65/100'])
        self.assertEqual(self.task.boss_read_bonus(),65)

    def test_confirm_explicit_zero_twice(self):
        self.frames(['','0/100','0/100'])
        self.assertEqual(self.task.boss_read_bonus(),0)

    def test_unreadable_counter_never_means_zero(self):
        self.frames([''])
        self.assertIsNone(self.task.boss_read_bonus())
        self.task.click.assert_not_called()

    def test_wrong_page_never_reads_counter(self):
        self.frames(['65/100'])
        self.task.boss_page_visible = lambda: False
        self.task.O_EVENT_BOSS_BONUS.ocr_single = Mock()
        with self.assertRaises(GameStuckError):
            self.task.boss_read_bonus()
        self.task.O_EVENT_BOSS_BONUS.ocr_single.assert_not_called()

    def test_unchanged_counter_after_victory_is_accepted(self):
        self.frames(['65/100'])
        self.assertEqual(self.task.boss_read_bonus(expected_min=65),65)

    def test_counter_decrease_cannot_be_accepted(self):
        self.frames(['64/100'])
        self.assertIsNone(self.task.boss_read_bonus(expected_min=65))

    def prepare_battle(self, before=65, after=66, win=True):
        self.task.boss_read_bonus = Mock(side_effect=[before,after])
        self.task.is_in_battle = lambda _: self.clock.now >= 102
        self.task.I_PREPARE_HIGHLIGHT='prepare'
        self.task.I_REWARD='reward'
        self.task.I_WIN='win'
        self.options={'success':NS(),'completion':NS()}
        self.task.loadout_from_config=lambda _: ({},self.options)
        self.task.get_general_battle_conf=lambda: None
        self.task.battle_wait=Mock(return_value=win)
        self.task.boss_wait_return=Mock()

    def test_100_never_clicks_or_starts_battle(self):
        self.prepare_battle(before=100)
        self.assertFalse(self.task.boss_battle_once())
        self.task.click.assert_not_called()
        self.task.battle_wait.assert_not_called()

    def test_65_resumes_only_remaining_and_confirms_increase(self):
        self.prepare_battle()
        self.assertTrue(self.task.boss_battle_once())
        self.task.click.assert_called_once_with(self.task.I_EVENT_BOSS_CHALLENGE)
        self.task.boss_wait_return.assert_called_once()
        self.task.boss_read_bonus.assert_called_with(expected_min=65)
        self.assertEqual(self.task._boss_completed,66)
        self.assertEqual(self.task.count_map['boss'],1)
        self.assertEqual(self.options['completion'].check_imgs,[self.task.I_EVENT_BOSS_PAGE])

    def test_last_bonus_finishes_without_extra_battle(self):
        self.prepare_battle(before=99,after=100)
        self.assertFalse(self.task.boss_battle_once())
        self.task.click.assert_called_once()
        self.assertEqual(self.task._boss_completed,99)
        self.assertEqual(self.task._boss_counted,100)
        self.task.boss_read_bonus.assert_called_once()

    def test_zero_is_a_new_counter_and_must_start_first_battle(self):
        self.prepare_battle(before=0,after=1)
        self.assertTrue(self.task.boss_battle_once())
        self.task.click.assert_called_once()
        self.assertEqual(self.task._boss_completed,1)

    def test_66_continues_with_34_remaining(self):
        self.prepare_battle(before=66,after=67)
        self.assertTrue(self.task.boss_battle_once())
        self.assertEqual(self.task._boss_completed,67)

    def test_entry_failure_only_clicks_once(self):
        self.prepare_battle()
        self.task.is_in_battle=lambda _:False
        with self.assertRaises(GameStuckError):
            self.task.boss_battle_once()
        self.task.click.assert_called_once()
        self.task.battle_wait.assert_not_called()
        self.assertEqual(self.task.count_map['boss'],0)

    def test_failed_battle_not_retried_or_counted(self):
        self.prepare_battle(win=False)
        with self.assertRaises(RequestHumanTakeover):
            self.task.boss_battle_once()
        self.task.boss_wait_return.assert_not_called()
        self.assertEqual(self.task.count_map['boss'],0)

    def test_boss_wait_allows_six_minute_battle_and_restores_device_guard(self):
        original=self.task.device.stuck_timer_long
        def wait():
            timer=self.task.device.stuck_timer_long
            self.clock.now += 360
            self.assertFalse(timer.reached())
            self.assertEqual(timer.limit,600)
            return True
        self.task.battle_wait=wait
        self.assertTrue(self.task.boss_battle_wait())
        self.assertIs(self.task.device.stuck_timer_long,original)
        self.task.device.stuck_record_clear.assert_called_once()

    def test_boss_wait_keeps_finite_timeout_and_restores_guard_on_error(self):
        original=self.task.device.stuck_timer_long
        def wait():
            timer=self.task.device.stuck_timer_long
            self.clock.now += 601
            for _ in range(301):expired=timer.reached()
            self.assertTrue(expired)
            raise GameStuckError('battle timeout')
        self.task.battle_wait=wait
        with self.assertRaises(GameStuckError):self.task.boss_battle_wait()
        self.assertIs(self.task.device.stuck_timer_long,original)
        self.task.device.stuck_record_clear.assert_called_once()

    def test_return_page_must_be_confirmed(self):
        with self.assertRaises(GameStuckError):
            self.task.boss_wait_return()

    def test_boss_ignores_legacy_count_cap_but_keeps_total_time_cap(self):
        class TimeOut(Exception): pass
        ns=dict(datetime=datetime,logger=FakeLogger(),LimitTimeOut=TimeOut,
                LimitCountOut=RuntimeError)
        method=extract_methods(SCRIPT,'StateMachine',['put_status'],ns)['put_status']
        task=NS(climb_type='boss',count_map={'boss':100},limit_time=timedelta(hours=6),
                start_time=datetime.now(),conf=NS(general_climb=NS(boss_limit=20)))
        method(task)
        task.start_time=datetime.now()-timedelta(hours=7)
        with self.assertRaises(TimeOut): method(task)

    def test_boss_only_sequence_and_timeout_are_scheduled_correctly(self):
        class TaskEnd(Exception): pass
        class TimeOut(Exception): pass
        ns=dict(logger=FakeLogger(), RequestHumanTakeover=RequestHumanTakeover,
                LimitCountOut=RuntimeError, LimitTimeOut=TimeOut, TaskEnd=TaskEnd,
                game=NS(page_main='courtyard'))
        method=extract_methods(SCRIPT,'ScriptTask',['run'],ns)['run']
        for timed_out in (False,True):
            task=NS(conf=NS(general_climb=NS(limit_time_v=None,run_sequence_v=['boss'],
                                            active_souls_clean=False)),
                    _run_boss=Mock(side_effect=TimeOut() if timed_out else None),
                    _run_pass=Mock(),goto_page=Mock(),set_next_run=Mock())
            with self.assertRaises(TaskEnd): method(task)
            task._run_boss.assert_called_once()
            task._run_pass.assert_not_called()
            task.set_next_run.assert_called_once_with(task='ActivityShikigami',success=not timed_out)

    def test_boss_loop_enters_once_and_stays_in_boss_between_battles(self):
        ns=dict(logger=FakeLogger(),game=NS(page_climb_boss='boss_page'))
        method=extract_methods(SCRIPT,'ScriptTask',['_run_boss'],ns)['_run_boss']
        task=NS(battle_state_reset=Mock(),goto_page=Mock(),boss_read_bonus=Mock(return_value=65),
                boss_load_progress=Mock(),
                boss_fixed_limit=lambda:None, boss_record_progress=Mock(),
                boss_budget_exhausted=lambda:getattr(task,'_boss_completed',None)==100,
                switch_soul=Mock(),I_EVENT_BOSS_RECORDS='records',I_EVENT_BOSS_PAGE='boss',
                boss_battle_once=Mock(side_effect=[True,True,False]))
        method(task)
        task.goto_page.assert_called_once_with('boss_page')
        self.assertEqual(task.boss_battle_once.call_count,3)
        task.boss_read_bonus.return_value=100
        task.boss_battle_once.reset_mock()
        task.switch_soul.reset_mock()
        method(task)
        task.boss_battle_once.assert_not_called()
        task.switch_soul.assert_not_called()

    def test_real_ocr_reads_bonus_from_below_challenge(self):
        from ppocronnx.rec import TextRecognizer
        roi=self.task.O_EVENT_BOSS_BONUS.roi
        x,y,w,h=roi
        recognizer=TextRecognizer(ort_providers=['CPUExecutionProvider'])
        for name,expected in [('boss_page',65),('boss_page_66',66),('boss_page_71',71)]:
            frame=self.image(name)
            for asset in (self.task.O_EVENT_BOSS_BONUS,self.task.O_EVENT_BOSS_BONUS_BACKUP):
                x,y,w,h=asset.roi
                results,_=recognizer([frame[y:y+h,x:x+w]])
                text,score=results[0]
                self.assertGreaterEqual(score,0.6)
                self.assertEqual(self.ns['parse_boss_bonus'](text),expected,(name,text,score))

    def test_slash_misread_uses_backup_without_repairing_digits(self):
        self.frames(['717100'],['(71/100)'])
        self.assertEqual(self.task.boss_read_bonus(expected_min=70),71)

    def test_primary_works_when_backup_is_unreadable(self):
        self.frames(['71/100'])
        self.assertEqual(self.task.boss_read_bonus(),71)

    def test_disagreeing_locations_retry_instead_of_using_prediction(self):
        self.frames(['71/100'],['(72/100)'])
        self.assertIsNone(self.task.boss_read_bonus(expected_min=70))

    def test_disagreement_then_agreement_can_recover(self):
        self.frames(['71/100']*3,['(72/100)','(71/100)','(71/100)'])
        self.assertEqual(self.task.boss_read_bonus(),71)
        self.assertEqual(self.task.O_EVENT_BOSS_BONUS.ocr_single.call_count,3)

    def test_progress_can_jump_after_battles_with_unreadable_counters(self):
        self.frames(['100/100'],['(100/100)'])
        self.assertEqual(self.task.boss_read_bonus(expected_min=70),100)

    def test_two_locations_confirm_final_100(self):
        self.frames(['100/100'],['(100/100)'])
        self.assertEqual(self.task.boss_read_bonus(expected_min=99),100)
        self.assertEqual(self.task.O_EVENT_BOSS_BONUS.ocr_single.call_count,2)

    def test_backup_alone_requires_three_fresh_terminal_frames(self):
        self.frames([''],['(100/100)'])
        self.assertEqual(self.task.boss_read_bonus(expected_min=99),100)
        self.assertEqual(self.task.O_EVENT_BOSS_BONUS_BACKUP.ocr_single.call_count,3)

    def test_local_checkpoint_cannot_complete_when_both_counters_are_unknown(self):
        self.task._boss_local_progress=dict(confirmed=99,expected_after_settlement=100)
        self.frames([''])
        self.assertIsNone(self.task.boss_read_bonus(expected_min=99))
        self.assertEqual(self.task._boss_local_progress['confirmed'],99)

    def test_victory_records_pending_progress_but_not_a_guessed_completion(self):
        self.prepare_battle(before=70)
        self.task.boss_read_bonus.side_effect=[70,None]
        self.assertTrue(self.task.boss_battle_once())
        self.assertEqual(self.task._boss_local_progress,
                         dict(confirmed=70,expected_after_settlement=None,counted=71))
        self.assertEqual(self.task.count_map['boss'],1)
        self.assertEqual(self.task._boss_completed,70)

    def test_restart_reconciles_checkpoint_with_fresh_game_counter(self):
        import tempfile
        from tasks.ActivityShikigami.boss_progress import BossProgressStore
        with tempfile.TemporaryDirectory() as directory:
            store=BossProgressStore(directory,'01-自己')
            store.save(70,71)
            self.task.config=NS(config_name='01-自己')
            self.task._boss_progress_store=store
            self.task.boss_load_progress()
            self.assertEqual(self.task._boss_local_progress['confirmed'],70)
            self.frames(['717100'],['(71/100)'])
            self.assertEqual(self.task.boss_read_bonus(),71)
            self.assertEqual(store.load()['confirmed'],71)
            self.assertIsNone(store.load()['expected_after_settlement'])

    def test_blank_frame_breaks_terminal_confirmation_streak(self):
        self.frames(['100/100','100/100','','100/100','100/100','100/100'])
        self.assertIsNone(self.task.boss_read_bonus())
        self.assertEqual(self.task.boss_read_bonus(),100)

    def test_unknown_digits_before_and_after_victory_still_allow_next_battle(self):
        self.prepare_battle(before=None,after=None)
        self.assertTrue(self.task.boss_battle_once())
        self.task.click.assert_called_once_with(self.task.I_EVENT_BOSS_CHALLENGE)
        self.assertEqual(self.task.count_map['boss'],1)
        self.assertFalse(hasattr(self.task,'_boss_completed'))
        self.assertEqual(self.task._boss_local_progress,
                         dict(confirmed=None,expected_after_settlement=None,counted=1))

    def test_multiple_unknown_battles_then_fresh_counter_resumes_progress(self):
        self.prepare_battle()
        self.task.boss_read_bonus.side_effect=[70,None,None,None,None,73,73,74]
        for _ in range(4):
            self.assertTrue(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,4)
        self.assertEqual(self.task.count_map['boss'],4)
        self.assertEqual(self.task._boss_completed,74)
        self.assertEqual(self.task._boss_local_progress,
                         dict(confirmed=74,expected_after_settlement=None,counted=74))

    def test_account24_disagreement_does_not_block_challenge(self):
        self.prepare_battle()
        del self.task.boss_read_bonus
        self.frames(['19/100'],['(9/100）'])
        self.assertTrue(self.task.boss_battle_once())
        self.task.click.assert_called_once()
        self.assertEqual(self.task.count_map['boss'],1)

    def test_unknown_initial_counter_does_not_skip_boss_loop(self):
        ns=dict(logger=FakeLogger(),game=NS(page_climb_boss='boss_page'))
        method=extract_methods(SCRIPT,'ScriptTask',['_run_boss'],ns)['_run_boss']
        task=NS(battle_state_reset=Mock(),goto_page=Mock(),boss_read_bonus=Mock(return_value=None),
                boss_load_progress=Mock(),switch_soul=Mock(),
                boss_fixed_limit=lambda:None,boss_budget_exhausted=lambda:False,
                boss_record_progress=Mock(),
                I_EVENT_BOSS_RECORDS='records',I_EVENT_BOSS_PAGE='boss',
                boss_battle_once=Mock(side_effect=[True,False]))
        method(task)
        task.switch_soul.assert_called_once()
        self.assertEqual(task.boss_battle_once.call_count,2)

    def test_confirmed_24_allows_exactly_76_more_wins_without_any_more_ocr(self):
        self.prepare_battle()
        self.task.boss_read_bonus.side_effect=lambda **kw:None
        self.task._boss_completed=24
        self.task.boss_record_progress(24)
        for index in range(76):
            self.assertEqual(self.task.boss_battle_once(),index<75)
        self.assertEqual(self.task.count_map['boss'],76)
        self.assertEqual(self.task._boss_counted,100)
        self.assertFalse(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,76)

    def test_confirmed_90_never_starts_eleventh_battle(self):
        self.prepare_battle()
        self.task.boss_read_bonus.side_effect=lambda **kw:None
        self.task._boss_completed=90
        self.task.boss_record_progress(90)
        for _ in range(10):
            self.task.boss_battle_once()
        self.assertFalse(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,10)
        self.assertEqual(self.task._boss_completed,90)
        self.assertEqual(self.task._boss_counted,100)

    def test_stale_but_readable_counter_never_extends_remaining_budget(self):
        self.prepare_battle()
        self.task.boss_read_bonus.side_effect=lambda **kw:90
        for _ in range(10):
            self.task.boss_battle_once()
        self.assertFalse(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,10)

    def test_no_readable_counter_still_never_starts_battle_101(self):
        self.prepare_battle()
        self.task.boss_read_bonus.side_effect=lambda **kw:None
        for index in range(100):
            self.assertEqual(self.task.boss_battle_once(),index<99)
        self.assertFalse(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,100)

    def test_fixed_mode_ignores_ocr_and_automatic_checkpoint_entirely(self):
        self.prepare_battle()
        self.task.conf=NS(general_climb=NS(boss_count_mode='boss_fixed_count',boss_limit=3))
        self.task._boss_counted=100
        self.task._boss_completed=100
        self.task.boss_read_bonus.side_effect=AssertionError('Fixed mode must never read reward digits')
        for index in range(3):
            self.assertEqual(self.task.boss_battle_once(),index<2)
        self.assertFalse(self.task.boss_battle_once())
        self.task.boss_read_bonus.assert_not_called()
        self.assertEqual(self.task.click.call_count,3)
        self.assertEqual(self.task.count_map['boss'],3)

    def test_fixed_zero_skips_all_battles_and_ocr(self):
        self.prepare_battle()
        self.task.conf=NS(general_climb=NS(boss_count_mode='boss_fixed_count',boss_limit=0))
        self.assertFalse(self.task.boss_battle_once())
        self.task.click.assert_not_called()
        self.task.boss_read_bonus.assert_not_called()

    def test_fixed_mode_can_exceed_automatic_100_cap_when_user_specifies_it(self):
        self.prepare_battle()
        self.task.conf=NS(general_climb=NS(boss_count_mode='boss_fixed_count',boss_limit=102))
        self.task.count_map['boss']=100
        self.assertTrue(self.task.boss_battle_once())
        self.assertFalse(self.task.boss_battle_once())
        self.assertFalse(self.task.boss_battle_once())
        self.assertEqual(self.task.click.call_count,2)
        self.task.boss_read_bonus.assert_not_called()

    def test_fixed_entry_never_loads_or_reads_automatic_progress(self):
        self.task.conf=NS(general_climb=NS(boss_count_mode='boss_fixed_count',boss_limit=5))
        self.task.battle_state_reset=Mock()
        self.task.goto_page=Mock()
        self.task.boss_load_progress=Mock()
        self.task.boss_read_bonus=Mock()
        self.task.switch_soul=Mock()
        self.task.boss_battle_once=Mock(side_effect=[True,False])
        method=extract_methods(SCRIPT,'ScriptTask',['_run_boss'],
                               dict(logger=FakeLogger(),game=NS(page_climb_boss='boss_page')))['_run_boss']
        method(self.task)
        self.task.boss_read_bonus.assert_not_called()
        self.task.boss_load_progress.assert_not_called()
        self.assertEqual(self.task.boss_battle_once.call_count,2)

    def test_mode_and_fixed_count_are_exposed_in_configuration_schema(self):
        from module.config.config_model import ConfigModel
        from tasks.ActivityShikigami.config import ActivityShikigami,EventClimb
        task=ActivityShikigami(general_climb={'boss_count_mode':'boss_fixed_count','boss_limit':76})
        args=ConfigModel.script_task(NS(activity_shikigami=task),'ActivityShikigami')
        fields={field['name']:field for field in args['general_climb']}
        self.assertEqual(fields['boss_count_mode']['type'],'enum')
        self.assertEqual(fields['boss_count_mode']['value'],'boss_fixed_count')
        self.assertEqual(fields['boss_count_mode']['enumEnum'],['boss_auto_progress','boss_fixed_count'])
        self.assertEqual(fields['boss_limit']['value'],76)
        self.assertEqual(EventClimb().boss_count_mode,'boss_auto_progress')


if __name__=='__main__': unittest.main()
