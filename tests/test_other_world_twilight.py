"""Device-free tests: AST method bodies, actual pure schemas, simulated frames only."""
import ast
from copy import deepcopy
from datetime import datetime, time, timedelta
from enum import Enum
import difflib
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace, MethodType, ModuleType
import unittest
import xml.etree.ElementTree as ET

import pydantic
import numpy as np

SOURCE = Path(__file__).resolve().parents[1]
LOG = SimpleNamespace(**{name: lambda *args, **kwargs: None for name in ('info', 'warning', 'error')})

class TaskEnd(Exception): pass
class GameStuckError(Exception): pass
class ScriptError(Exception): pass

def pure_model(relative, namespace, *, classes=None):
    tree = ast.parse((SOURCE / relative).read_text(encoding='utf-8-sig'))
    if classes:
        tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in classes]
    else:
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.If))]
    exec(compile(tree, str(SOURCE / relative), 'exec'), namespace)

def schemas():
    ns = {'__name__': 'offline_owt_schema', 'datetime': datetime, 'time': time,
          'timedelta': timedelta, 'Enum': Enum, 're': __import__('re'), 'Any': object}
    for name in ('BaseModel', 'Field', 'ValidationError', 'BeforeValidator', 'PlainSerializer',
                 'WithJsonSchema', 'field_serializer', 'SerializationInfo', 'validator'):
        ns[name] = getattr(pydantic, name)
    ns['Annotated'] = __import__('typing_extensions').Annotated
    pure_model('tasks/Component/config_base.py', ns)
    for file in ('tasks/Component/config_scheduler.py', 'tasks/Component/GeneralInvite/config_invite.py',
                 'tasks/Component/SwitchSoul/switch_soul_config.py',
                 'tasks/Component/GeneralBattle/config_general_battle.py'):
        pure_model(file, ns)
    pure_model('tasks/Orochi/config.py', ns, classes={'UserStatus'})
    pure_model('tasks/OtherWorldTwilight/config.py', ns)
    return ns

class Clock:
    def __init__(self): self.elapsed = 0
    def sleep(self, duration): self.elapsed += duration
    def timer(self, seconds):
        clock = self
        class Timer:
            def __init__(self): self.initial = None
            def start(self): self.initial = clock.elapsed; return self
            def clear(self): self.initial = None
            def started(self): return self.initial is not None
            def reached(self):
                clock.elapsed += .1
                return self.initial is not None and clock.elapsed - self.initial >= seconds
        return Timer()

class Page:
    def __init__(self, key): self.key = key
    def __eq__(self, other): return isinstance(other, Page) and self.key == other.key

PAGES = {name: Page(name) for name in ('page_main', 'page_owt', 'page_battle',
          'page_battle_prepare', 'page_reward', 'page_shikigami_records')}

class Harness:
    def __init__(self, **kwargs):
        self.clock = Clock()
        self.current_count = 0
        self.start_time = datetime.now()
        self.conf = SimpleNamespace(
            switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False,
                switch_group_team='2,3', group_name='御魂组', team_name='御魂队'),
            other_world_twilight_config=SimpleNamespace(user_status='alone', limit_count=2,
                limit_time_v=timedelta(hours=1)),
            general_battle_config=SimpleNamespace(lock_team_enable=False, green_mark_name='缘结神'),
            invite_config=SimpleNamespace(default_invite=True, wait_time=time(minute=2)))
        self.config = SimpleNamespace(other_world_twilight=self.conf)
        self.events, self.clicks, self.intervals, self.frameno = [], [], [], 0
        self.room = True
        self.create_ok = True
        self.ensure_ok = True
        self.invite_ok = True
        self.wait_ok = True
        self.real_battle = True
        self.preparing = False
        self.dark_preparing = False
        self.ocr_results = []
        self.ocr_error = None
        self.completed = True
        self.frame_pages = [Page('page_owt'), Page('page_battle_prepare'), Page('page_owt'), Page('page_battle')]
        self.O_GREEN_MARK_AREA = SimpleNamespace(roi=(84,256,1116,308), detect_and_ocr=self.detect)
        self.device = SimpleNamespace(image=object(), click=self.device_click,
            screenshot_interval_set=lambda *args: self.intervals.append(args),
            stuck_record_add=lambda *a: None, stuck_record_clear=lambda: None)
        for key in ('I_OWT_LOCK', 'I_OWT_UNLOCK', 'I_OWT_TEAM', 'I_OWT_FIRE', 'I_CHECK_TEAM',
                    'I_CHECK_MAIN', 'I_PREPARE_HIGHLIGHT', 'I_MATCHING', 'I_CHECK_EXPLORATION'):
            setattr(self, key, key)
        for key in ('I_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD'):
            setattr(self, key, key)
        self.result_visible = None
        tree = ast.parse((SOURCE / 'tasks/OtherWorldTwilight/script_task.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        ns = {'logger': LOG, 'datetime': datetime, 'Timer': self.clock.timer,
              'sleep': self.clock.sleep, 'difflib': difflib, 'TaskEnd': TaskEnd,
              'GameStuckError': GameStuckError, 'ScriptError': ScriptError,
              'OtherWorldTwilight': object,
              'UserStatus': SimpleNamespace(LEADER='leader', MEMBER='member', ALONE='alone'), **PAGES}
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<offline task methods>', 'exec'), ns)
        for method in methods: setattr(self, method.name, MethodType(ns[method.name], self))
        self.__dict__.update(kwargs)

    def screenshot(self):
        self.frameno += 1
        self.clock.elapsed += .5
        if self.frameno > 120: raise AssertionError('Unbounded polling')
    def goto_page(self, page): self.events.append(('goto', page.key))
    def check_lock(self, *args): self.events.append(('lock', args[0]))
    def ui_click(self, *args, **kwargs): self.events.append(('team',))
    def create_room(self): self.events.append(('create',)); return self.create_ok
    def ensure_private(self): self.events.append(('private',))
    def create_ensure(self): self.events.append(('ensure',)); return self.ensure_ok
    def check_and_invite(self, *args): return False
    def is_in_room(self, *args): return self.room
    def run_invite(self, **kwargs): self.events.append(('invite', kwargs.get('is_first', False))); return self.invite_ok
    def run_general_battle(self, *args, **kwargs):
        self.events.append(('battle',))
        self.current_count += 1
        self.real_battle = False
        return True
    def exit_room(self): self.events.append(('exit_room',)); return True
    def exit_team(self): self.events.append(('exit_team',)); return True
    def exit_battle(self): self.events.append(('exit_battle',)); return True
    def check_then_accept(self): return False
    def wait_battle(self, **kwargs): return self.wait_ok
    def is_in_battle(self, *args): return self.real_battle
    def is_in_real_battle(self, *args): return self.real_battle and not self.preparing and not self.dark_preparing
    def is_in_prepare(self, *args): return self.preparing or self.dark_preparing
    def get_current_page(self, *args):
        if self.frame_pages: return self.frame_pages.pop(0)
        return Page('page_owt')
    def appear(self, rule):
        if rule == self.result_visible: return True
        if rule == self.I_PREPARE_HIGHLIGHT: return self.preparing
        if rule == self.I_CHECK_MAIN: return self.completed
        return False
    def appear_then_click(self, rule, **kwargs): self.events.append(('fire', rule)); return True
    def detect(self, *args):
        if self.ocr_error: raise self.ocr_error
        return self.ocr_results
    def device_click(self, *args, **kwargs): self.clicks.append((args, kwargs))
    def set_next_run(self, *args, **kwargs): self.events.append(('schedule', args, kwargs))
    def run_switch_soul(self, *args): self.events.append(('switch', args))
    def run_switch_soul_by_name(self, *args): self.events.append(('switch_name', args))

class FeatureTests(unittest.TestCase):
    def test_new_task_in_config_model(self):
        tree = ast.parse((SOURCE / 'module/config/config_model.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigModel')
        fields = [n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)]
        self.assertIn('other_world_twilight', fields)
    def test_default_disabled_and_limits(self):
        ns = schemas(); conf = ns['OtherWorldTwilight']()
        self.assertFalse(conf.scheduler.enable)
        self.assertEqual(conf.other_world_twilight_config.limit_count, 30)
        self.assertEqual(conf.other_world_twilight_config.limit_time_v, timedelta(minutes=30))
    def test_three_roles_and_time_parsing(self):
        ns = schemas()
        self.assertEqual([role.value for role in ns['UserStatus']], ['leader', 'member', 'alone'])
        for role in ('alone', 'leader', 'member'):
            conf = ns['OtherWorldTwilightConfig'](user_status=role, limit_time='01:02:03', limit_count=7)
            self.assertEqual(conf.user_status, role)
            self.assertEqual(conf.limit_time_v, timedelta(hours=1, minutes=2, seconds=3))
    def test_named_green_schema_and_preset_hidden(self):
        conf = schemas()['OWTBattleConfig'](green_enable=True, green_mark_name='缘结神')
        self.assertEqual(conf.green_mark_name, '缘结神')
        hidden = conf.model_dump(context={'hide': True})
        self.assertEqual(hidden['preset_enable'], 0xABCDEF)
        self.assertEqual(hidden['green_mark'], 0xABCDEF)
        self.assertEqual(hidden['green_mark_name'], '缘结神')
    def test_menu_old_new_model_combinations(self):
        tree = ast.parse((SOURCE / 'module/config/config_menu.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigMenu')
        init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        for has_field in (False, True):
            mod = ModuleType('module.config.config_model')
            mod.ConfigModel = SimpleNamespace(model_fields={'other_world_twilight': object()} if has_field else {})
            prior = sys.modules.get(mod.__name__)
            sys.modules[mod.__name__] = mod
            try:
                ns = {}; exec(compile(ast.Module(body=[init], type_ignores=[]), '<menu>', 'exec'), ns)
                menu = SimpleNamespace(); ns['__init__'](menu)
                self.assertEqual('OtherWorldTwilight' in menu.menu['Soul Zones'], has_field)
                self.assertIn('CourtyardAffairs', menu.menu['Daily Task'])
            finally:
                if prior is None: sys.modules.pop(mod.__name__, None)
                else: sys.modules[mod.__name__] = prior
    def test_config_field_schema_and_scheduler_type(self):
        ns = schemas()
        tree = ast.parse((SOURCE / 'module/config/config_model.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigModel')
        field = next(n for n in cls.body if isinstance(n, ast.AnnAssign) and n.target.id == 'other_world_twilight')
        type_method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'type')
        minimal = ast.ClassDef(name='ConfigModel', bases=[ast.Name(id='ConfigBase', ctx=ast.Load())],
            keywords=[], body=[field, type_method], decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[minimal], type_ignores=[])), '<config-field>', 'exec'), ns)
        model = ns['ConfigModel']()
        self.assertFalse(model.other_world_twilight.scheduler.enable)
        self.assertEqual(ns['ConfigModel'].type('other_world_twilight'), 'OtherWorldTwilight')
        data = model.other_world_twilight.model_dump(mode='json')
        self.assertEqual(data['other_world_twilight_config']['limit_time'], '00:30:00')
        self.assertIn('green_mark_name', data['general_battle_config'])
        schema = model.model_json_schema()
        self.assertIn('OtherWorldTwilight', schema['$defs'])
    def test_old_writer_merge_preserves_new_task_settings(self):
        for root in (SOURCE,):
            tree = ast.parse((root / 'module/config/config_model.py').read_text(encoding='utf-8-sig'))
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigModel')
            methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('_local_changes', '_apply_local_changes')]
            minimal = ast.ClassDef(name='ConfigModel', bases=[], keywords=[], body=methods, decorator_list=[])
            ns = {'deepcopy': deepcopy}; exec(compile(ast.fix_missing_locations(ast.Module(body=[minimal], type_ignores=[])), '<merge>', 'exec'), ns)
            baseline = {'restart': {'scheduler': {'next_run': 'old'}}}
            current = {'restart': {'scheduler': {'next_run': 'new'}}}
            latest = deepcopy(baseline); latest['other_world_twilight'] = {'scheduler': {'enable': True}, 'limit': 5}
            changes = list(ns['ConfigModel']._local_changes(baseline, current))
            result = ns['ConfigModel']._apply_local_changes(latest, changes)
            self.assertEqual(result['other_world_twilight'], {'scheduler': {'enable': True}, 'limit': 5})
            self.assertEqual(result['restart']['scheduler']['next_run'], 'new')
    def test_page_entry_exit_and_clone_identity(self):
        # Run the actual new page module's definitions using simple atoms; no
        # rule modules, OCR model, device or account is imported.
        class GraphPage(Page):
            def __init__(self, recognizer=None, **kwargs):
                super().__init__(kwargs.get('key', 'page_owt')); self.edges = []
            def connect(self, destination, action, **kwargs): self.edges.append((destination.key, action))
        demon = GraphPage(key='page_demon_encounter')
        assets = SimpleNamespace(I_OWT_FIRE='fire', I_OWT_TEAM='team', I_DE_TO_OTHER_WORLD='entry')
        ns = {'page_demon_encounter': demon, 'Page': GraphPage, 'any_of': lambda *a: a,
              'GlobalGameAssets': SimpleNamespace(I_UI_BACK_YELLOW='back'), 'OtherWorldTwilightAssets': assets}
        tree = ast.parse((SOURCE / 'tasks/OtherWorldTwilight/page.py').read_text(encoding='utf-8-sig'))
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        exec(compile(tree, '<owt-page>', 'exec'), ns)
        self.assertEqual(demon.edges, [('page_owt', 'entry')])
        self.assertEqual(ns['page_owt'].edges, [('page_demon_encounter', 'back')])
        self.assertEqual(deepcopy(ns['page_owt']), ns['page_owt'])
    def test_all_files_syntax_and_asset_dimensions(self):
        for path in (SOURCE / 'tasks/OtherWorldTwilight').rglob('*.py'): ast.parse(path.read_text(encoding='utf-8-sig'))
        for item in json.loads((SOURCE / 'tasks/OtherWorldTwilight/owt/image.json').read_bytes()):
            data = (SOURCE / 'tasks/OtherWorldTwilight/owt' / item['imageName']).read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', data[16:24])
            roi = tuple(map(int, item['roiFront'].split(',')))
            self.assertGreater(width, 0); self.assertGreater(height, 0)
            self.assertEqual((width, height), roi[2:])
    def test_alone_uses_equal_session_clone_then_fights_twice(self):
        h = Harness(); h.run_alone()
        self.assertEqual(h.current_count, 2)
        self.assertEqual(sum(e[0] == 'fire' for e in h.events), 2)
    def test_alone_count_limit_does_not_click(self):
        h = Harness(current_count=2); h.run_alone()
        self.assertNotIn(('fire', 'I_OWT_FIRE'), h.events)
    def test_alone_time_limit_does_not_click(self):
        h = Harness(start_time=datetime.now() - timedelta(hours=2)); h.run_alone()
        self.assertEqual(h.current_count, 0)
    def test_alone_unknown_timer_survives_frames(self):
        h = Harness(frame_pages=[Page('unknown')] * 23 + [Page('page_battle')] * 2)
        h.run_alone()
        self.assertGreaterEqual(h.events.count(('goto', 'page_owt')), 2)
    def test_leader_first_and_later_invites_and_cleanup(self):
        h = Harness(); self.assertTrue(h.run_leader())
        self.assertEqual([e for e in h.events if e[0] == 'invite'], [('invite', True), ('invite', False)])
        self.assertEqual(h.current_count, 2)
        self.assertEqual(h.events[-2:], [('exit_room',), ('exit_team',)])
    def test_leader_create_failure_does_not_invite(self):
        h = Harness(create_ok=False); self.assertFalse(h.run_leader())
        self.assertFalse(any(e[0] in ('ensure', 'invite', 'battle') for e in h.events))
    def test_leader_ensure_failure_does_not_invite(self):
        h = Harness(ensure_ok=False); self.assertFalse(h.run_leader())
        self.assertFalse(any(e[0] in ('invite', 'battle') for e in h.events))
    def test_leader_invite_failure_cleanup(self):
        h = Harness(invite_ok=False); self.assertFalse(h.run_leader())
        self.assertEqual(h.current_count, 0)
        self.assertEqual(h.events[-2:], [('exit_room',), ('exit_team',)])
    def test_leader_dead_room_failure(self):
        h = Harness(room=False); h.is_room_dead = lambda: True
        self.assertFalse(h.run_leader())
    def test_member_accepts_and_runs_battle(self):
        h = Harness(); self.assertTrue(h.run_member())
        self.assertEqual(h.current_count, 2)
    def test_member_invitation_then_room_battle(self):
        h = Harness(); accepted = iter((True, False, False, False))
        h.check_then_accept = lambda: next(accepted)
        self.assertTrue(h.run_member()); self.assertEqual(h.current_count, 2)
        self.assertEqual(h.frameno, 5)
    def test_member_direct_battle_without_room(self):
        h = Harness(room=False)
        def battle(*args, **kwargs): h.current_count += 1
        h.run_general_battle = battle
        self.assertTrue(h.run_member()); self.assertEqual(h.current_count, 2)
    def test_member_wait_timeout_is_failure_without_battle(self):
        h = Harness(wait_ok=False)
        self.assertFalse(h.run_member()); self.assertEqual(h.current_count, 0)
    def test_member_cleanup_is_bounded_not_false_success(self):
        h = Harness(current_count=2, completed=False)
        with self.assertRaises(GameStuckError): h.run_member()
        self.assertLess(h.frameno, 65)
    def test_run_role_and_final_schedule(self):
        for role in ('leader', 'member', 'alone'):
            h = Harness(); h.conf.other_world_twilight_config.user_status = role
            with self.assertRaises(TaskEnd): h.run()
            self.assertEqual(h.events[-1][2], {'finish': True, 'success': True})
    def test_run_failed_create_schedules_failure(self):
        h = Harness(create_ok=False); h.conf.other_world_twilight_config.user_status = 'leader'
        with self.assertRaises(TaskEnd): h.run()
        self.assertFalse(h.events[-1][2]['success'])
    def test_run_unrecognized_role_raises_without_success(self):
        h = Harness(); h.conf.other_world_twilight_config.user_status = 'wild'
        with self.assertRaises(ScriptError): h.run()
        self.assertFalse(any(e[0] == 'schedule' for e in h.events))
    def test_switch_soul_disabled_does_not_switch(self):
        h = Harness()
        with self.assertRaises(TaskEnd): h.run()
        self.assertFalse(any(e[0].startswith('switch') for e in h.events))
    def test_both_explicit_switch_options_preserved(self):
        h = Harness(); h.conf.switch_soul.enable = True; h.conf.switch_soul.enable_switch_by_name = True
        with self.assertRaises(TaskEnd): h.run()
        self.assertIn(('switch', ('2,3',)), h.events)
        self.assertIn(('switch_name', ('御魂组', '御魂队')), h.events)
    def test_green_disabled_empty_and_no_battle_never_click(self):
        for mode in ('disabled', 'empty', 'finished'):
            h = Harness()
            if mode == 'empty': h.conf.general_battle_config.green_mark_name = ' '
            if mode == 'finished': h.real_battle = False
            h.green_mark(mode != 'disabled', None)
            self.assertEqual(h.clicks, [])
    def test_green_uses_best_name_and_current_two_argument_api(self):
        h = Harness(ocr_results=[SimpleNamespace(ocr_text='不相关', box=np.array([[0,0]])),
            SimpleNamespace(ocr_text='缘结神', box=np.array([[300,100]]))])
        h.green_mark(True, None)
        self.assertEqual(h.clicks[0][0], (389,386))
        self.assertEqual(h.clicks[0][1]['control_name'], '缘结神')
        self.assertEqual(h.intervals, [(), ('combat',)])
    def test_green_no_match_bounded_and_no_stale_click(self):
        h = Harness(ocr_results=[]); h.green_mark(True, None)
        self.assertEqual(h.clicks, []); self.assertLess(h.frameno, 15)
    def test_green_prepare_not_clicked_as_name(self):
        for option in ('preparing', 'dark_preparing'):
            h = Harness(**{option: True}); h.O_GREEN_MARK_AREA.detect_and_ocr = lambda *a: self.fail('OCR on preparation page')
            h.green_mark(True, None)
            self.assertEqual(h.clicks, [])
    def test_green_result_overlay_never_ocr_or_click(self):
        for overlay in ('I_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD'):
            h = Harness(); h.result_visible = overlay
            h.O_GREEN_MARK_AREA.detect_and_ocr = lambda *a: self.fail('OCR on result overlay')
            h.green_mark(True, None); self.assertEqual(h.clicks, [])
    def test_green_exception_propagates_and_restores_interval(self):
        h = Harness(ocr_error=RuntimeError('simulated OCR failure'))
        with self.assertRaisesRegex(RuntimeError, 'simulated'): h.green_mark(True, None)
        self.assertEqual(h.intervals[-1], ('combat',))
    def test_green_coordinates_stay_inside_canvas(self):
        h = Harness(ocr_results=[SimpleNamespace(ocr_text='缘结神', box=np.array([[1500,1000]]))])
        h.green_mark(True, None); self.assertEqual(h.clicks[0][0], (1279,719))

if __name__ == '__main__':
    unittest.main(verbosity=2)
