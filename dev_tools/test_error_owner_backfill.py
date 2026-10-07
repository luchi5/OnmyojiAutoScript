import json
from pathlib import Path
import tempfile
import unittest

from dev_tools.backfill_error_owners import backfill


class ErrorOwnerBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'log' / 'error').mkdir(parents=True)
        for name in ('demo-account-001', '04-托管'):
            (self.root / 'config' / f'{name}.json').write_text('{}')
        self.record = self.root / 'log' / 'error' / '1791282152472'
        self.record.mkdir()

    def source(self, account, text):
        (self.root / 'log' / f'2026-10-06_{account}.txt').write_text(text, encoding='utf-8')

    def test_exact_reference_assigns_account_and_task_without_guessing(self):
        self.source('demo-account-001', 'Scheduler: Start task `Delegation`\n'
                    'Saving error: ./log/error/1791282152472\n')
        self.source('04-托管', 'Game error around the same time\n')
        result = backfill(self.root, apply=True)
        owner = json.loads((self.record / 'metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(owner['config_name'], 'demo-account-001')
        self.assertEqual(owner['task'], 'Delegation')
        self.assertEqual(len(result['assigned']), 1)

    def test_conflicting_or_unregistered_logs_cannot_assign_account(self):
        line = 'Saving error: ./log/error/1791282152472\n'
        self.source('demo-account-001', line)
        self.source('04-托管', line)
        result = backfill(self.root, apply=True)
        self.assertEqual(result['conflicts'], [self.record.name])
        self.assertFalse((self.record / 'metadata.json').exists())
        (self.root / 'log' / '2026-10-06_demo-account-001.txt').unlink()
        (self.root / 'log' / '2026-10-06_04-托管.txt').unlink()
        self.source('01', line)
        self.assertEqual(backfill(self.root, apply=True)['unknown'], [self.record.name])

    def test_dry_run_preserves_files_and_existing_metadata_is_not_overwritten(self):
        self.source('demo-account-001', 'Saving error: ./log/error/1791282152472\n')
        self.assertEqual(len(backfill(self.root)['assigned']), 1)
        self.assertFalse((self.record / 'metadata.json').exists())
        saved = '{"version":1,"config_name":"04-托管"}'
        (self.record / 'metadata.json').write_text(saved, encoding='utf-8')
        self.assertEqual(backfill(self.root, apply=True)['existing_metadata'], [self.record.name])
        self.assertEqual((self.record / 'metadata.json').read_text(encoding='utf-8'), saved)


if __name__ == '__main__':
    unittest.main()
