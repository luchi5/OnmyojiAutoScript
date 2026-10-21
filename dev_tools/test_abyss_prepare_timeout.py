import ast, unittest
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
src=ast.parse(Path('tasks/AbyssShadows/script_task.py').read_text(encoding='utf-8-sig'))
cls=next(n for n in src.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask')
fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='wait_target_prepare')
class Check(unittest.TestCase):
 def run_case(self,ticks,prepare,dialog=False):
  env={'time':SimpleNamespace(monotonic=Mock(side_effect=ticks)),'logger':Mock()}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'target','exec'),env)
  s=Mock(); s.I_ABYSS_FINISHED_XY='finish';s.I_PREPARE_HIGHLIGHT='prepare';s.I_ABYSS_MAP='map';s.I_ABYSS_NAVIGATION='nav';s.I_ENSURE_BUTTON='confirm';s.I_REWARD_EXHAUSTED='quota';s.I_ABYSS_FIRE='fire'
  p=iter(prepare)
  s.appear.side_effect=lambda x: next(p) if x=='prepare' else (dialog if x=='quota' else False)
  s.appear_then_click.return_value=True
  return env['wait_target_prepare'](s),s
 def test_timeout(self):
  result,s=self.run_case([0,29,30],[False,False]);self.assertIsNone(result);s.recover_target_map.assert_called_once()
 def test_prepare(self):
  result,s=self.run_case([0,1],[False,True]);self.assertTrue(result);s.appear_then_click.assert_any_call('fire',interval=2)
 def test_delayed_quota_dialog(self):
  result,s=self.run_case([0,1],[False,False],True)
  self.assertEqual(result,'reward_exhausted')
  self.assertFalse(any(c.args[0]=='confirm' for c in s.appear_then_click.call_args_list))
 def test_type_isolation_and_restart(self):
  import tempfile
  from datetime import date
  from tasks.AbyssShadows.workflow import TargetPlan, ProgressStore, ALL_TARGETS, enemy_type
  for kind in ('BOSS','GENERAL','ELITE'):
   plan=TargetPlan(ALL_TARGETS);plan.mark_reward_exhausted(kind)
   self.assertNotEqual(enemy_type(plan.next_target()),kind)
   self.assertEqual(sum(plan.counts.values()),0)
   with tempfile.TemporaryDirectory() as root:
    store=ProgressStore(root,'test',date.today());store.save(plan)
    done,unavailable=store.load()
    self.assertEqual(store.reward_exhausted,{kind});self.assertEqual(done,[])
if __name__ == '__main__': unittest.main()
