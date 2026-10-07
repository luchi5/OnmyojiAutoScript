"""Assign legacy archives only from exact references in original account logs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


ACCOUNT_LOG = re.compile(r"^\d{4}-\d{2}-\d{2}_(.+)\.txt$")
ARCHIVE_ID = re.compile(r"^[0-9]{10,17}$")
SAVED_ERROR = re.compile(r"Saving error:\s+(?:\./)?log[/\\]error[/\\]([0-9]{10,17})(?=\s|$)")
START_TASK = re.compile(r"Scheduler: Start task `([^`]+)`")


def discover_owners(log_root: Path, accounts: set[str]):
    references = defaultdict(dict)
    scanned = 0
    for source in sorted(log_root.glob('*.txt')):
        match = ACCOUNT_LOG.fullmatch(source.name)
        if not match or match[1] not in accounts or source.is_symlink():
            continue
        account, task = match[1], None
        scanned += 1
        with source.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                started = START_TASK.search(line)
                if started:
                    task = started[1]
                saved = SAVED_ERROR.search(line)
                if saved:
                    references[saved[1]][account] = {'task': task, 'source': source.name}
    return references, scanned


def backfill(root: Path, *, apply: bool = False):
    accounts = {path.stem for path in (root / 'config').glob('*.json')
                if path.stem != 'template' and not path.is_symlink()}
    references, scanned = discover_owners(root / 'log', accounts)
    report = {'applied': apply, 'logs_scanned': scanned, 'assigned': [],
              'existing_metadata': [], 'conflicts': [], 'unknown': []}
    error_root = root / 'log' / 'error'
    for folder in sorted(error_root.iterdir()):
        if folder.is_symlink() or not folder.is_dir() or not ARCHIVE_ID.fullmatch(folder.name):
            continue
        destination = folder / 'metadata.json'
        if destination.exists():
            report['existing_metadata'].append(folder.name)
            continue
        owners = references.get(folder.name, {})
        if len(owners) != 1:
            report['conflicts' if owners else 'unknown'].append(folder.name)
            continue
        account, evidence = next(iter(owners.items()))
        metadata = {'version': 1, 'config_name': account, 'task': evidence['task'],
                    'timestamp_ms': int(folder.name), 'source': 'account-log-reference',
                    'source_log': evidence['source']}
        if apply:
            try:
                # Never overwrite metadata produced by a running worker.
                with destination.open('x', encoding='utf-8') as stream:
                    json.dump(metadata, stream, ensure_ascii=False)
            except FileExistsError:
                report['existing_metadata'].append(folder.name)
                continue
        report['assigned'].append({'id': folder.name, 'config_name': account,
                                   'task': evidence['task']})
    report['counts_by_account'] = dict(Counter(row['config_name'] for row in report['assigned']))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = backfill(args.root.resolve(), apply=args.apply)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text, encoding='utf-8')
    print(json.dumps({key: len(value) if isinstance(value, list) else value
                      for key, value in result.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
