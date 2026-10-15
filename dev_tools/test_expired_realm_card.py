"""Expired-card recovery without starting a device or OCR model."""
from datetime import timedelta, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer
ROOT=Path(__file__).resolve().parents[1]

def harness(values):
    clock=FakeClock(1)
    h=SimpleNamespace(device=SimpleNamespace(image=None), O_CARD_ALL_TIME=SimpleNamespace(ocr_duration=Mock(side_effect=values)))
    h.screenshot=Mock(side_effect=clock.advance)
    h.realm_card_marker=Mock(return_value=('unknown',None))
    h.harvest_card=Mock(return_value=True)
    ns={'timedelta':timedelta,'datetime':datetime,'time':SimpleNamespace(sleep=lambda _:None),
        'logger':Mock(),'GameStuckError':RuntimeError,'Timer':lambda t:FakeTimer(clock,t),'ActivationConfig':object}
    for name,method in extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py','ScriptTask',
          ('ocr_time','collect_expired_card','run_activation'),ns).items():
        setattr(h,name,method.__get__(h))
    return h

class ExpiredTests(unittest.TestCase):
    def test_run_delegates_collection_decision_to_activation(self):
        methods = extract_methods(ROOT/'tasks/KekkaiActivation/script_task.py', 'ScriptTask',
                                  ('run',), {'page_guild': 'guild', 'page_main': 'main',
                                            'TaskEnd': RuntimeError})
        h = SimpleNamespace(config=SimpleNamespace(kekkai_activation=SimpleNamespace(
            activation_config=SimpleNamespace(exchange_before=False, exchange_max=False))))
        h.goto_page=Mock(); h.goto_realm=Mock(); h.harvest_card=Mock()
        h.run_activation=Mock(); h.screenshot=Mock(); h.appear=Mock(return_value=True)
        h.I_REALM_SHIN='realm'
        with self.assertRaises(RuntimeError): methods['run'](h)
        h.harvest_card.assert_not_called()
        h.run_activation.assert_called_once()

    def test_active_card_only_schedules_next_run(self):
        h=harness([timedelta(hours=2)])
        h.realm_card_marker.return_value=('active',None)
        h.goto_cards=Mock(); h.check_card_status=lambda:True; h.check_card_effect=lambda:True
        h.collect_expired_card=Mock(); h.set_next_run=Mock()
        self.assertFalse(h.run_activation(SimpleNamespace(card_type='不筛选（第一张）')))
        h.collect_expired_card.assert_not_called()
        h.harvest_card.assert_not_called()
        h.goto_cards.assert_called_once()
        h.set_next_run.assert_called_once()

    def test_no_bubble_falls_back_to_panel_timer(self):
        h=harness([timedelta(hours=2)])
        h.goto_cards=Mock(); h.check_card_status=lambda:True; h.check_card_effect=lambda:True
        h.collect_expired_card=Mock(); h.set_next_run=Mock()
        self.assertFalse(h.run_activation(SimpleNamespace(card_type='不筛选（第一张）')))
        h.harvest_card.assert_not_called()
        h.goto_cards.assert_called_once()
        h.O_CARD_ALL_TIME.ocr_duration.assert_called_once()

    def test_expired_bubble_collects_before_panel_and_new_card_selection(self):
        h=harness([timedelta(hours=24)])
        h.realm_card_marker.return_value=('expired',object())
        events=[]
        h.harvest_card=lambda:events.append('collect')
        h.goto_cards=lambda:events.append('panel')
        h.check_card_status=Mock(side_effect=[False,True])
        h.check_card_effect=lambda:False
        h.I_A_ACTIVATE_YELLOW='activate';h.I_A_DEMOUNT='unload';h.I_A_INVITE='invite'
        h.appear=lambda marker,**kw:marker=='invite'
        h.screening_card=lambda _:events.append('select')
        h.set_next_run=lambda *args,**kw:events.append('schedule')
        self.assertTrue(h.run_activation(SimpleNamespace(card_type='不筛选（第一张）')))
        self.assertEqual(events,['collect','panel','select','schedule'])
        # The only timer read is for scheduling the newly activated card.
        h.O_CARD_ALL_TIME.ocr_duration.assert_called_once()

    def test_fresh_chest_overrides_zero_ocr_and_schedules_short_retry(self):
        h=harness([timedelta(0)]*3)
        h.goto_cards=Mock();h.check_card_status=lambda:True;h.check_card_effect=lambda:True
        h.collect_expired_card=Mock(return_value=False);h.set_next_run=Mock()
        before=datetime.now()
        self.assertFalse(h.run_activation(SimpleNamespace(card_type='不筛选（第一张）')))
        target=h.set_next_run.call_args.kwargs['target']
        self.assertGreaterEqual(target,before+timedelta(minutes=5))
        self.assertLessEqual(target,datetime.now()+timedelta(minutes=5))

    def test_transient_zero_does_not_collect(self):
        h=harness([timedelta(0),timedelta(hours=2)])
        self.assertEqual(h.ocr_time(),timedelta(hours=2))

    def test_expiration_requires_three_readings(self):
        h=harness([timedelta(0)]*3)
        self.assertEqual(h.ocr_time(),timedelta(0))
        self.assertEqual(h.screenshot.call_count,2)

    def test_failed_ocr_is_not_expiration(self):
        h=harness([None,timedelta(0),None])
        with self.assertRaises(RuntimeError): h.ocr_time()

    def test_recovery_returns_to_realm_before_harvest(self):
        h=harness([]);h.I_REALM_SHIN='realm';h.I_SHI_GROWN='grown';h.I_UI_BACK_RED='back'
        h.I_A_CHECK_CARD='panel';h.I_A_AUTO_INVITE='invite'
        # Realm markers can remain visible behind the panel: close it first.
        h.appear=lambda marker:(marker=='realm' or (marker=='panel' and h.screenshot.call_count==1))
        h.appear_then_click=Mock()
        actions=[];h.harvest_card=lambda:actions.append('harvest');h.goto_cards=lambda:actions.append('cards')
        h.collect_expired_card()
        h.appear_then_click.assert_called_once_with('back',interval=1)
        self.assertEqual(actions,['harvest','cards'])

    def test_failed_collection_is_bounded_and_not_scheduled(self):
        h=harness([timedelta(0)]*9)
        h.goto_cards=Mock();h.check_card_status=lambda:True;h.check_card_effect=lambda:True
        h.collect_expired_card=Mock();h.set_next_run=Mock()
        with self.assertRaisesRegex(RuntimeError,'two collection attempts'):
            h.run_activation(SimpleNamespace(card_type='不筛选（第一张）'))
        self.assertEqual(h.collect_expired_card.call_count,2)
        h.set_next_run.assert_not_called()

if __name__=='__main__': unittest.main()
