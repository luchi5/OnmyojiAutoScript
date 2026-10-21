"""Rename regression tests using temporary accounts only; no worker/device I/O."""
import ast
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'module/server/config_manager.py').read_text(encoding='utf-8-sig'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
namespace = dict(Path=Path, ExitStack=ExitStack, FileLock=FileLock, logger=Mock())
exec(compile(ast.Module(body=[cls], type_ignores=[]), '<ConfigManager>', 'exec'), namespace)
Manager = namespace['ConfigManager']


class RenameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = self.root / 'config'
        self.states = self.config / 'daily_closeout'
        self.states.mkdir(parents=True)
        self.old = self.config / '09-Plus.json'
        self.new = self.config / '09-MAX.json'
        self.old.write_bytes(b'{"untouched": true}')
        self.before = self.states / self.old.name
        self.after = self.states / self.new.name
        ctx = patch.object(Path, 'cwd', return_value=self.root)
        ctx.start()
        self.addCleanup(ctx.stop)

    def rename(self):
        return Manager.rename('09-Plus', '09-MAX')

    def test_all_completion_and_queue_fields_preserved_byte_for_byte(self):
        state = {'date': '2026-10-18', 'completed_date': '2026-10-18',
                 'outcomes': {'AbyssShadows': {'date': '2026-10-18', 'outcome': 'completed'}},
                 'queued_date': '2026-10-18', 'queue_stage': 'intent',
                 'queued_next_run': '2026-10-18T20:00:00',
                 'manual_date': '2026-10-18', 'manual_next_run': '2026-10-18T20:01:00'}
        raw = json.dumps(state).encode()
        self.before.write_bytes(raw)
        self.assertTrue(self.rename())
        self.assertEqual(self.after.read_bytes(), raw)
        self.assertEqual(self.new.read_bytes(), b'{"untouched": true}')
        self.assertFalse(self.old.exists())
        self.assertFalse(self.before.exists())

    def test_no_existing_state(self):
        self.assertTrue(self.rename())
        self.assertFalse(self.after.exists())

    def test_target_state_conflict_does_not_merge_or_overwrite(self):
        self.before.write_bytes(b'old proof')
        self.after.write_bytes(b'unrelated target proof')
        self.assertFalse(self.rename())
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())
        self.assertEqual(self.before.read_bytes(), b'old proof')
        self.assertEqual(self.after.read_bytes(), b'unrelated target proof')

    def test_orphan_target_without_source_state_also_rejected(self):
        self.after.write_bytes(b'unrelated proof')
        self.assertFalse(self.rename())
        self.assertTrue(self.old.exists())

    def test_existing_target_config_untouched(self):
        self.new.write_bytes(b'target')
        self.assertFalse(self.rename())
        self.assertEqual(self.new.read_bytes(), b'target')

    def test_config_rename_failure_rolls_back_state(self):
        self.before.write_bytes(b'proof')
        original = Path.rename
        def fail_config(path, target):
            if path == self.old:
                raise PermissionError('injected')
            return original(path, target)
        with patch.object(Path, 'rename', fail_config):
            self.assertFalse(self.rename())
        self.assertTrue(self.old.exists())
        self.assertEqual(self.before.read_bytes(), b'proof')
        self.assertFalse(self.after.exists())
        self.assertFalse(self.new.exists())

    def test_state_move_failure_leaves_config_unchanged(self):
        self.before.write_bytes(b'proof')
        with patch.object(Path, 'rename', side_effect=PermissionError('injected')):
            self.assertFalse(self.rename())
        self.assertTrue(self.old.exists())
        self.assertTrue(self.before.exists())
        self.assertFalse(self.new.exists())

    def test_missing_source_and_unsafe_names(self):
        for old, new in [('missing', 'new'), ('09-Plus', '../escape'), ('../escape', 'new')]:
            self.assertFalse(Manager.rename(old, new))
        self.assertTrue(self.old.exists())


if __name__ == '__main__':
    unittest.main()
