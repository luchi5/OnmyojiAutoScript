"""Persist one automatic session reclaim per 30-minute window, per device."""
import hashlib
import json
import math
import os
import time
from pathlib import Path
from filelock import FileLock


def claim_recovery(account, root=Path('config'), now=None):
    now = time.time() if now is None else now
    config = json.loads((root / (account + '.json')).read_text(encoding='utf-8-sig'))
    serial = config['script']['device']['serial']
    if not isinstance(serial, str) or not serial.strip():
        raise ValueError('Missing device identity')
    folder = root / 'account_session_recovery'
    folder.mkdir(exist_ok=True)
    path = folder / (hashlib.sha256(serial.encode()).hexdigest() + '.json')
    with FileLock(str(path) + '.lock', timeout=5):
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            last = record['last_attempt']
            if type(last) not in (int, float) or not math.isfinite(last):
                raise ValueError('Invalid recovery time')
            # Clock rollback and the exact 30-minute boundary remain blocked.
            if now - last <= 1800:
                return False
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'account': account, 'last_attempt': now}), encoding='utf-8')
        os.replace(temporary, path)
    return True
