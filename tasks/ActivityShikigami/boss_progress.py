"""Persist observed progress and wins counted after a reliable observation."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import uuid


class BossProgressStore:
    def __init__(self, directory, account):
        self.account = account
        key = hashlib.sha256(account.encode('utf-8')).hexdigest()[:24]
        self.path = Path(directory) / (key + '.json')

    def load(self):
        if not self.path.exists():
            return None
        record = json.loads(self.path.read_text(encoding='utf-8'))
        if (record.get('account') != self.account
                or record.get('event') != '202610_boss'
                or record.get('schema_version') not in (1, 2)):
            raise ValueError('Wrong boss checkpoint identity/version')
        record['counted'] = self._validate(record.get('confirmed'),
            record.get('expected_after_settlement'), record.get('counted'))
        return record

    @staticmethod
    def _validate(confirmed, expected, counted=None):
        if confirmed is not None and (type(confirmed) is not int or not 0 <= confirmed <= 100):
            raise ValueError('Invalid confirmed boss progress')
        if expected is not None and (type(expected) is not int
                                     or confirmed is None or confirmed >= 100
                                     or expected != confirmed + 1):
            raise ValueError('Expected progress must be exactly one confirmed settlement ahead')
        if counted is None and confirmed is not None:
            counted = max(confirmed, expected or 0)
        if (type(counted) is not int or not 0 <= counted <= 100
                or counted < max(confirmed or 0, expected or 0)):
            raise ValueError('Invalid counted boss progress')
        return counted

    def save(self, confirmed, expected=None, *, counted=None):
        counted = self._validate(confirmed, expected, counted)
        record = dict(schema_version=2, event='202610_boss', account=self.account,
                      confirmed=confirmed, expected_after_settlement=expected,
                      counted=counted,
                      updated_at=datetime.now(timezone.utc).isoformat())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return record
