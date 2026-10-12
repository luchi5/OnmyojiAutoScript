"""Quota parsing and actual resource-box loop without emulator or live OCR."""
from pathlib import Path
from types import SimpleNamespace as S
import unittest
from unittest.mock import patch

from tasks.KekkaiUtilize.exp_box_counter import parse_exp_box_counter
from dev_tools.test_bondling_battle_entry import FakeClock, FakeTimer, FakeLogger, extract_methods


ROOT = Path(__file__).resolve().parents[1]


class Harness:
    def __init__(self, counter, *, confirmation=False, stuck_confirm=False, stuck_close=False):
        self.counter = counter
        self.state = 'realm'
        self.clock = FakeClock(step=.3)
        self.clicks = []
        self.frames = 0
        self.confirmation = confirmation
        self.stuck_confirm = stuck_confirm
        self.stuck_close = stuck_close
        self.device = S(image=None)
        for name in ('I_REALM_SHIN', 'I_BOX_EXP', 'I_BOX_EXP_MAX', 'I_BOX_AP',
                     'I_EXP_EXTRACT', 'I_UI_CONFIRM', 'I_UI_CANCEL', 'I_UI_BACK_RED'):
            setattr(self, name, name)
        method = extract_methods(ROOT/'tasks/KekkaiUtilize/script_task.py', 'ScriptTask',
            ('check_box_ap_or_exp',), dict(Timer=lambda n: FakeTimer(self.clock,n),
                                         logger=FakeLogger(), GameStuckError=RuntimeError))['check_box_ap_or_exp']
        self.run = method.__get__(self)

    def screenshot(self):
        self.clock.advance()
        self.frames += 1
        if self.frames > 100:
            raise AssertionError('experience loop bypassed its timeout')

    def appear(self, marker, **kw):
        return ((marker in ('I_REALM_SHIN', 'I_BOX_EXP') and self.state == 'realm')
                or (marker == 'I_EXP_EXTRACT' and self.state == 'box')
                or (marker in ('I_UI_CONFIRM', 'I_UI_CANCEL') and self.state == 'confirm'))

    def appear_then_click(self, marker, **kw):
        if marker == 'I_UI_BACK_RED' and self.state != 'realm':
            self.clicks.append(marker)
            if not self.stuck_close:
                self.state = 'realm'
            return True
        if not self.appear(marker):
            return False
        self.clicks.append(marker)
        if marker == 'I_BOX_EXP':
            self.state = 'box'
        elif marker == 'I_EXP_EXTRACT':
            self.state = 'confirm' if self.confirmation else 'realm'
        elif marker in ('I_UI_CONFIRM', 'I_UI_CANCEL') and not self.stuck_confirm:
            self.state = 'realm'
        return True

    def execute(self, waste=True):
        with patch('tasks.KekkaiUtilize.exp_box_counter.read_exp_box_counter', return_value=self.counter):
            self.run(ap_enable=False, exp_enable=True, exp_waste=waste)


class CounterTests(unittest.TestCase):
    def test_new_bonus_counter_uses_sum(self):
        self.assertEqual(parse_exp_box_counter('今日已领取0/40000+8000'), (0,48000,48000))
        self.assertEqual(parse_exp_box_counter('今日已领取40000/40000+8000'), (40000,8000,48000))
        self.assertEqual(parse_exp_box_counter('48000/40000+8000'), (48000,0,48000))

    def test_old_counter_still_supported(self):
        self.assertEqual(parse_exp_box_counter('1200/40000'), (1200,38800,40000))

    def test_full_width_and_spaces(self):
        self.assertEqual(parse_exp_box_counter('今日已领取 ０ ／ ４００００ ＋ ８０００'), (0,48000,48000))

    def test_invalid_values_never_prove_zero_or_full(self):
        for value in ('', None, '400008000', '0/0', '50000/40000+8000', '0/40000+', '0/40000+foo'):
            with self.subTest(value=value):
                self.assertIsNone(parse_exp_box_counter(value))

    def test_unreadable_counter_times_out_closes_and_never_extracts(self):
        task = Harness(None)
        task.execute()
        self.assertNotIn('I_EXP_EXTRACT', task.clicks)
        self.assertIn('I_UI_BACK_RED', task.clicks)
        self.assertEqual(task.state, 'realm')
        self.assertLess(task.frames,50)

    def test_bonus_remaining_is_collectable(self):
        task = Harness((40000,8000,48000))
        task.execute()
        self.assertIn('I_EXP_EXTRACT', task.clicks)

    def test_full_daily_cap_closes_without_extracting(self):
        task = Harness((48000,0,48000))
        task.execute()
        self.assertNotIn('I_EXP_EXTRACT', task.clicks)
        self.assertEqual(task.state,'realm')

    def test_confirm_and_cancel_keep_waste_preference(self):
        for waste, button in ((True,'I_UI_CONFIRM'),(False,'I_UI_CANCEL')):
            with self.subTest(waste=waste):
                task = Harness((0,48000,48000),confirmation=True)
                task.execute(waste=waste)
                self.assertIn(button,task.clicks)
                self.assertEqual(task.state,'realm')

    def test_persistent_confirmation_also_obeys_timeout(self):
        task = Harness((0,48000,48000),confirmation=True,stuck_confirm=True)
        task.execute()
        self.assertEqual(task.state,'realm')
        self.assertLess(task.frames,60)

    def test_failed_close_reports_bounded_error(self):
        task = Harness(None,stuck_close=True)
        with self.assertRaisesRegex(RuntimeError,'Cannot close'):
            task.execute()
        self.assertLess(task.frames,80)


if __name__ == '__main__':
    unittest.main()
