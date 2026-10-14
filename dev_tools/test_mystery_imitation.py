"""Mystery island regression: max level and reminder dialog ordering."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer
ROOT=Path(__file__).resolve().parents[1]

def harness(frames=()):
    clock=FakeClock(1)
    h=SimpleNamespace(cnt_skill101=5, visible=(), I_BACK_EXIT='back')
    frames=iter(frames)
    def screenshot():
        clock.advance()
        h.visible=next(frames, ())
    h.screenshot=screenshot
    h.appear=lambda rule: rule in h.visible
    h.in_main=lambda: 'main' in h.visible
    h.click=Mock(); h.appear_then_click=Mock()
    ns={'logger':Mock(),'Timer':lambda t:FakeTimer(clock,t), 'GameStuckError':RuntimeError,
        'RuleImage':lambda **kw:Path(kw['file']).stem}
    for name,method in extract_methods(ROOT/'tasks/SixRealms/moon_sea/l102.py', 'MoonSeaL102',
                                      ('imitate','leave_mystery_island'),ns).items():
        setattr(h,name,method.__get__(h))
    return h

class MysteryTests(unittest.TestCase):
    def test_max_level_skips_imitation(self):
        h=harness(); h.leave_mystery_island=Mock()
        h.imitate()
        h.leave_mystery_island.assert_called_once()
        h.appear_then_click.assert_not_called()
        self.assertEqual(h.cnt_skill101,5)

    def test_slow_imitation_does_not_open_a_second_dialog(self):
        h=harness([('skill','imitate'),('skill','imitate'),
                   ('skill','imitate'),('skill','imitate','confirm'),
                   ('skill','imitate','confirm'),('success','confirm'),('main',)])
        h.cnt_skill101=3
        h.I_IMITATE_1='skill';h.I_IMITATE='imitate';h.I_IMITATE_SUCCESS='success'
        h.I_UI_CONFIRM_SAMLL='confirm';h.I_UI_CONFIRM='confirm2'
        h.imitate()
        self.assertEqual([c.args[0] for c in h.click.call_args_list],['skill','imitate','confirm'])
        self.assertEqual(h.cnt_skill101,4)

    def test_no_result_does_not_resubmit_or_increment_level(self):
        h=harness([('skill',),('imitate',)]+[('imitate',)]*30)
        h.cnt_skill101=3
        h.I_IMITATE_1='skill';h.I_IMITATE='imitate';h.I_IMITATE_SUCCESS='success'
        h.I_UI_CONFIRM_SAMLL='confirm';h.I_UI_CONFIRM='confirm2'
        with self.assertRaises(RuntimeError):h.imitate()
        self.assertEqual([c.args[0] for c in h.click.call_args_list],['skill','imitate'])
        self.assertEqual(h.cnt_skill101,3)

    def test_reminder_is_checked_before_confirm(self):
        h=harness([('leave_title','leave_unchecked'),('leave_title',),('main',)])
        h.leave_mystery_island()
        h.click.assert_called_once_with('leave_unchecked',interval=1)
        h.appear_then_click.assert_called_once_with('leave_confirm',interval=1.5)

    def test_already_checked_is_not_toggled(self):
        h=harness([('leave_title',),('main',)])
        h.leave_mystery_island()
        h.click.assert_not_called()
        h.appear_then_click.assert_called_once_with('leave_confirm',interval=1.5)

    def test_missing_dialog_is_bounded(self):
        h=harness()
        with self.assertRaises(RuntimeError):h.leave_mystery_island()

if __name__=='__main__': unittest.main()
