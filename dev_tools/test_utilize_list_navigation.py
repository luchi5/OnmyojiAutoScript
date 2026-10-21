import ast,unittest,cv2
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from enum import Enum
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
src=ast.parse(Path('tasks/KekkaiUtilize/script_task.py').read_text(encoding='utf-8'))
cls=next(n for n in src.body if isinstance(n,ast.ClassDef) and n.name=='ScriptTask')
E=Enum('Friend','SAME_SERVER DIFFERENT_SERVER')
class Checks(unittest.TestCase):
 def harness(self,frames):
  clock=[0];ns={'time':SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n)),'logger':Mock(),'SelectFriendList':E,'UtilizeNavigationError':RuntimeError}
  nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('in_utilize_list','grown_goto_utilize','switch_friend_list')]
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'test','exec'),ns)
  h=SimpleNamespace();h.I_UTILIZE_FRIEND_GROUP=SimpleNamespace(name='same',coord=lambda:(260,120));h.I_UTILIZE_ZONES_GROUP=SimpleNamespace(name='other',coord=lambda:(390,120));h.I_UTILIZE_ADD=SimpleNamespace(name='add')
  cur=[set()];idx=[0]
  def shot():cur[0]=frames[min(idx[0],len(frames)-1)];idx[0]+=1
  h.screenshot=shot;h.appear=lambda r:r.name in cur[0];h.in_shikigami_growth=lambda:'growth' in cur[0]
  h.appear_then_click=Mock(return_value=True);h.device=SimpleNamespace(click=Mock())
  for n in nodes:setattr(h,n.name,ns[n.name].__get__(h))
  return h
 def test_wrong_page_no_click(self):
  h=self.harness([set()])
  with self.assertRaises(RuntimeError):h.switch_friend_list(E.DIFFERENT_SERVER)
  h.device.click.assert_not_called()
 def test_tab_max_three_clicks(self):
  h=self.harness([{'same'}])
  with self.assertRaises(RuntimeError):h.switch_friend_list(E.DIFFERENT_SERVER)
  self.assertEqual(h.device.click.call_count,3)
 def test_tab_transition_verified(self):
  h=self.harness([{'same'},{'other'},{'other'}]);self.assertTrue(h.switch_friend_list(E.DIFFERENT_SERVER));self.assertEqual(h.device.click.call_count,1)
 def test_entry_wrong_page_no_click(self):
  h=self.harness([set()]);self.assertFalse(h.grown_goto_utilize());h.appear_then_click.assert_not_called()
 def test_entry_three_clicks(self):
  h=self.harness([{'growth'}]);self.assertFalse(h.grown_goto_utilize());self.assertEqual(h.appear_then_click.call_count,3)
 def test_entry_stable_list(self):
  h=self.harness([{'growth'},{'same'},{'same'}]);self.assertTrue(h.grown_goto_utilize())
 def test_error_screenshot_is_not_list(self):
  im=cv2.cvtColor(cv2.imread('dev_tools/fixtures/utilize_wrong_page.png'),cv2.COLOR_BGR2RGB)
  self.assertFalse(KekkaiUtilizeAssets.I_UTILIZE_FRIEND_GROUP.match(im));self.assertFalse(KekkaiUtilizeAssets.I_UTILIZE_ZONES_GROUP.match(im))
if __name__=='__main__':unittest.main()
