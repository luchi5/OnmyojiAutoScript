"""Exercise share timeouts and continuation without sending a real share."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer

ROOT=Path(__file__).resolve().parents[1]


def harness():
    clock=FakeClock(1)
    ns={'Timer':lambda t:FakeTimer(clock,t),'sleep':lambda _:None,
        'logger':Mock(),'TaskEnd':RuntimeError,'page_collection':'collection'}
    h=SimpleNamespace()
    methods=extract_methods(ROOT/'tasks/WeeklyTrifles/script_task.py','ScriptTask',
        ('_open_share_panel','_share_collect','_leave_collect_share','run'),ns)
    for n,m in methods.items():setattr(h,n,m.__get__(h))
    h.screenshot=Mock(side_effect=clock.advance)
    h.appear=Mock(return_value=False);h.appear_then_click=Mock(return_value=True)
    for key in ('I_WT_QR_CODE','I_WT_LOGO','I_WT_COLLECT_WECHAT','I_WT_SHIKIAGMI',
                'I_WT_SHARE','I_UI_BACK_RED','I_UI_BACK_YELLOW'):
        setattr(h,key,key)
    h.goto_page=Mock()
    return h


class ShareTests(unittest.TestCase):
    def test_no_transition_clicks_at_most_three_times(self):
        h=harness()
        self.assertFalse(h._open_share_panel('wechat'))
        self.assertEqual(h.appear_then_click.call_count,3)

    def test_missing_button_is_bounded(self):
        h=harness();h.appear_then_click.return_value=False
        self.assertFalse(h._open_share_panel('wechat'))
        self.assertLess(h.screenshot.call_count,65)

    def test_last_attempt_gets_time_to_transition(self):
        h=harness();h.appear.side_effect=lambda _:h.screenshot.call_count>=6
        self.assertTrue(h._open_share_panel('wechat'))
        self.assertEqual(h.appear_then_click.call_count,3)

    def test_already_open_share_does_not_click_again(self):
        h=harness();h.appear.return_value=True
        self.assertTrue(h._open_share_panel('wechat'))
        h.appear_then_click.assert_not_called()

    def test_failed_collection_share_exits_and_continues_other_tasks(self):
        h=harness();h.appear.side_effect=lambda rule:rule==h.I_WT_LOGO
        h._open_share_panel=Mock(return_value=False);h._leave_collect_share=Mock()
        h.config=SimpleNamespace(weekly_trifles=SimpleNamespace(trifles=SimpleNamespace(
            share_collect=True,share_area_boss=True,share_secret=True,save_touch_fish=False,broken_amulet=0)))
        h._share_area_boss=Mock();h._share_secret=Mock();h.set_next_run=Mock()
        with self.assertRaises(RuntimeError):h.run()
        h._leave_collect_share.assert_called_once()
        h._share_area_boss.assert_called_once();h._share_secret.assert_called_once()

    def test_exit_checks_returned_page(self):
        h=harness();h.appear.side_effect=lambda _:h.screenshot.call_count>=2
        h._leave_collect_share()
        h.appear_then_click.assert_called_once_with(h.I_UI_BACK_RED,interval=1)


if __name__=='__main__':unittest.main()

class SecretShareTests(unittest.TestCase):
    def test_missing_secret_share_is_bounded_and_leaves(self):
        clock=FakeClock(1)
        h=SimpleNamespace()
        ns={'Timer':lambda t:FakeTimer(clock,t),'sleep':lambda _:None,
            'logger':Mock(),'page_secret_zones':'secret'}
        methods=extract_methods(ROOT/'tasks/WeeklyTrifles/script_task.py','ScriptTask',('_share_secret',),ns)
        h._share_secret=methods['_share_secret'].__get__(h)
        h.screenshot=Mock(side_effect=clock.advance);h.goto_page=Mock()
        h.appear=Mock(return_value=False);h.appear_then_click=Mock(return_value=False)
        h._leave_secret_share=Mock()
        for key in ('I_WT_SE_WECHAT','I_WT_ENTER_SE','I_WT_SE_SHARE','I_WT_SE_RANK'):
            setattr(h,key,key)
        self.assertFalse(h._share_secret())
        h._leave_secret_share.assert_called_once()
        self.assertLessEqual(h.screenshot.call_count,21)
