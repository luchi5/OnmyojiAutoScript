"""Plan the visible guild settings; apply only with private runtime stopped."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import subprocess

from tasks.Dokan.config import Dokan
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from tasks.Component.collective_missions_link import LINK_IDLE_TARGET


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT.parent / 'migration-records/guild-visible-settings-20261006'


def migrated_settings(data):
    """Change only new settings and the linked queue, preserving all other data."""
    result = deepcopy(data)
    for key, group, cls in (
        ('dokan', 'dokan_config', Dokan),
        ('guild_banquet', 'guild_banquet_time', GuildBanquet),
        ('demon_retreat', 'demon_retreat_time', DemonRetreat),
    ):
        task = result.get(key)
        if not isinstance(task, dict):
            continue
        parsed = cls(**task).model_dump()[group]
        target = task.setdefault(group, {})
        for name in ('opening_wait_minutes', 'opening_retry_interval'):
            target[name] = parsed[name]
        if key == 'dokan':
            target['dokan_run_time'] = parsed['dokan_run_time']
    task = result.get('collective_missions')
    if isinstance(task, dict):
        settings = task.setdefault('missions_config', {})
        settings.update(monday_to_thursday=True, run_after_dokan=True)
        # Do not invent a proof from a timer or start an extra game task now.
        for name in ('dokan_finished_date', 'attempted_date', 'completed_date', 'pending_kind', 'pending_until'):
            settings.setdefault(name, '')
        task['scheduler']['next_run'] = LINK_IDLE_TARGET.strftime('%Y-%m-%d %H:%M:%S')
    return result


def runtime_must_be_stopped():
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(('127.0.0.1', 22289)) == 0:
            raise RuntimeError('Stop the private backend and workers before applying new fields')
    # Any private Python worker can still save an old model and erase fields.
    import os
    executable_dir = str(ROOT / 'toolkit').replace("'", "''")
    command = ("Get-CimInstance Win32_Process | Where-Object { "
               f"$_.ProcessId -ne {os.getpid()} -and $_.ExecutablePath -and "
               f"([IO.Path]::GetDirectoryName($_.ExecutablePath) -eq '{executable_dir}') "
               "} | Select-Object -ExpandProperty ProcessId")
    processes = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                               capture_output=True, text=True, timeout=15, check=True)
    if processes.stdout.strip():
        raise RuntimeError('Private Python processes still exist; new fields were not written')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    if options.apply:
        runtime_must_be_stopped()
    paths = sorted((ROOT / 'config').glob('0*.json')) + [ROOT / 'config/template.json']
    plan = []
    for path in paths:
        if path.resolve().parent != (ROOT / 'config').resolve():
            raise RuntimeError('Configuration path is outside the private project')
        before = json.loads(path.read_text('utf-8'))
        after = migrated_settings(before)
        if options.apply:
            backup = RECORD / 'pre-apply-config' / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            path.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
        plan.append({
            'account': path.stem,
            'opening': {key: after[key][group] for key, group in
                        [('dokan', 'dokan_config'), ('guild_banquet', 'guild_banquet_time'),
                         ('demon_retreat', 'demon_retreat_time')]},
            'collective_enabled': after['collective_missions']['scheduler']['enable'],
            'collective_linked': after['collective_missions']['missions_config']['run_after_dokan'],
            'mode': 'applied' if options.apply else 'planned_only',
        })
    RECORD.mkdir(parents=True, exist_ok=True)
    name = 'settings-applied.json' if options.apply else 'settings-plan.json'
    (RECORD / name).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(paths)} configurations: {"applied" if options.apply else "planned without changes"}')


if __name__ == '__main__':
    main()
