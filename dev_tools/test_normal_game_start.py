"""Normal startup stays independent of automatic error recovery."""
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from dev_tools.test_account_session_guard import fake_script, fake_device, isolated_class, ROOT, FakeLogger, GameStuckError, GameTooManyClickError, RequestHumanTakeover

class StartupTestCase(unittest.TestCase):
    def setUp(self):
        # Startup-frame inference is patched by each test; only its imported
        # exception type is needed, never a live ADB client or installation.
        adb = ModuleType('adbutils')
        errors = ModuleType('adbutils.errors')
        errors.AdbError = type('AdbError', (Exception,), {})
        adb.errors = errors
        modules = patch.dict(sys.modules, {'adbutils': adb, 'adbutils.errors': errors})
        modules.start()
        self.addCleanup(modules.stop)
        # patch.dict restores sys.modules between tests; refresh the parent's
        # cached attribute as well so string patches target the imported module.
        parent = importlib.import_module('module.device')
        parent.startup_frame = importlib.import_module('module.device.startup_frame')


class NormalGameStartTests(StartupTestCase):
    def script(self, running=False, fail_login=False):
        script=fake_script(fake_device())
        script.device.app_is_running=lambda:running
        def start(*, normal_start=False):
            self.assertTrue(normal_start)
            script.actions.append('app_start')
        script.device.app_start=start
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
    def test_closed_app_uses_real_device_gate_with_handle_error_disabled(self):
        s=self.script()
        s.config.script.error.handle_error=False
        s.device=guarded_device(s.config,s.actions)
        with patch('module.device.startup_frame.ensure_startup_frame'):
            self.assertTrue(s.run('GameStart'))
        self.assertEqual(s.actions,['app_start','login'])
        self.assertFalse(s.config.script.error.handle_error)
        with self.assertRaises(RequestHumanTakeover):s.device.app_start()
        with self.assertRaises(RequestHumanTakeover):s.device.app_stop()
        self.assertEqual(s.actions,['app_start','login'])
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

def guarded_device(config=None, actions=None, running=False):
    actions=[] if actions is None else actions
    class Control:
        def app_start(self):actions.append('app_start')
        def app_stop(self):actions.append('app_stop')
    cls=isolated_class(ROOT/'module/device/device.py','Device',{'app_start','app_stop'},
        {'logger':FakeLogger(),'RequestHumanTakeover':RequestHumanTakeover},base=Control)
    device=cls()
    device.config=config or SimpleNamespace(script=SimpleNamespace(error=SimpleNamespace(handle_error=False)))
    device.app_is_running=lambda:running
    device.stuck_record_clear=Mock()
    device.click_record_clear=Mock()
    device.actions=actions
    return device


class DeviceStartupGateTests(StartupTestCase):
    def test_normal_start_keeps_recovery_and_stop_disabled(self):
        device=guarded_device()
        with patch('module.device.startup_frame.ensure_startup_frame') as ensure:
            device.app_start(normal_start=True)
        self.assertEqual(device.actions,['app_start'])
        ensure.assert_called_once()
        device.stuck_record_clear.assert_called_once()
        device.click_record_clear.assert_called_once()
        self.assertFalse(device.config.script.error.handle_error)
        with self.assertRaises(RequestHumanTakeover):device.app_start()
        with self.assertRaises(RequestHumanTakeover):device.app_stop()
        self.assertEqual(device.actions,['app_start'])

    def test_already_running_game_is_untouched_even_if_it_started_between_checks(self):
        device=guarded_device(running=True)
        with patch('module.device.startup_frame.ensure_startup_frame') as ensure:
            device.app_start(normal_start=True)
        self.assertEqual(device.actions,[])
        ensure.assert_not_called()

    def test_default_recovery_start_and_stop_still_require_handle_error(self):
        device=guarded_device()
        with patch('module.device.startup_frame.ensure_startup_frame') as ensure:
            with self.assertRaises(RequestHumanTakeover):device.app_start()
            with self.assertRaises(RequestHumanTakeover):device.app_stop()
        self.assertEqual(device.actions,[])
        ensure.assert_not_called()

    def test_enabled_recovery_preserves_start_stop_and_frame_check(self):
        device=guarded_device()
        device.config.script.error.handle_error=True
        with patch('module.device.startup_frame.ensure_startup_frame') as ensure:
            device.app_start()
            device.app_stop()
        self.assertEqual(device.actions,['app_start','app_stop'])
        ensure.assert_called_once()


if __name__=='__main__':unittest.main()
