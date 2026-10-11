"""Device-free behavior tests: extract definitions only, with no project imports."""
import argparse
import ast
from datetime import datetime, timedelta, time as daytime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
args = NS(source='candidate', ipc_source='candidate', repro_only=False, compat_only=False)
SCRIPT_SOURCE = ROOT / 'script.py'
IPC_SOURCE = ROOT / 'module/device/method/nemu_ipc.py'

class FakeLogger:
    def __init__(self): self.errors = []; self.messages = []
    def __getattr__(self, name):
        def log(*values, **kw):
            self.messages.append((name, values))
            if name in ('exception', 'error') and values: self.errors.append(values[0])
        return log

def extract(path, wanted, namespace):
    tree = ast.parse(path.read_bytes())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in wanted:
            names = wanted[node.name]
            if names is not None:
                node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in names]
                for method in node.body:
                    method.decorator_list = [d for d in method.decorator_list
                                             if isinstance(d, ast.Name) and d.id in ('staticmethod', 'classmethod')]
            selected.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), 'exec'), namespace)
    return namespace

def ipc_types(streams=None):
    streams = streams or NS(stdout=None, stderr=None)
    ns = {'sys': streams, 'os': os, 'logger': FakeLogger()}
    extract(IPC_SOURCE, {'CaptureStd': None, 'CaptureNemuIpc': None,
                        'NemuIpcError': None, 'NemuIpcIncompatible': None,
                        'NemuIpcImpl': {'connect', 'ev_run_sync'}}, ns)
    return ns

def fake_ipc(ns):
    impl = ns['NemuIpcImpl'].__new__(ns['NemuIpcImpl'])
    def nemu_connect(*values): return 0
    impl.lib = NS(nemu_connect=nemu_connect)
    impl._ev = NS(run_until_complete=lambda value: value)
    impl.ev_run_async = lambda *a, **kw: 0
    impl.connect_id = 0; impl.nemu_folder = 'offline'; impl.instance_id = 2
    return impl

ERROR_NAMES = ['AccountLoggedInElsewhere', 'TaskEnd', 'GameNotRunningError', 'GameStuckError',
               'GameTooManyClickError', 'GameBugError', 'GamePageUnknownError', 'ScriptError',
               'RequestHumanTakeover', 'EmulatorNotRunningError']

class WakeHarness:
    def __init__(self, *, running=True, close_during_wait=True, restart_enabled=True,
                 status=None, query_error=None, method='close_game'):
        self.actions = []; self.notifications = []; self.running = running
        self.close_during_wait = close_during_wait
        self.status = status if status is not None else {
            'index': '2', 'android_version': '12.0', 'error_code': 0,
            'is_process_started': False, 'is_android_started': False}
        self.query_error = query_error; self.query_count = 0
        self.now = datetime(2026, 10, 11, 3, 7)
        owner = self
        class Clock:
            @staticmethod
            def now(): return owner.now
        ns = {name: type(name, (Exception,), {}) for name in ERROR_NAMES}
        self.errors = ns.copy(); self.logger = FakeLogger()
        ns.update({'logger': self.logger, 'datetime': Clock, 'timedelta': timedelta,
                   'time': NS(sleep=self.sleep), 'Path': Path, 'json': json,
                   're': __import__('re'), 'IS_WINDOWS': True,
                   'Device': self.new_device, 'inflection': NS(underscore=lambda v: v),
                   'I18n': NS(trans_zh_cn=lambda v: v), 'exit': lambda code: sys.exit(code)})
        class Task:
            def __init__(inner, config, device): inner.device = device
            def run(inner):
                self.actions.append(('task', inner.device._account_session_recovery))
                raise ns['TaskEnd']()
        ns['load_module'] = lambda *a: NS(ScriptTask=Task)
        extract(SCRIPT_SOURCE, {'Script': {'wait_until', '_ensure_emulator_after_wait', 'run',
                '_stop_when_restart_disabled', '_wait_close_game', '_wait_stay_there', '_wait_goto_main',
                '_time_to_timedelta', '_handle_wait_during_idle'}}, ns)
        self.ns = ns
        self.script = ns['Script']()
        self.script.config_name = '03-offline'
        settings = NS(emulatorinfo_type='MuMuPlayer12', emulatorinfo_name='MuMuPlayer-12.0-2',
                      emulatorinfo_path='C:/offline/nx_main/MuMuNxMain.exe')
        model = NS(read_json=lambda name: {'restart': {'scheduler': {'enable': restart_enabled}}})
        self.script.config = NS(model=model, script=NS(device=settings, optimization=NS(
            when_task_queue_empty=method, close_game_wait_duration=daytime(minute=10),
            close_emulator_wait_duration=daytime(minute=30))), start_watching=lambda: None,
            should_reload=lambda: False, notifier=NS(push=lambda **kw: self.notifications.append(kw) or True),
            task_call=lambda name: self.actions.append(('schedule', name)))
        self.script.instance_guard = None; self.script._emulator_down = False
        self.script._try_acquire_queue_token = lambda: True
        self.script._check_account_session_before_recovery = lambda cmd: False
        self.script._record_task_result = lambda *a: None
        self.script.failure_record = {}
        self.script.exception_handler = lambda **kw: self.actions.append('diagnose')
        self.script.save_error_log = lambda: self.actions.append('save_error')
        self.script.device = self.device()

    def device(self):
        device = NS(_account_session_recovery=False, package='offline')
        def screenshot():
            self.actions.append('screenshot')
            if not self.running: fake_ipc(ipc_types()).connect()
        device.screenshot = screenshot
        device.app_stop = lambda: self.actions.append('app_stop')
        device.release_during_wait = lambda: self.actions.append('release')
        device.emulator_stop = lambda: self.actions.append('unexpected_stop')
        device.app_is_running = lambda: True
        device.sleep = lambda seconds: self.actions.append(('device_sleep', seconds))
        return device

    def new_device(self, config):
        self.actions.append('wake_device'); self.running = True
        return self.device()

    def sleep(self, seconds):
        if self.close_during_wait: self.running = False
        self.now += timedelta(minutes=20)

    def query(self, *a, **kw):
        self.query_count += 1
        self.actions.append('status_query')
        assert a[0] == ['C:\\offline\\nx_main\\MuMuManager.exe', 'info', '-v', '2'], a
        assert kw['timeout'] == 5 and kw['check'] is True
        if self.query_error: raise self.query_error
        payload = self.status if isinstance(self.status, bytes) else json.dumps(self.status).encode()
        return NS(stdout=payload)

    def run(self, command='Restart'):
        with patch.object(subprocess, 'run', self.query): return self.script.run(command)

    def idle(self, method='close_game'):
        self.script.config.script.optimization.when_task_queue_empty = method
        with patch.object(subprocess, 'run', self.query):
            return self.script._handle_wait_during_idle(self.now + timedelta(minutes=13))

class ReproductionTests(unittest.TestCase):
    def test_manual_close_after_wait_wakes_before_first_screenshot(self):
        h = WakeHarness()
        self.assertTrue(h.idle())
        self.assertEqual(h.actions.count('wake_device'), 1)
        self.assertLess(h.actions.index('wake_device'), h.actions.index('screenshot'))
        self.assertTrue(h.running)
        self.assertIn(('task', True), h.actions)
        self.assertFalse(h.script.device._account_session_recovery)

    def test_native_connect_failure_without_stdout_keeps_original_error(self):
        ns = ipc_types(); impl = fake_ipc(ns)
        with self.assertRaisesRegex(ns['NemuIpcError'], 'Connection failed'): impl.connect()
        self.assertEqual(impl.connect_id, 0)
        self.assertIsNone(ns['sys'].stdout)
        self.assertIsNone(ns['CaptureNemuIpc'].instance)

class WakeRegressions(unittest.TestCase):
    def test_running_and_booting_instances_keep_existing_device(self):
        for state in [(True, True), (True, False), (False, True)]:
            with self.subTest(state=state):
                status = {'index': '2', 'android_version': '12.0', 'error_code': 0,
                          'is_process_started': state[0], 'is_android_started': state[1]}
                h = WakeHarness(close_during_wait=False, status=status)
                old = h.script.device
                self.assertTrue(h.idle())
                self.assertIs(h.script.device, old)
                self.assertNotIn('wake_device', h.actions)
                self.assertNotIn('unexpected_stop', h.actions)

    def test_unknown_status_does_not_wake_or_restart_live_instance(self):
        cases = [b'broken', b'\xff', b'[]', {}, {'index': '8'},
                 {'index': '2', 'android_version': '15.0', 'error_code': 0,
                  'is_process_started': False, 'is_android_started': False}]
        base = WakeHarness().status
        for key, value in [('error_code', 1), ('error_code', False), ('error_code', '0'),
                           ('is_process_started', 0), ('is_android_started', 'false'), ('index', '8')]:
            cases.append(dict(base, **{key: value}))
        for status in cases:
            with self.subTest(status=status):
                h = WakeHarness(close_during_wait=False, status=status)
                self.assertTrue(h.idle())
                self.assertNotIn('wake_device', h.actions)
                self.assertEqual(h.query_count, 1)

    def test_status_query_failure_preserves_existing_handling(self):
        for error in [OSError('missing exe'), subprocess.TimeoutExpired('offline', 5),
                      subprocess.CalledProcessError(1, 'offline')]:
            with self.subTest(error=error):
                h = WakeHarness(close_during_wait=False, query_error=error)
                self.assertTrue(h.idle())
                self.assertNotIn('wake_device', h.actions)

    def test_stopped_instance_with_disabled_restart_stays_stopped(self):
        h = WakeHarness(running=False, close_during_wait=False, restart_enabled=False)
        h.script.wait_until(h.now - timedelta(seconds=2))
        with self.assertRaises(SystemExit): h.run('KekkaiUtilize')
        self.assertNotIn('wake_device', h.actions)
        self.assertNotIn('screenshot', h.actions)
        self.assertEqual(len(h.notifications), 1)

    def test_latest_unknown_restart_switch_cannot_authorize_wake(self):
        for latest in [{}, None, {'restart': {'scheduler': {'enable': 'true'}}}]:
            h = WakeHarness(running=False, close_during_wait=False)
            h.script.config.model.read_json = lambda name: latest
            h.script.wait_until(h.now - timedelta(seconds=2))
            with self.assertRaises(SystemExit): h.run('KekkaiUtilize')
            self.assertNotIn('wake_device', h.actions)

    def test_takeover_authorization_and_context_survive_device_replacement(self):
        h = WakeHarness(running=False, close_during_wait=False, restart_enabled=False)
        h.script._account_session_restart_authorized = True
        h.script.wait_until(h.now - timedelta(seconds=2))
        self.assertTrue(h.run())
        self.assertIn(('task', True), h.actions)
        self.assertFalse(h.script._account_session_restart_authorized)
        self.assertFalse(h.script._account_session_restart_active)
        self.assertFalse(h.script.device._account_session_recovery)

    def test_no_wait_does_not_add_status_probes(self):
        h = WakeHarness(close_during_wait=False)
        self.assertTrue(h.run('KekkaiUtilize'))
        self.assertEqual(h.query_count, 0)

    def test_unsupported_platform_and_emulator_leave_behavior_unchanged(self):
        for change in ['platform', 'type', 'name', 'path']:
            h = WakeHarness(close_during_wait=False)
            if change == 'platform': h.ns['IS_WINDOWS'] = False
            elif change == 'type': h.script.config.script.device.emulatorinfo_type = 'LDPlayer9'
            elif change == 'name': h.script.config.script.device.emulatorinfo_name = 'MuMuPlayer-15.0-2'
            else: h.script.config.script.device.emulatorinfo_path = 'C:/offline/MuMuPlayer.exe'
            self.assertTrue(h.idle())
            self.assertEqual(h.query_count, 0)

    def test_short_and_other_idle_strategies_check_before_next_task(self):
        for method in ['goto_main', 'stay_there', 'close_game']:
            h = WakeHarness(method=method)
            if method == 'close_game': h.script.config.script.optimization.close_game_wait_duration = daytime(minute=25)
            self.assertTrue(h.idle(method))
            h.actions.clear()
            self.assertTrue(h.run('KekkaiUtilize'))
            self.assertEqual(h.actions.count('wake_device'), 1)
            self.assertLess(h.actions.index('wake_device'), h.actions.index('screenshot'))

    def test_config_change_interrupt_still_marks_device_for_resume_check(self):
        h = WakeHarness()
        h.script.config.should_reload = lambda: True
        self.assertFalse(h.script.wait_until(h.now + timedelta(minutes=13)))
        self.assertTrue(h.run('KekkaiUtilize'))
        self.assertIn('wake_device', h.actions)

    def test_wake_failure_is_reported_without_success_or_retry_loop(self):
        h = WakeHarness(running=False, close_during_wait=False)
        def fail(config):
            h.actions.append('wake_attempt')
            raise h.errors['RequestHumanTakeover']('offline boot failure')
        h.ns['Device'] = fail
        h.script.wait_until(h.now - timedelta(seconds=2))
        with self.assertRaises(SystemExit): h.run('KekkaiUtilize')
        self.assertEqual(h.actions.count('wake_attempt'), 1)
        self.assertNotIn('screenshot', h.actions)
        self.assertFalse(any(isinstance(a, tuple) and a[0] == 'task' for a in h.actions))
        self.assertTrue(h.script._emulator_down)

    def test_queue_denial_does_not_query_or_wake(self):
        h = WakeHarness(running=False, close_during_wait=False)
        h.script.wait_until(h.now - timedelta(seconds=2))
        h.script._try_acquire_queue_token = lambda: False
        self.assertFalse(h.run('KekkaiUtilize'))
        self.assertEqual(h.query_count, 0)

class CaptureRegressions(unittest.TestCase):
    def test_missing_either_stream_or_fileno_does_not_mask_sdk_error(self):
        for streams in [NS(stdout=None, stderr=None), NS(stdout=io.StringIO(), stderr=None),
                        NS(stdout=None, stderr=io.StringIO()), NS(stdout=io.StringIO(), stderr=io.StringIO())]:
            ns = ipc_types(streams)
            with self.assertRaises(ns['NemuIpcError']): fake_ipc(ns).connect()

    def test_closed_stream_does_not_mask_sdk_error(self):
        out = tempfile.TemporaryFile(mode='w+'); out.close()
        ns = ipc_types(NS(stdout=out, stderr=None))
        with self.assertRaises(ns['NemuIpcError']): fake_ipc(ns).connect()

    def test_disabled_capture_does_not_swallow_body_exception(self):
        ns = ipc_types()
        with self.assertRaisesRegex(ValueError, 'body error'):
            with ns['CaptureStd'](): raise ValueError('body error')

    def test_normal_native_fd_capture_and_restore_are_preserved(self):
        streams = NS(stdout=tempfile.TemporaryFile(mode='w+'), stderr=tempfile.TemporaryFile(mode='w+'))
        ns = ipc_types(streams)
        try:
            before = (streams.stdout.fileno(), streams.stderr.fileno())
            with ns['CaptureStd']() as capture:
                os.write(streams.stdout.fileno(), b'native stdout')
                os.write(streams.stderr.fileno(), b'native stderr')
            self.assertEqual(capture.stdout, b'native stdout')
            self.assertEqual(capture.stderr, b'native stderr')
            self.assertEqual((streams.stdout.fileno(), streams.stderr.fileno()), before)
            streams.stdout.write('restored'); streams.stdout.flush()
        finally:
            streams.stdout.close(); streams.stderr.close()

    def test_nested_capture_leaves_topmost_instance_and_clears_after_exit(self):
        ns = ipc_types()
        with ns['CaptureNemuIpc']() as outer:
            self.assertIs(ns['CaptureNemuIpc'].instance, outer)
            with ns['CaptureNemuIpc']():
                self.assertIs(ns['CaptureNemuIpc'].instance, outer)
        self.assertIsNone(ns['CaptureNemuIpc'].instance)

    def test_successful_sdk_result_does_not_require_capture(self):
        ns = ipc_types(); impl = fake_ipc(ns)
        impl.ev_run_async = lambda *a, **kw: 77
        impl.connect()
        self.assertEqual(impl.connect_id, 77)

class MixedVersionCompatibility(unittest.TestCase):
    def test_closed_guest_behavior_is_bound_to_script_version(self):
        h = WakeHarness()
        if args.source == 'candidate':
            self.assertTrue(h.idle())
            self.assertEqual(h.actions.count('wake_device'), 1)
            self.assertNotIn('unexpected_stop', h.actions)
        else:
            with self.assertRaises(SystemExit): h.idle()
            self.assertNotIn('wake_device', h.actions)
            self.assertIn('save_error', h.actions)
            self.assertFalse(any(isinstance(a, tuple) and a[0] == 'task' for a in h.actions))

    def test_sdk_failure_classification_is_bound_to_capture_version(self):
        ns = ipc_types()
        expected = ns['NemuIpcError'] if (args.ipc_source or args.source) == 'candidate' else AttributeError
        with self.assertRaises(expected): fake_ipc(ns).connect()

    def test_running_guest_still_completes_without_reinitialization(self):
        status = dict(WakeHarness().status, is_process_started=True, is_android_started=True)
        h = WakeHarness(close_during_wait=False, status=status)
        self.assertTrue(h.idle())
        self.assertNotIn('wake_device', h.actions)
        self.assertNotIn('unexpected_stop', h.actions)

    def test_disabled_restart_still_stops_before_screenshot(self):
        h = WakeHarness(running=False, close_during_wait=False, restart_enabled=False)
        with self.assertRaises(SystemExit): h.run()
        self.assertNotIn('screenshot', h.actions)
        self.assertNotIn('wake_device', h.actions)

    def test_takeover_authorization_on_running_device_is_preserved(self):
        h = WakeHarness(close_during_wait=False, restart_enabled=False)
        h.script._account_session_restart_authorized = True
        self.assertTrue(h.run())
        self.assertIn(('task', True), h.actions)
        self.assertFalse(h.script._account_session_restart_authorized)
        self.assertFalse(h.script.device._account_session_recovery)

    def test_normal_capture_on_both_versions(self):
        CaptureRegressions('test_normal_native_fd_capture_and_restore_are_preserved').test_normal_native_fd_capture_and_restore_are_preserved()

if __name__ == '__main__':
    if args.repro_only:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReproductionTests)
    elif args.compat_only:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(MixedVersionCompatibility)
    else:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
