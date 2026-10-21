"""Migrate a frozen XY snapshot into the fixed private backend, without starting tasks.

Only new XY-prefixed names are accepted. Original account files are never edited.
Full source and destination backups remain outside the Git checkout.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys

PRIVATE_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = Path(r'C:\Onmyoji\migration-records').resolve()
PANEL_ROOT = Path(r'C:\Onmyoji\OAS-WebPanel')
sys.path.insert(0, str(PANEL_ROOT))
from oas_client import OasClient


def target_name(source):
    name = 'XY-' + re.sub(r'[^A-Za-z0-9_\-\u4e00-\u9fff]', '-', source)
    if len(name) > 64 or not re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]+', name):
        raise ValueError('迁移配置名称不符合私库规则')
    return name


def enabled_tasks(config):
    return [name for name, value in config.items() if isinstance(value, dict)
            and isinstance(value.get('scheduler'), dict)
            and value['scheduler'].get('enable') is True]


def task_name(key):
    return ''.join(part[:1].upper() + part[1:] for part in key.split('_'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_originals(backup):
    before = backup / 'private-before'
    for original in before.glob('*.json'):
        live = PRIVATE_ROOT / 'config' / original.name
        if not live.is_file() or digest(live) != digest(original):
            raise RuntimeError('现有私库配置已经改变；请重新备份后再迁移')


def check_frontend():
    path = Path(r'C:\Onmyoji\OASX-AzurTian\profile-azurtian\GetStorage.gs')
    settings = json.loads(path.read_text(encoding='utf-8-sig'))
    automatic = settings.get('autoScriptList', [])
    if isinstance(automatic, str):
        automatic = json.loads(automatic)
    if automatic != [] or settings.get('address') != '127.0.0.1:22289':
        raise RuntimeError('私库前端连接或自动运行设置已改变；迁移没有继续')


def read_plan(backup):
    from xy_config_migration import convert_xy_config
    entries = []
    sources = sorted((backup / 'xy-original').glob('*.json'))
    if not sources or any(path.stem == 'template' for path in sources):
        raise ValueError('请选择只包含账号配置的冻结快照')
    for source in sources:
        raw = json.loads(source.read_text(encoding='utf-8-sig'))
        name = target_name(source.stem)
        converted, details = convert_xy_config(raw, name)
        if converted.get('config_name') != name or converted.get('running_task'):
            raise ValueError('转换后的配置身份或运行标记不正确')
        entries.append({'source': source.stem, 'target': name, 'config': converted,
                        'enabled': enabled_tasks(converted), 'details': details})
    if len({entry['target'].casefold() for entry in entries}) != len(entries):
        raise ValueError('迁移后的配置名称重复')
    return entries


def normalized_config(data):
    from xy_config_migration import Converter
    return Converter().service._validated(data)


async def observe_state(client, name):
    """Read process state without requiring a schedule from a disabled account."""
    result = {'connected': False, 'state': None}
    try:
        async with client.connection(name) as ws:
            async def read_state():
                while True:
                    event = client.event(await ws.recv())
                    if event['type'] == 'state':
                        result.update(connected=True, state=event['state'])
                        return
            await asyncio.wait_for(read_state(), 5)
    except Exception:
        # A received state remains authoritative even if the server closes while
        # computing the empty schedule. No commands are sent on this connection.
        pass
    return result


def verify_partial_config(saved, expected):
    """Only disabled/partially restored flags may differ after an interrupted import."""
    actual = normalized_config(saved)
    wanted = normalized_config(expected)
    for key, value in wanted.items():
        if not isinstance(value, dict) or not isinstance(value.get('scheduler'), dict):
            continue
        scheduler = actual.get(key, {}).get('scheduler', {})
        actual_enable = scheduler.get('enable')
        intended_enable = value['scheduler'].get('enable')
        if type(actual_enable) is not bool or (actual_enable and not intended_enable):
            raise RuntimeError('恢复配置包含计划外的任务开关；没有继续修改')
        scheduler['enable'] = intended_enable
    if actual != wanted:
        raise RuntimeError('恢复配置参数已经改变；没有覆盖或继续修改')


async def run(backup, apply=False, resume=False):
    if PRIVATE_ROOT.resolve() != Path(r'C:\Onmyoji\OnmyojiAutoScript-runhey').resolve():
        raise RuntimeError('此工具只允许用于当前私库目录')
    backup = backup.resolve()
    if backup.parent != MIGRATIONS or not (backup / 'private-before').is_dir():
        raise ValueError('备份目录必须位于项目迁移记录目录中')
    check_frontend()
    verify_originals(backup)
    entries = read_plan(backup)
    client = OasClient('http://127.0.0.1:22289')
    await client.transfer_capabilities()
    existing = await client.accounts()
    account_plan = [
        {'source': entry['source'], 'target': entry['target'],
         'enabled_tasks': entry['enabled'], 'details': entry['details']}
        for entry in entries]
    report_path = backup / 'migration-report.json'
    targets = {entry['target'].casefold() for entry in entries}
    if resume:
        if not report_path.is_file():
            raise RuntimeError('没有可恢复的迁移记录')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if not isinstance(report, dict) or report.get('applied') is not False or report.get('accounts') != account_plan:
            raise RuntimeError('迁移记录已经完成或与当前计划不一致；没有继续修改')
        previous = report.get('imported')
        if not isinstance(previous, list) or any(not isinstance(item, dict) for item in previous):
            raise RuntimeError('迁移记录格式不正确')
        attempted = {item.get('target'): item for item in previous if item.get('attempted') is True}
        if (len(attempted) != len(previous) or any(not isinstance(name, str) or name.casefold() not in targets for name in attempted)
                or report.get('original_accounts') != [name for name in existing if name.casefold() not in targets]):
            raise RuntimeError('现有账号与迁移记录不一致；没有覆盖或继续修改')
        for entry in entries:
            name = entry['target']
            matches = [item for item in existing if item.casefold() == name.casefold()]
            if matches and (matches != [name] or name not in attempted):
                raise RuntimeError('同名账号并非本次已记录的导入；没有覆盖或重复导入')
        # Validate every existing partial import before restoring any flags.
        for entry in entries:
            if entry['target'] in existing:
                state = await observe_state(client, entry['target'])
                if not state['connected'] or state['state'] != 0:
                    raise RuntimeError('恢复账号不是停止状态；没有继续修改')
                saved = await client.transfer_export(entry['target'], 'backup')
                verify_partial_config(saved, entry['config'])
        report.pop('failure', None)
    else:
        if any(entry['target'].casefold() in {name.casefold() for name in existing}
               for entry in entries):
            raise RuntimeError('同名迁移账号已经存在；没有覆盖或重复导入')
        report = {'applied': False, 'original_accounts': list(existing),
                  'accounts': account_plan, 'imported': []}
    # Confirm every task before importing any account.
    menu = await client.menu()
    commands = {name for tasks in menu.values() for name in tasks}
    if any(task_name(key) not in commands for entry in entries for key in entry['enabled']):
        raise RuntimeError('迁移排程含当前后台未注册的任务')
    def save_report():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save_report()
    if not apply:
        print(json.dumps({'validated_accounts': len(entries), 'apply': False,
                          'report': str(report_path)}, ensure_ascii=False))
        return
    check_frontend()
    verify_originals(backup)
    stage, name = 'before_import', None
    try:
        for entry in entries:
            name = entry['target']
            # Record attempts before a request: timeout does not prove no write.
            stage = 'import_config'
            item = next((item for item in report['imported'] if item['target'] == name), None)
            if item is None:
                item = {'target': name, 'attempted': True, 'confirmed_imported': False,
                        'verified': False, 'state': None}
                report['imported'].append(item)
            item.update(verified=False, state=None)
            save_report()
            # The backend creates a stopped instance with all schedules disabled first.
            if name not in existing:
                await client.transfer_import(name, json.dumps(entry['config'], ensure_ascii=False))
                existing.append(name)
            item['confirmed_imported'] = True
            save_report()
            stage = 'confirm_stopped'
            state = await observe_state(client, name)
            if not state['connected'] or state['state'] != 0:
                raise RuntimeError('新迁入配置不是停止状态；没有恢复后续排程')
            # Restore selected tasks, not account execution or frontend automatic starts.
            for key in entry['enabled']:
                stage = 'restore_schedule:' + key
                state = await observe_state(client, name)
                if not state['connected'] or state['state'] != 0:
                    raise RuntimeError('迁入配置状态已改变；已暂停后续参数恢复')
                await client.set_value(name, task_name(key), 'scheduler', 'enable', True, 'boolean')
            stage = 'verify_saved'
            saved = await client.transfer_export(name, 'backup')
            if normalized_config(saved) != normalized_config(entry['config']):
                raise RuntimeError('迁入配置回读与转换结果不一致；请查看记录，不要重复导入')
            stage = 'verify_state'
            state = await observe_state(client, name)
            if not state['connected'] or state['state'] != 0:
                raise RuntimeError('迁入配置检查时已不再停止；请查看记录')
            item.update(verified=True, state=0)
            save_report()
        stage, name = 'verify_originals', None
        verify_originals(backup)
        check_frontend()
        stage = 'verify_accounts'
        names = await client.accounts()
        if any(entry['target'] not in names for entry in entries):
            raise RuntimeError('后台未列出部分迁入配置')
    except Exception as exc:
        report['failure'] = {'stage': stage, 'target': name, 'type': type(exc).__name__}
        save_report()
        raise
    report['applied'] = True
    report['total_accounts_after'] = len(names)
    save_report()
    print(json.dumps({'imported': len(entries), 'all_stopped': True,
                      'originals_unchanged': True, 'total_accounts': len(names),
                      'report': str(report_path)}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.snapshot, args.apply, args.resume))
    except Exception as exc:
        # Avoid tracebacks containing configuration values or transport data.
        print(f'迁移未完成：{type(exc).__name__}：{exc}', file=sys.stderr)
        sys.exit(1)
