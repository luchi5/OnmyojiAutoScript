"""Durable count of fully settled series, never individual battles."""
import json
from pathlib import Path
import uuid


class SeriesBatch:
    def __init__(self, path, target, legacy_checkpoint):
        self.path, self.legacy = Path(path), Path(legacy_checkpoint)
        self.target = int(target)
        if self.target < 1:
            raise ValueError('At least one complete series is required')
        self.state = None
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not data.get('finished'):
                self.state = data
                self.state['target'] = self.target

    @property
    def pending(self):
        return self.state is not None and not self.state.get('finished')

    @property
    def completed(self):
        return len(self.state['results']) if self.state else 0

    @property
    def checkpoint(self):
        if not self.state or not self.state.get('checkpoint'):
            return self.legacy
        path = (self.path.parent/self.state['checkpoint']).resolve()
        if self.path.parent.resolve() not in path.parents:
            raise ValueError('Batch checkpoint must stay in the pair directory')
        return path

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def begin(self, adopt=False):
        if self.state is None:
            self.state = dict(version=1, batch_id=uuid.uuid4().hex, target=self.target,
                              results=[], checkpoint=None, finished=False)
        if self.completed >= self.target:
            self.save()
            return False
        if not self.state['checkpoint']:
            self.state['checkpoint'] = (self.legacy.name if adopt and self.completed == 0 else
                'rounds/%s-%d/checkpoint.json' % (self.state['batch_id'], self.completed+1))
        self.save()
        return True

    def settle(self, result):
        if not self.state.get('checkpoint'):
            raise ValueError('No active series to settle')
        self.state['results'].append(dict(result, series_number=self.completed+1))
        self.state['checkpoint'] = None
        self.save()

    def finish(self):
        if self.completed < self.target:
            raise ValueError('Cannot finish before the configured series count')
        self.state['finished'] = True
        self.save()
