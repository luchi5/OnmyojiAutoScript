"""Real config persistence under isolated temporary directories; no game or push.

Run: toolkit/python.exe -X utf8 -B -m unittest dev_tools.test_config_concurrent_updates
"""
from copy import deepcopy
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from module.config.config import Config
from module.config.config_model import ConfigModel
from module.config.utils import write_file
from tasks.Component.Costume.config import MainType


def concurrent_writer(directory, role, barrier):
    """Two genuinely separate processes write siblings from stale snapshots."""
    os.chdir(directory)
    model = ConfigModel('sample')
    for step in range(6):
        barrier.wait(timeout=30)
        if role == 'schedule':
            model.restart.scheduler.next_run = datetime(2026, 10, 7, 12, step, 0)
        else:
            model.restart.harvest_config.enable_mail = step % 2 == 0
            model.kekkai_utilize.utilize_config.utilize_harvest = step % 2 == 0
        model.save()
        barrier.wait(timeout=30)


class ConcurrentConfigTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.directory = tempfile.TemporaryDirectory(prefix='oas-config-test-')
        os.chdir(self.directory.name)
        self.path = Path('config/sample.json')
        seed = json.loads(json.dumps(ConfigModel().model_dump(), default=str))
        seed['config_name'] = 'sample'
        write_file(self.path, seed)

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.directory.cleanup()

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def external_edit(self, callback):
        data = self.read()
        callback(data)
        write_file(self.path, data)

    def test_constructor_and_private_baseline_do_not_save(self):
        before = self.path.read_bytes()
        model = ConfigModel('sample')
        model._save_baseline = deepcopy(model._save_baseline)
        ConfigModel('missing')
        ConfigModel()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path('config/missing.json').exists())
        self.assertFalse(Path('config/oas.json').exists())

    def test_stale_runtime_save_preserves_external_bark_mail_and_harvest(self):
        worker = ConfigModel('sample')
        editor = ConfigModel('sample')
        editor.script.error.notify_enable = True
        editor.script.error.notify_config = 'provider: bark\nkey: offline-test'
        editor.restart.harvest_config.enable_mail = False
        editor.kekkai_utilize.utilize_config.utilize_harvest = False
        editor.save()
        worker.restart.scheduler.next_run = datetime(2026, 10, 7, 12, 34, 56)
        worker.running_task = 'Restart'  # Actual auto-save path.
        worker.running_task = ''
        data = self.read()
        self.assertEqual(data['script']['error']['notify_config'], 'provider: bark\nkey: offline-test')
        self.assertTrue(data['script']['error']['notify_enable'])
        self.assertFalse(data['restart']['harvest_config']['enable_mail'])
        self.assertFalse(data['kekkai_utilize']['utilize_config']['utilize_harvest'])
        self.assertEqual(data['restart']['scheduler']['next_run'], '2026-10-07 12:34:56')
        self.assertEqual(data['running_task'], '')

    def test_changes_to_two_sibling_fields_both_survive(self):
        one, two = ConfigModel('sample'), ConfigModel('sample')
        one.restart.harvest_config.enable_mail = False
        two.restart.harvest_config.enable_sign = False
        one.save()
        two.save()
        harvest = self.read()['restart']['harvest_config']
        self.assertFalse(harvest['enable_mail'])
        self.assertFalse(harvest['enable_sign'])

    def test_same_field_local_change_is_committed(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data['script']['error'].update(notify_config='external'))
        worker.script.error.notify_config = 'local-explicit'
        worker.save()
        self.assertEqual(self.read()['script']['error']['notify_config'], 'local-explicit')

    def test_second_save_does_not_replay_first_local_change(self):
        worker = ConfigModel('sample')
        worker.script.error.notify_config = 'first-local'
        worker.save()
        self.external_edit(lambda data: data['script']['error'].update(notify_config='newer-external'))
        worker.running_task = 'Orochi'
        worker.save()
        self.assertEqual(self.read()['script']['error']['notify_config'], 'newer-external')

    def test_baseline_is_independent_of_mutable_nested_models(self):
        worker = ConfigModel('sample')
        baseline = worker._save_baseline['restart']['harvest_config']['enable_mail']
        worker.restart.harvest_config.enable_mail = not baseline
        self.assertEqual(worker._save_baseline['restart']['harvest_config']['enable_mail'], baseline)
        worker.save()
        self.assertEqual(self.read()['restart']['harvest_config']['enable_mail'], not baseline)

    def test_new_unknown_fields_survive_stale_save(self):
        worker = ConfigModel('sample')
        def add_unknown(data):
            data['future_task'] = {'new_option': [1, {'enabled': True}]}
            data['restart']['harvest_config']['future_flag'] = True
        self.external_edit(add_unknown)
        worker.restart.harvest_config.enable_mail = False
        worker.save()
        data = self.read()
        self.assertEqual(data['future_task'], {'new_option': [1, {'enabled': True}]})
        self.assertTrue(data['restart']['harvest_config']['future_flag'])

    def test_external_delete_of_unchanged_known_field_stays_deleted(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data['restart']['harvest_config'].pop('enable_mail'))
        worker.running_task = 'GoldYoukai'
        self.assertNotIn('enable_mail', self.read()['restart']['harvest_config'])

    def test_explicit_local_deletion_is_saved_without_deleting_siblings(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data['script']['error'].update(future_flag=True))
        del worker.script.error.notify_config
        worker.save()
        error = self.read()['script']['error']
        self.assertNotIn('notify_config', error)
        self.assertTrue(error['future_flag'])
        self.assertIn('notify_enable', error)

    def test_list_replacement_is_atomic_and_enum_serialization_is_stable(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data['global_game']['costume_config'].update(
            costume_main_type=['costume_main_1', 'costume_main_2'], future_flag=True))
        worker.global_game.costume_config.costume_main_type = [MainType.COSTUME_MAIN_17]
        worker.save()
        costume = self.read()['global_game']['costume_config']
        self.assertEqual(costume['costume_main_type'], ['costume_main_17'])
        self.assertTrue(costume['future_flag'])
        self.external_edit(lambda data: data['global_game']['costume_config'].update(
            costume_main_type=['costume_main_13']))
        worker.running_task = 'Restart'
        self.assertEqual(self.read()['global_game']['costume_config']['costume_main_type'], ['costume_main_13'])

    def test_no_changes_do_not_rewrite_or_normalize_disk(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data.update(future_task={'version': 1}))
        before = self.path.read_bytes()
        before_mtime = self.path.stat().st_mtime_ns
        worker.save()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)

    def test_explicit_save_can_create_missing_file(self):
        worker = ConfigModel('new-account')
        self.assertFalse(Path('config/new-account.json').exists())
        worker.save()
        data = json.loads(Path('config/new-account.json').read_text(encoding='utf-8'))
        self.assertEqual(data['config_name'], 'new-account')
        self.assertIn('restart', data)

    def test_malformed_disk_is_not_silently_replaced(self):
        worker = ConfigModel('sample')
        self.path.write_text('{broken', encoding='utf-8')
        worker.restart.harvest_config.enable_mail = False
        with self.assertRaises(json.JSONDecodeError):
            worker.save()
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{broken')
        self.assertTrue(worker._save_baseline['restart']['harvest_config']['enable_mail'])

    def test_config_save_uses_the_same_merge_path(self):
        config = Config('sample')
        self.external_edit(lambda data: data['script']['error'].update(notify_config='external'))
        config.model.restart.scheduler.next_run = datetime(2026, 10, 7, 11, 22, 33)
        config.save()
        data = self.read()
        self.assertEqual(data['script']['error']['notify_config'], 'external')
        self.assertEqual(data['restart']['scheduler']['next_run'], '2026-10-07 11:22:33')

    def test_notifier_initializes_from_latest_disk_then_reload_clears_cache(self):
        config = Config('sample')
        self.external_edit(lambda data: data['script']['error'].update(
            notify_config='offline-first', notify_enable=True))
        first, second = Mock(), Mock()
        with patch('module.config.config.Notifier', side_effect=[first, second]) as provider:
            self.assertIs(config.notifier, first)
            provider.assert_called_once_with('offline-first', enable=True)
            self.external_edit(lambda data: data['script']['error'].update(
                notify_config='offline-second', notify_enable=False))
            config.reload()
            self.assertNotIn('notifier', config.__dict__)
            self.assertIs(config.notifier, second)
            self.assertEqual(provider.call_args.args, ('offline-second',))
            self.assertEqual(provider.call_args.kwargs, {'enable': False})

    def test_bulk_schedule_reset_preserves_external_notification_edit(self):
        worker = ConfigModel('sample')
        self.external_edit(lambda data: data['script']['error'].update(notify_config='external'))
        worker.reset_datetime_for_all_enabled_tasks(datetime(2026, 10, 7, 13, 14, 15))
        data = self.read()
        self.assertEqual(data['script']['error']['notify_config'], 'external')
        self.assertEqual(data['orochi']['scheduler']['next_run'], '2026-10-07 13:14:15')
        worker.save()
        self.assertEqual(self.read()['script']['error']['notify_config'], 'external')

    def test_actual_spawn_processes_preserve_concurrent_sibling_updates(self):
        ctx = multiprocessing.get_context('spawn')
        barrier = ctx.Barrier(2)
        processes = [ctx.Process(target=concurrent_writer,
                                 args=(self.directory.name, role, barrier))
                     for role in ('schedule', 'harvest')]
        for process in processes:
            process.start()
        try:
            for process in processes:
                process.join(timeout=45)
            self.assertEqual([p.exitcode for p in processes], [0, 0])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        data = self.read()
        self.assertEqual(data['restart']['scheduler']['next_run'], '2026-10-07 12:05:00')
        self.assertFalse(data['restart']['harvest_config']['enable_mail'])
        self.assertFalse(data['kekkai_utilize']['utilize_config']['utilize_harvest'])


if __name__ == '__main__':
    unittest.main()
