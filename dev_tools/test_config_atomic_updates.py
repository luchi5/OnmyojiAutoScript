"""Temporary-file CAS/watcher/serializer tests; never import the live server/logger."""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault('module.logger', types.SimpleNamespace(logger=Mock()))

from fastapi import HTTPException
from filelock import FileLock, Timeout
from module.config.config import Config
from module.config.config_model import ConfigModel, ConfigConflictError, EXPECTED_UNSET
from module.config.config_watcher import ConfigWatcher
from module.config.utils import write_file, convert_to_underscore
from tasks.Component.config_base import TimeDelta
from tasks.Component.guild_opening_config import GuildOpeningScheduler


def cas_writer(directory, value, barrier, results):
    os.chdir(directory)
    model = ConfigModel('sample')
    barrier.wait(timeout=20)
    try:
        model.script_set_arg('Script', 'error', 'notify_config', value, expected_value='original')
        results.put(('saved', value))
    except ConfigConflictError:
        results.put(('conflict', value))


class AtomicConfigTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='oas-cas-')
        os.chdir(self.temp.name)
        self.path = Path('config/sample.json')
        seed = ConfigModel()._serialized_data()
        seed['config_name'] = 'sample'
        seed['script']['error']['notify_config'] = 'original'
        write_file(self.path, seed)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def data(self):
        return json.loads(self.path.read_text(encoding='utf8'))

    def set_value(self, model, value, expected='original'):
        return model.script_set_arg('Script', 'error', 'notify_config', value, expected_value=expected)

    def test_same_field_script_edit_after_panel_read_rejected_at_save(self):
        panel, script = ConfigModel('sample'), ConfigModel('sample')
        script.script.error.notify_config = 'external-script'
        script.save()
        before = self.path.read_bytes()
        with self.assertRaises(ConfigConflictError):
            self.set_value(panel, 'panel')
        self.assertEqual(self.path.read_bytes(), before)

    def test_other_field_script_edit_is_preserved(self):
        panel, script = ConfigModel('sample'), ConfigModel('sample')
        script.restart.harvest_config.enable_mail = False
        script.save()
        self.assertTrue(self.set_value(panel, 'panel'))
        self.assertEqual(self.data()['script']['error']['notify_config'], 'panel')
        self.assertFalse(self.data()['restart']['harvest_config']['enable_mail'])

    def test_stale_expected_value_rejected_before_local_mutation(self):
        model = ConfigModel('sample')
        with self.assertRaises(ConfigConflictError):
            self.set_value(model, 'new', 'stale')
        self.assertEqual(model.script.error.notify_config, 'original')

    def test_identical_proposal_still_checks_external_changes(self):
        model = ConfigModel('sample')
        newer = ConfigModel('sample')
        newer.script.error.notify_config = 'external'
        newer.save()
        with self.assertRaises(ConfigConflictError):
            self.set_value(model, 'original')
        self.assertEqual(self.data()['script']['error']['notify_config'], 'external')

    def test_default_missing_field_can_be_saved_and_competing_write_conflicts(self):
        data = self.data()
        data['script']['error'].pop('notify_config')
        write_file(self.path, data)
        first, second = ConfigModel('sample'), ConfigModel('sample')
        default = first.script.error.notify_config
        self.set_value(first, 'first', default)
        with self.assertRaises(ConfigConflictError):
            self.set_value(second, 'second', default)
        self.assertEqual(self.data()['script']['error']['notify_config'], 'first')

    def test_missing_file_is_not_recreated_by_stale_editor(self):
        model = ConfigModel('sample')
        self.path.unlink()
        with self.assertRaises(ConfigConflictError):
            self.set_value(model, 'new')
        self.assertFalse(self.path.exists())

    def test_successive_expected_saves_update_source_baseline(self):
        model = ConfigModel('sample')
        self.set_value(model, 'first')
        self.set_value(model, 'second', 'first')
        self.assertEqual(self.data()['script']['error']['notify_config'], 'second')

    def test_legacy_clients_still_use_last_explicit_write(self):
        model, other = ConfigModel('sample'), ConfigModel('sample')
        other.script.error.notify_config = 'external'
        other.save()
        self.assertTrue(model.script_set_arg('Script', 'error', 'notify_config', 'legacy'))
        self.assertEqual(self.data()['script']['error']['notify_config'], 'legacy')

    def test_false_is_not_equal_to_zero_but_integral_number_is_accepted(self):
        self.assertFalse(ConfigModel._same_value(False, 0))
        self.assertFalse(ConfigModel._same_value(1, True))
        self.assertTrue(ConfigModel._same_value(1.0, 1))
        self.assertFalse(ConfigModel._same_value(float('nan'), float('nan')))

    def test_multi_enum_value_is_checked_as_a_whole(self):
        model = ConfigModel('sample')
        old = model._serialized_data()['global_game']['costume_config']['costume_main_type']
        model.script_set_arg('GlobalGame', 'costume_config', 'costume_main_type',
                             '["costume_main_17"]', expected_value=old)
        self.assertEqual(self.data()['global_game']['costume_config']['costume_main_type'], ['costume_main_17'])

    def test_raw_legacy_list_path_resolves_numbered_group(self):
        data = {'meta_demon': {'md_strategies': [{'foo': 4}, {'foo': 9}]}}
        path = ConfigModel._argument_path(data, 'meta_demon', 'md_strategies_2', 'foo')
        self.assertEqual(ConfigModel._path_value(data, path), 9)

    def test_numbered_groups_preserve_other_index_and_sibling_field(self):
        data = self.data()
        data['meta_demon']['meta_demon_config']['md_strategy_count'] = 2
        for index in (1, 2):
            data['meta_demon'][f'md_strategies_{index}'] = {
                'md_match_names': f'name-{index}',
                'md_preset_group_team_1': '1,1', 'md_preset_group_team_2': '1,1'}
        write_file(self.path, data)
        panel, worker = ConfigModel('sample'), ConfigModel('sample')
        worker.meta_demon.md_strategies[1].md_preset_group_team_1 = '2,2'
        worker.meta_demon.md_strategies[0].md_preset_group_team_2 = '3,3'
        worker.save()
        self.assertTrue(panel.script_set_arg('MetaDemon', 'md_strategies_1',
                                            'md_match_names', 'new-name', expected_value='name-1'))
        restored = ConfigModel('sample')
        self.assertEqual(restored.meta_demon.md_strategies[0].md_match_names, 'new-name')
        self.assertEqual(restored.meta_demon.md_strategies[0].md_preset_group_team_2, '3,3')
        self.assertEqual(restored.meta_demon.md_strategies[1].md_preset_group_team_1, '2,2')

    def test_legacy_array_layout_updates_only_targeted_field(self):
        data = self.data()
        data['meta_demon']['meta_demon_config']['md_strategy_count'] = 2
        for key in list(data['meta_demon']):
            if key.startswith('md_strategies_'):
                del data['meta_demon'][key]
        data['meta_demon']['md_strategies'] = [
            {'md_match_names': f'name-{index}', 'md_preset_group_team_1': '1,1',
             'md_preset_group_team_2': '1,1'} for index in (1, 2)]
        write_file(self.path, data)
        panel = ConfigModel('sample')
        latest = self.data()
        latest['meta_demon']['md_strategies'][1]['md_preset_group_team_1'] = '2,2'
        write_file(self.path, latest)
        self.assertTrue(panel.script_set_arg('MetaDemon', 'md_strategies_1',
                                            'md_match_names', 'new-name', expected_value='name-1'))
        restored = ConfigModel('sample')
        self.assertEqual(restored.meta_demon.md_strategies[0].md_match_names, 'new-name')
        self.assertEqual(restored.meta_demon.md_strategies[1].md_preset_group_team_1, '2,2')
        self.assertNotIn('md_strategies_1', self.data()['meta_demon'])

    def test_duration_expected_value_uses_display_format(self):
        model = ConfigModel('sample')
        condition = model.expect_argument('Dokan', 'scheduler', 'success_interval', '01 00:00:00')
        self.assertEqual(condition[0], ('dokan', 'scheduler', 'success_interval'))

    def test_wait_command_cas_checked_after_its_reload(self):
        config = Config('sample')
        previous = config.model._serialized_data()['chess']['scheduler']['next_run']
        original_save = ConfigModel.save
        def concurrent_save(instance, **kwargs):
            if kwargs.get('expected'):
                other = ConfigModel('sample')
                other.chess.scheduler.next_run = datetime(2030, 1, 1)
                original_save(other)
            return original_save(instance, **kwargs)
        with patch.object(ConfigModel, 'save', concurrent_save):
            with self.assertRaises(ConfigConflictError):
                config.task_delay('Chess', success=True, expected_value=previous)
        self.assertEqual(self.data()['chess']['scheduler']['next_run'], '2030-01-01 00:00:00')

    def test_two_real_processes_accept_exactly_one_same_field_cas(self):
        context = multiprocessing.get_context('spawn')
        barrier, results = context.Barrier(2), context.Queue()
        workers = [context.Process(target=cas_writer, args=(self.temp.name, value, barrier, results))
                   for value in ('first', 'second')]
        for worker in workers:
            worker.start()
        try:
            for worker in workers:
                worker.join(timeout=30)
            self.assertEqual([worker.exitcode for worker in workers], [0, 0])
            outcomes = [results.get(timeout=3), results.get(timeout=3)]
            self.assertEqual(sorted(state for state, _ in outcomes), ['conflict', 'saved'])
            winner = next(value for state, value in outcomes if state == 'saved')
            self.assertEqual(self.data()['script']['error']['notify_config'], winner)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=3)
                worker.close()
            results.close()


class ConfigWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oas-watch-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text('{}')
        self.watcher = ConfigWatcher()
        patcher = patch('module.config.config_watcher.filepath_config', return_value=str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unchanged_signature_does_not_reload(self):
        self.watcher.start_watching()
        self.assertFalse(self.watcher.should_reload())

    def test_same_second_change_reloads(self):
        base = 1800000000000000000
        os.utime(self.path, ns=(base, base + 100000000))
        self.watcher.start_watching()
        os.utime(self.path, ns=(base, base + 900000000))
        self.assertTrue(self.watcher.should_reload())

    def test_timestamp_rollback_reloads(self):
        self.watcher.start_watching()
        previous = self.path.stat().st_mtime_ns
        os.utime(self.path, ns=(previous, previous - 2000000000))
        self.assertTrue(self.watcher.should_reload())

    def test_atomic_replacement_same_size_and_time_reloads(self):
        self.watcher.start_watching()
        stat = self.path.stat()
        replacement = self.path.with_suffix('.tmp')
        replacement.write_text('[]')
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, self.path)
        self.assertTrue(self.watcher.should_reload())

    def test_size_change_reloads_even_with_same_timestamp(self):
        self.watcher.start_watching()
        stat = self.path.stat()
        self.path.write_text('{"new":true}')
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertTrue(self.watcher.should_reload())

    def test_start_watching_again_acknowledges_change(self):
        self.watcher.start_watching()
        self.path.write_text('{"new":true}')
        self.assertTrue(self.watcher.should_reload())
        self.watcher.start_watching()
        self.assertFalse(self.watcher.should_reload())
        self.assertIsInstance(self.watcher.get_mtime(), datetime)


class SerializerTests(unittest.TestCase):
    def test_hidden_intervals_roundtrip_in_canonical_format(self):
        scheduler = GuildOpeningScheduler(success_interval=timedelta(days=2, hours=3))
        data = scheduler.model_dump(mode='json')
        self.assertEqual(data['success_interval'], '02 03:00:00')
        restored = GuildOpeningScheduler(**data)
        self.assertEqual(restored.success_interval, timedelta(days=2, hours=3))
        self.assertEqual(scheduler.model_dump(context={'hide': True})['success_interval'], 0xABCDEF)

    def test_import_rechecks_digest_and_replaces_while_file_lock_is_held(self):
        # Import the module by path: importing module.server would start its manager.
        import importlib.util
        spec = importlib.util.spec_from_file_location('offline_transfer_lock', ROOT / 'module/server/config_transfer.py')
        transfer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(transfer)
        with tempfile.TemporaryDirectory(prefix='oas-import-lock-') as directory:
            service = transfer.ConfigTransferService(directory, model_type=ConfigModel)
            path = Path(directory) / 'sample.json'
            path.write_text('{"config_name":"sample"}', encoding='utf8')
            _, _, data, digest = service._read('sample')
            replacement = os.replace
            def replace_locked(source, target):
                with self.assertRaises(Timeout):
                    with FileLock(f'{path}.lock', timeout=0):
                        pass
                replacement(source, target)
            data['running_task'] = ''
            with patch.object(transfer.os, 'replace', side_effect=replace_locked) as replace:
                service._write('sample', path, data, previous=digest)
            replace.assert_called_once()
            self.assertEqual(json.loads(path.read_text(encoding='utf8')), data)


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_translates_authoritative_conflict_to_409(self):
        source = ast.parse((ROOT / 'module/server/script_router.py').read_text(encoding='utf8'))
        route = next(node for node in source.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == 'script_task' and any(arg.arg == 'expected_value' for arg in node.args.args))
        helper = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_expected_value')
        route.decorator_list = []
        model = Mock()
        model.script_set_arg.side_effect = ConfigConflictError('conflict')
        manager = types.SimpleNamespace(config_cache=lambda _: types.SimpleNamespace(model=model))
        env = dict(json=json, datetime=datetime, TimeDelta=TimeDelta, HTTPException=HTTPException,
                   ConfigConflictError=ConfigConflictError, EXPECTED_UNSET=EXPECTED_UNSET, mm=manager, logger=Mock())
        exec(compile(ast.Module(body=[helper, route], type_ignores=[]), '<offline-route>', 'exec'), env)
        with self.assertRaises(HTTPException) as error:
            await env['script_task']('sample', 'Script', 'error', 'notify_config', 'string', 'new', '"old"')
        self.assertEqual(error.exception.status_code, 409)
        model.script_set_arg.assert_called_once_with('Script', 'error', 'notify_config', 'new', expected_value='old')
        for value in ('NaN', '{broken'):
            with self.assertRaises(HTTPException) as invalid:
                env['_expected_value'](value)
            self.assertEqual(invalid.exception.status_code, 400)
        self.assertIs(env['_expected_value'](None), EXPECTED_UNSET)


if __name__ == '__main__':
    unittest.main(verbosity=2)
