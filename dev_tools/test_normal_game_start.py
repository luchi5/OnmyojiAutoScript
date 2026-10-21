"""Normal startup stays independent of automatic error recovery."""
from types import SimpleNamespace
import unittest
from test_account_session_guard import fake_script, fake_device, isolated_class, ROOT, FakeLogger, GameStuckError, GameTooManyClickError, RequestHumanTakeover

class NormalGameStartTests(unittest.TestCase):
    def script(self, running=False, fail_login=False):
        script=fake_script(fake_device())
        script.device.app_is_running=lambda:running
        script.device.app_start=lambda:script.actions.append('app_start')
        def login(handler):
            self.assertFalse(handler._allow_login_restart)
            script.actions.append('login')
            if fail_login: raise RequestHumanTakeover('login failed')
        class Handler:
            def __init__(self, **kwargs): pass
            app_handle_login=login
        original=script._start_game_normally.__globals__['load_module']
        script._start_game_normally.__globals__['load_module']=lambda name,path: SimpleNamespace(LoginHandler=Handler) if name=='login' else original(name,path)
        return script
    def test_closed_app_starts_once_with_restart_disabled(self):
        s=self.script();self.assertTrue(s.run('GameStart'))
        self.assertEqual(s.actions,['app_start','login'])
        self.assertFalse(s.config.model.restart.scheduler.enable)
    def test_running_app_is_never_stopped_or_started(self):
        s=self.script(running=True);self.assertTrue(s.run('GameStart'));self.assertEqual(s.actions,[])
    def test_first_normal_task_starts_game_before_work(self):
        s=self.script();self.assertTrue(s.run('KekkaiUtilize'))
        self.assertEqual(s.actions,['app_start','login'])
        self.assertTrue(s.run('KekkaiUtilize'))
        self.assertEqual(s.actions,['app_start','login'])
    def test_failed_start_does_not_queue_restart(self):
        s=self.script(fail_login=True)
        with self.assertRaises(SystemExit):s.run('GameStart')
        self.assertEqual(s.actions.count('app_start'),1)
        self.assertNotIn('Restart',s.actions)
    def test_login_failure_does_not_stop_or_relaunch_app(self):
        cls=isolated_class(ROOT/'tasks/Restart/login.py','LoginHandler',{'app_handle_login'},
            {'logger':FakeLogger(),'GameStuckError':GameStuckError,'GameTooManyClickError':GameTooManyClickError,'RequestHumanTakeover':RequestHumanTakeover})
        h=cls();h._allow_login_restart=False;actions=[]
        h.device=SimpleNamespace(stuck_record_clear=lambda:None,click_record_clear=lambda:None,app_stop=lambda:actions.append('stop'),app_start=lambda:actions.append('start'))
        def fail():raise GameStuckError('login stalled')
        h._app_handle_login=fail
        with self.assertRaises(RequestHumanTakeover):h.app_handle_login()
        self.assertEqual(actions,[])
if __name__=='__main__':unittest.main()
