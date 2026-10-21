from pathlib import Path
import tempfile
import unittest

from tasks.ActivityShikigami.boss_progress import BossProgressStore


class BossProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = BossProgressStore(self.directory.name,'01-自己')

    def test_pending_and_confirmed_survive_restart(self):
        self.store.save(70,71)
        reloaded=BossProgressStore(self.directory.name,'01-自己')
        self.assertEqual(reloaded.load()['confirmed'],70)
        self.assertEqual(reloaded.load()['expected_after_settlement'],71)
        reloaded.save(71)
        self.assertEqual(self.store.load()['confirmed'],71)
        self.assertIsNone(self.store.load()['expected_after_settlement'])
        self.assertEqual(list(Path(self.directory.name).glob('*.tmp')),[])

    def test_account_records_are_isolated(self):
        self.store.save(71)
        other=BossProgressStore(self.directory.name,'02-测试')
        self.assertIsNone(other.load())
        other.save(20)
        self.assertEqual(self.store.load()['confirmed'],71)

    def test_unknown_and_out_of_range_are_not_persisted(self):
        for completed,pending in [(None,None),(True,None),(-1,None),(101,None),(70,72),(100,101)]:
            with self.assertRaises(ValueError): self.store.save(completed,pending)
        self.assertFalse(self.store.path.exists())

    def test_corrupt_or_foreign_records_are_rejected(self):
        self.store.path.write_text('not JSON',encoding='utf-8')
        with self.assertRaises(ValueError): self.store.load()
        self.store.path.write_text('{"account":"another"}',encoding='utf-8')
        with self.assertRaises(ValueError): self.store.load()

    def test_anchor_plus_wins_survive_restart_without_new_ocr(self):
        self.store.save(90,counted=99)
        record=BossProgressStore(self.directory.name,'01-自己').load()
        self.assertEqual((record['confirmed'],record['counted']),(90,99))
        self.store.save(90,counted=100)
        self.assertEqual(self.store.load()['counted'],100)

    def test_unknown_ocr_can_store_actual_wins_without_inventing_an_observation(self):
        self.store.save(None,counted=6)
        record=self.store.load()
        self.assertIsNone(record['confirmed'])
        self.assertEqual(record['counted'],6)

    def test_legacy_checkpoint_uses_confirmed_settlement_as_counted_progress(self):
        import json
        self.store.path.write_text(json.dumps(dict(schema_version=1,event='202610_boss',
            account='01-自己',confirmed=70,expected_after_settlement=71)),encoding='utf-8')
        self.assertEqual(self.store.load()['counted'],71)

    def test_counted_progress_cannot_be_lower_than_observation_or_above_100(self):
        for counted in (89,101,-1,True):
            with self.assertRaises(ValueError):self.store.save(90,counted=counted)


if __name__=='__main__': unittest.main()
