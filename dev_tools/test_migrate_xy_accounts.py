"""Migration control tests using synthetic configs, temporary files and a fake backend.

No real frontend profile, source account, private account or HTTP service is read.
The production CLI is never invoked.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


TOOL = Path(__file__).with_name('migrate_xy_accounts.py')
FIXED_PRIVATE = Path(r'C:\Onmyoji\OnmyojiAutoScript-runhey').resolve()


def canonical(data):
    """Model the mainline numbered-list encoding without importing game modules."""
    result = copy.deepcopy(data)
    for task in result.values():
        if not isinstance(task, dict):
            continue
        numbered = sorted((key for key in task if key.startswith('md_strategy_')
                           and key.rsplit('_', 1)[-1].isdigit()),
                          key=lambda key: int(key.rsplit('_', 1)[-1]))
        if numbered:
            task['md_strategy'] = [task.pop(key) for key in numbered]
    return result


class FakeService:
    def _normalize(self, data, model=None):
        return canonical(data)

    def _validated(self, data):
        return canonical(data)


class FakeConverter:
    def __init__(self, *args, **kwargs):
        self.service = FakeService()
        self.model_type = object

    def convert(self, source, target):
        result = canonical(source)
        result['config_name'] = target
        result['running_task'] = ''
        return result, {'validated': True, 'counts': {'field_changes': 0}}


class SandboxPrivateRoot:
    """Keep the production location guard, while mapping every file access to temp."""
    def __init__(self, temporary_root):
        self.temporary_root = temporary_root

    def resolve(self):
        return FIXED_PRIVATE

    def __truediv__(self, child):
        return self.temporary_root / child


class FakeOasClient:
    def __init__(self):
        self.names = ['existing']
        self.documents = {}
        self.calls = []
        self.states = []
        self.import_error = None
        self.commit_before_error = False
        self.export_transform = lambda document: document
        self.after_export = None
        self.commands = ['Chess', 'KekkaiUtilize', 'MetaDemon']
        self.fail_disabled_snapshot = False
        self.close_error = False

    async def transfer_capabilities(self):
        self.calls.append(('capabilities',))
        return {'version': 1, 'config_import': True, 'task_import': True,
                'modes': ['backup', 'share']}

    async def accounts(self):
        self.calls.append(('accounts',))
        return list(self.names)

    async def menu(self):
        self.calls.append(('menu',))
        return {'Fixture': self.commands}

    async def transfer_import(self, name, text, task=None):
        self.calls.append(('import', name))
        if task is not None or name in self.names:
            raise RuntimeError('unexpected overwrite or task import')
        if self.import_error is not None and not self.commit_before_error:
            raise self.import_error
        document = json.loads(text)
        for value in document.values():
            if isinstance(value, dict) and isinstance(value.get('scheduler'), dict):
                value['scheduler']['enable'] = False
        self.documents[name] = document
        self.names.append(name)
        if self.import_error is not None:
            raise self.import_error
        return {'name': name}

    async def snapshot(self, name):
        self.calls.append(('snapshot', name))
        if self.fail_disabled_snapshot and not any(
                isinstance(value, dict) and value.get('scheduler', {}).get('enable') is True
                for value in self.documents.get(name, {}).values()):
            raise RuntimeError('all-disabled connection closed before schedule')
        state = self.states.pop(0) if self.states else 0
        return {'connected': state is not None, 'state': state, 'schedule': {}}

    @contextlib.asynccontextmanager
    async def connection(self, name):
        self.calls.append(('connection', name))
        state = self.states.pop(0) if self.states else 0
        client = self
        class StateThenClose:
            reads = 0
            async def recv(self):
                self.reads += 1
                client.calls.append(('recv', name))
                if self.reads > 1 or state is None:
                    raise OSError('synthetic WS closed before schedule')
                return json.dumps({'state': state})
        yield StateThenClose()
        if self.close_error:
            raise OSError('synthetic WS closed during context exit')

    @staticmethod
    def event(raw):
        parsed = json.loads(raw)
        if type(parsed.get('state')) is int and parsed['state'] in (0, 1, 2, 3):
            return {'type': 'state', 'state': parsed['state']}
        return {'type': 'log', 'line': raw}

    async def set_value(self, account, task, group, argument, value, kind):
        self.calls.append(('set_value', account, task, group, argument, value, kind))
        if (group, argument, value, kind) != ('scheduler', 'enable', True, 'boolean'):
            raise AssertionError('only scheduler enable may be restored')
        key = {'Chess': 'chess', 'KekkaiUtilize': 'kekkai_utilize',
               'MetaDemon': 'meta_demon'}[task]
        self.documents[account][key]['scheduler']['enable'] = True

    async def transfer_export(self, name, mode, task=None):
        self.calls.append(('export', name, mode, task))
        if mode != 'backup' or task is not None:
            raise AssertionError('verification must read the full backup')
        result = self.export_transform(copy.deepcopy(self.documents[name]))
        if self.after_export is not None:
            self.after_export()
        return result

    async def action(self, *args, **kwargs):
        raise AssertionError('migration must never start or stop any process')


class MigrationControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.private = base / 'private'
        (self.private / 'config').mkdir(parents=True)
        self.migrations = base / 'migration-records'
        self.backup = self.migrations / 'frozen'
        self.source = self.backup / 'xy-original'
        self.before = self.backup / 'private-before'
        self.source.mkdir(parents=True)
        self.before.mkdir()
        self.old_content = b'{"config_name":"existing","fixture":"unchanged"}'
        (self.private / 'config' / 'existing.json').write_bytes(self.old_content)
        (self.before / 'existing.json').write_bytes(self.old_content)
        self.raw = {
            'config_name': 'source-old-name', 'running_task': 'must-not-run',
            'script': {'device': {'serial': 'synthetic-device', 'package_name': 'synthetic.package'}},
            'chess': {'scheduler': {'enable': True, 'next_run': '2030-01-01 12:00:00'},
                      'chess_config': {'synthetic_option': 'preserved'}},
            'kekkai_utilize': {'scheduler': {'enable': False},
                              'utilize_config': {'min_taiko_value': 60}},
        }
        self.write_source('9', self.raw)
        converter_module = types.ModuleType('xy_config_migration')
        converter_module.Converter = FakeConverter
        converter_module.convert_xy_config = lambda source, target: FakeConverter().convert(source, target)
        adapter_module = types.ModuleType('oas_client')
        adapter_module.OasClient = lambda address: (_ for _ in ()).throw(
            AssertionError('backend constructor must be replaced by the fake'))
        self.modules = patch.dict(sys.modules, {'xy_config_migration': converter_module,
                                               'oas_client': adapter_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        specification = importlib.util.spec_from_file_location('_isolated_xy_migration', TOOL)
        self.tool = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.tool)
        self.client = FakeOasClient()
        self.factory_addresses = []

        def create_client(address):
            self.factory_addresses.append(address)
            return self.client

        self.tool.PRIVATE_ROOT = SandboxPrivateRoot(self.private)
        self.tool.MIGRATIONS = self.migrations.resolve()
        self.tool.OasClient = create_client
        self.frontend_checks = 0

        def check_frontend():
            self.frontend_checks += 1

        self.tool.check_frontend = check_frontend

    def write_source(self, name, document):
        (self.source / (name + '.json')).write_text(
            json.dumps(document, ensure_ascii=False), encoding='utf-8')

    async def migrate(self, apply=True, resume=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return await self.tool.run(self.backup, apply=apply, resume=resume)

    async def interrupted_after_first_import(self):
        self.client.states = [None]
        with self.assertRaisesRegex(RuntimeError, '停止状态'):
            await self.migrate()
        self.client.calls.clear()
        return self.report()['imported'][0]['target']

    def save_report(self, report):
        (self.backup / 'migration-report.json').write_text(
            json.dumps(report, ensure_ascii=False), encoding='utf-8')

    def report(self):
        return json.loads((self.backup / 'migration-report.json').read_text(encoding='utf-8'))

    def operations(self, kind):
        return [call for call in self.client.calls if call[0] == kind]

    def assert_originals_unchanged(self):
        self.assertEqual((self.private / 'config' / 'existing.json').read_bytes(), self.old_content)
        self.assertEqual((self.before / 'existing.json').read_bytes(), self.old_content)
        self.assertEqual(json.loads((self.source / '9.json').read_text()), self.raw)

    async def test_dry_run_has_no_write_requests_and_keeps_originals(self):
        await self.migrate(apply=False)
        self.assertFalse(self.operations('import'))
        self.assertFalse(self.operations('set_value'))
        self.assertEqual(self.factory_addresses, ['http://127.0.0.1:22289'])
        self.assertFalse(self.report()['applied'])
        self.assert_originals_unchanged()

    async def test_success_restores_only_selected_enable_flags_without_start(self):
        await self.migrate()
        self.assertEqual(self.operations('import'), [('import', 'XY-9')])
        self.assertEqual(self.operations('set_value'), [
            ('set_value', 'XY-9', 'Chess', 'scheduler', 'enable', True, 'boolean')])
        target = self.client.documents['XY-9']
        expected, _ = FakeConverter().convert(self.raw, 'XY-9')
        self.assertEqual(target, expected)
        self.assertFalse(target['kekkai_utilize']['scheduler']['enable'])
        report = self.report()
        self.assertTrue(report['applied'])
        self.assertTrue(report['imported'][0]['verified'])
        self.assertEqual(report['imported'][0]['state'], 0)
        self.assertNotIn('synthetic-device', json.dumps(report))
        self.assert_originals_unchanged()

    async def test_existing_name_is_rejected_case_insensitively_without_post(self):
        self.client.names.append('xy-9')
        with self.assertRaisesRegex(RuntimeError, '同名'):
            await self.migrate()
        self.assertFalse(self.operations('import'))
        self.assert_originals_unchanged()

    async def test_colliding_sanitized_source_names_are_rejected_before_client(self):
        self.write_source('a b', self.raw)
        self.write_source('a+b', self.raw)
        with self.assertRaisesRegex(ValueError, '重复'):
            await self.migrate()
        self.assertFalse(self.factory_addresses)

    async def test_unregistered_enabled_task_is_rejected_before_any_import(self):
        self.client.commands.remove('Chess')
        with self.assertRaisesRegex(RuntimeError, '未注册'):
            await self.migrate()
        self.assertFalse(self.operations('import'))

    async def test_nonstopped_new_config_never_restores_enable_or_imports_next(self):
        self.write_source('10', self.raw)
        self.client.states = [1]
        with self.assertRaisesRegex(RuntimeError, '停止状态'):
            await self.migrate()
        self.assertEqual(len(self.operations('import')), 1)
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.report()['applied'])
        self.assertFalse(self.report()['imported'][0]['verified'])

    async def test_state_change_between_import_and_restore_prevents_put(self):
        self.client.states = [0, 1]
        with self.assertRaisesRegex(RuntimeError, '状态已改变'):
            await self.migrate()
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.report()['applied'])

    async def test_disconnected_new_config_never_restores_enable(self):
        self.client.states = [None]
        with self.assertRaisesRegex(RuntimeError, '停止状态'):
            await self.migrate()
        self.assertFalse(self.operations('set_value'))

    async def test_true_field_drift_is_rejected_and_keeps_partial_report(self):
        def drift(document):
            document['script']['device']['serial'] = 'changed-synthetic-device'
            return document
        self.client.export_transform = drift
        with self.assertRaisesRegex(RuntimeError, '回读'):
            await self.migrate()
        self.assertFalse(self.report()['applied'])
        self.assertFalse(self.report()['imported'][0]['verified'])
        self.assert_originals_unchanged()

    async def test_schema_equivalent_numbered_list_readback_is_accepted(self):
        self.raw['meta_demon'] = {
            'scheduler': {'enable': False},
            'md_strategy': [{'synthetic_strategy': 'first'}, {'synthetic_strategy': 'second'}],
        }
        self.write_source('9', self.raw)
        def numbered(document):
            task = document['meta_demon']
            for index, item in enumerate(task.pop('md_strategy'), start=1):
                task['md_strategy_' + str(index)] = item
            return document
        self.client.export_transform = numbered
        await self.migrate()
        self.assertTrue(self.report()['applied'])
        self.assert_originals_unchanged()

    async def test_final_running_state_cannot_be_reported_as_stopped_success(self):
        self.client.states = [0, 0, 1]
        with self.assertRaisesRegex(RuntimeError, '不再停止'):
            await self.migrate()
        report = self.report()
        self.assertFalse(report['applied'])
        self.assertFalse(report['imported'][0]['verified'])
        self.assertEqual(report['failure']['stage'], 'verify_state')
        self.assertEqual(report['failure']['target'], 'XY-9')

    async def test_existing_file_hash_change_blocks_all_backend_calls(self):
        (self.private / 'config' / 'existing.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '已经改变'):
            await self.migrate()
        self.assertFalse(self.factory_addresses)

    async def test_original_hash_change_during_import_cannot_report_success(self):
        self.client.after_export = lambda: (self.private / 'config' / 'existing.json').write_text(
            '{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '已经改变'):
            await self.migrate()
        self.assertFalse(self.report()['applied'])

    async def test_wrong_converted_identity_is_rejected_before_client(self):
        fake_module = sys.modules['xy_config_migration']
        def wrong(source, target):
            candidate, details = FakeConverter().convert(source, target)
            candidate['config_name'] = 'wrong-target'
            return candidate, details
        fake_module.convert_xy_config = wrong
        with self.assertRaisesRegex(ValueError, '身份'):
            await self.migrate()
        self.assertFalse(self.factory_addresses)

    async def test_import_timeout_records_attempt_without_leaking_payload(self):
        self.client.import_error = TimeoutError('synthetic-private-payload-must-not-be-reported')
        self.client.commit_before_error = True
        with self.assertRaises(TimeoutError):
            await self.migrate()
        report = self.report()
        self.assertFalse(report['applied'])
        attempted = report.get('attempted', report.get('imported', []))
        self.assertTrue(any(item['target'] == 'XY-9' for item in attempted))
        self.assertEqual(report['failure']['type'], 'TimeoutError')
        self.assertEqual(report['failure']['stage'], 'import_config')
        self.assertEqual(report['failure']['target'], 'XY-9')
        self.assertFalse(report['imported'][0]['confirmed_imported'])
        self.assertNotIn('synthetic-private-payload-must-not-be-reported', json.dumps(report))
        self.assertFalse(self.operations('set_value'))

    async def test_all_disabled_state_then_close_is_enough_to_confirm_stopped(self):
        self.client.fail_disabled_snapshot = True
        await self.migrate()
        self.assertTrue(self.report()['applied'])
        self.assertFalse(self.operations('snapshot'))
        # Every fake WS closes on a second receive; no schedule is provided.
        self.assertEqual(len(self.operations('connection')), len(self.operations('recv')))

    async def test_received_state_survives_connection_exit_error(self):
        self.client.close_error = True
        observed = await self.tool.observe_state(self.client, 'XY-9')
        self.assertEqual(observed, {'connected': True, 'state': 0})
        self.assertEqual(len(self.operations('recv')), 1)

    async def test_resume_restores_existing_first_account_and_imports_other_eight(self):
        for name in range(1, 9):
            self.write_source(str(name), self.raw)
        first_target = await self.interrupted_after_first_import()
        self.assertEqual(first_target, 'XY-1')
        before = copy.deepcopy(self.client.documents[first_target])
        self.assertFalse(before['chess']['scheduler']['enable'])
        await self.migrate(resume=True)
        self.assertNotIn(('import', first_target), self.operations('import'))
        self.assertEqual(len(self.operations('import')), 8)
        self.assertEqual(len(self.client.documents), 9)
        self.assertTrue(self.report()['applied'])
        self.assertEqual(len(self.report()['imported']), 9)
        self.assertTrue(all(item['verified'] and item['state'] == 0
                            for item in self.report()['imported']))
        for name, document in self.client.documents.items():
            expected, _ = FakeConverter().convert(self.raw, name)
            self.assertEqual(document, expected)
        self.assert_originals_unchanged()

    async def test_resume_rejects_existing_target_without_previous_attempt(self):
        await self.migrate(apply=False)
        candidate, _ = FakeConverter().convert(self.raw, 'XY-9')
        candidate['chess']['scheduler']['enable'] = False
        self.client.names.append('XY-9')
        self.client.documents['XY-9'] = candidate
        self.client.calls.clear()
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('import'))
        self.assertFalse(self.operations('set_value'))

    async def test_resume_rejects_parameter_drift_before_restoring_any_enable(self):
        target = await self.interrupted_after_first_import()
        self.client.documents[target]['script']['device']['serial'] = 'different-synthetic-device'
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('import'))
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.report()['applied'])

    async def test_resume_checks_every_existing_partial_before_any_put(self):
        self.write_source('a', self.raw)
        await self.interrupted_after_first_import()
        second, _ = FakeConverter().convert(self.raw, 'XY-a')
        second['chess']['scheduler']['enable'] = False
        second['kekkai_utilize']['utilize_config']['min_taiko_value'] = 61
        self.client.documents['XY-a'] = second
        self.client.names.append('XY-a')
        report = self.report()
        report['imported'].append({'target': 'XY-a', 'attempted': True,
                                   'confirmed_imported': True, 'verified': False, 'state': None})
        self.save_report(report)
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('import'))
        self.assertFalse(self.operations('set_value'))

    async def test_resume_rejects_unexpected_enabled_task(self):
        target = await self.interrupted_after_first_import()
        self.client.documents[target]['kekkai_utilize']['scheduler']['enable'] = True
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('import'))
        self.assertFalse(self.operations('set_value'))

    async def test_resume_accepts_already_restored_expected_enable_without_post(self):
        target = await self.interrupted_after_first_import()
        self.client.documents[target]['chess']['scheduler']['enable'] = True
        await self.migrate(resume=True)
        self.assertFalse(self.operations('import'))
        self.assertTrue(self.report()['applied'])
        self.assert_originals_unchanged()

    async def test_resume_missing_attempted_target_may_create_it_again(self):
        target = await self.interrupted_after_first_import()
        del self.client.documents[target]
        self.client.names.remove(target)
        await self.migrate(resume=True)
        self.assertEqual(self.operations('import'), [('import', target)])
        self.assertTrue(self.report()['applied'])

    async def test_resume_rejects_changed_plan_and_original_account_list(self):
        await self.interrupted_after_first_import()
        report = self.report()
        report['accounts'][0]['target'] = 'XY-unrelated'
        self.save_report(report)
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.operations('import'))

    async def test_resume_rejects_added_original_account(self):
        await self.interrupted_after_first_import()
        self.client.names.append('unrelated-new-original')
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.operations('import'))

    async def test_resume_rejects_finished_or_missing_report(self):
        with self.assertRaises((RuntimeError, ValueError)):
            await self.migrate(resume=True)
        await self.migrate()
        self.client.calls.clear()
        with self.assertRaises(RuntimeError):
            await self.migrate(resume=True)
        self.assertFalse(self.operations('set_value'))
        self.assertFalse(self.operations('import'))


if __name__ == '__main__':
    unittest.main()
