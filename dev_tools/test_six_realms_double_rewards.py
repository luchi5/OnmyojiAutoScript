"""Reward quota guards before starting or resuming Moon Sea."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime,timedelta,time as dt_time
import unittest
from dev_tools.test_bondling_battle_entry import extract_methods
ROOT=Path(__file__).resolve().parents[1]

def harness(readings=(),enabled=True,resume=True):
    h=SimpleNamespace(device=SimpleNamespace(image=None),_conf=SimpleNamespace(double_rewards_only=enabled,limit_count=1,limit_time=dt_time(1)),start_time=datetime.now(),
        I_MSTART='start',I_MCONINUE='resume',I_MENTER='enter')
    h.screenshot=Mock();h.appear=lambda name: name==('resume' if resume else 'start')
    h.appear_then_click=Mock(return_value=False);h._continue=Mock()
    ocr=SimpleNamespace(ocr=Mock(side_effect=readings))
    ns={'RuleOcr':lambda **kw:ocr,'logger':Mock(),'time':SimpleNamespace(sleep=lambda _:None),
        'datetime':datetime,'timedelta':timedelta,'GameStuckError':RuntimeError}
    for n,m in extract_methods(ROOT/'tasks/SixRealms/moon_sea/moon_sea.py','MoonSea',
            ('read_double_reward_count','_start','_run_moon_sea'),ns).items():setattr(h,n,m.__get__(h))
    return h

class QuotaTests(unittest.TestCase):
    def test_zero_preserves_continue(self):
        h=harness(['0','0']);self.assertFalse(h._start());h._continue.assert_not_called();h.appear_then_click.assert_not_called()
    def test_zero_does_not_start_new_run(self):
        h=harness(['0','0'],resume=False);self.assertFalse(h._start());h.appear_then_click.assert_not_called()
    def test_positive_allows_continue(self):
        h=harness(['2','2']);self.assertTrue(h._start());h._continue.assert_called_once()
    def test_disabled_does_not_read_quota(self):
        h=harness(enabled=False);self.assertTrue(h._start())
    def test_blank_is_not_zero(self):
        h=harness(['']*4)
        with self.assertRaises(RuntimeError):h._start()
        h._continue.assert_not_called()
    def test_unstable_count_does_not_start(self):
        h=harness(['0','1','0','1'])
        with self.assertRaises(RuntimeError):h._start()
    def test_quota_mode_ignores_count_limit(self):
        h=harness();h.one=Mock(side_effect=[None,None,False]);h._run_moon_sea()
        self.assertEqual(h.one.call_count,3)
    def test_normal_mode_keeps_count_limit(self):
        h=harness(enabled=False);h.one=Mock();h._run_moon_sea();self.assertEqual(h.one.call_count,1)
    def test_time_limit_still_applies(self):
        h=harness();h.start_time=datetime.now()-timedelta(hours=2);h.one=Mock();h._run_moon_sea();h.one.assert_not_called()

if __name__=='__main__':unittest.main()
