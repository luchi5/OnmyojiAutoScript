"""Local daily screenshots and honest, deduplicated feedback notifications.

This module never opens the game or changes task schedules. Optional storage
and notification failures must not affect the account's game tasks.
"""
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from filelock import FileLock
from module.config.atomicwrites import atomic_write


ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / 'config' / 'daily_feedback'
OPTIONS_PATH = STATE_DIR / 'settings.json'
KINDS = frozenset(('talisman', 'collective'))
PUBLIC_ORIGIN = 'http://127.0.0.1:22300'
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _warn(operation):
    try:
        from module.logger import logger
        logger.warning(f'Daily feedback {operation} unavailable; game tasks continue')
    except Exception:
        pass


def _options():
    try:
        value = json.loads(OPTIONS_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def feedback_enabled(config):
    options = _options()
    accounts = options.get('accounts', [])
    return bool(options.get('enable') is True and isinstance(accounts, list)
                and getattr(config, 'config_name', None) in accounts)


def _account(config):
    name = getattr(config, 'config_name', None)
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_\-\u3400-\u9fff]+(?:\.[A-Za-z0-9_\-\u3400-\u9fff]+)*', name)
            or len(name) > 80):
        raise ValueError('Invalid daily feedback account')
    return name


def _directory(config, now):
    name = _account(config)
    key = hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]
    directory = STATE_DIR / now.date().isoformat() / key
    # Reject reparse points/symlinks in this generated local state tree.
    for path in (ROOT / 'config', STATE_DIR, directory.parent, directory):
        if path.is_symlink() or (path.exists() and path.resolve() != path.absolute()):
            raise ValueError('Unexpected feedback state path')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _load(directory, config, now):
    path = directory / 'report.json'
    if path.is_symlink():
        raise ValueError('Unexpected feedback report path')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        value = {'schema_version': 1, 'date': now.date().isoformat(),
                 'account': _account(config), 'updated_at': '', 'evidence': {},
                 'closeout': {'phase': 'provisional'},
                 'notification': {'status': 'pending', 'attempts': 0}}
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or value.get('account') != _account(config) or value.get('date') != now.date().isoformat()
            or not isinstance(value.get('evidence'), dict)):
        raise ValueError('Invalid feedback report')
    return value


def _write(directory, value, now):
    value['updated_at'] = now.isoformat(timespec='seconds')
    with atomic_write(str(directory / 'report.json'), overwrite=True, encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _valid_counter(kind, current, total, verified):
    if verified is not True or type(current) is not int or not 0 <= current <= 300:
        return None, None, False
    if total is not None and (type(total) is not int or not 1 <= total <= 300 or current > total):
        return None, None, False
    if kind == 'collective' and total != 30:
        return None, None, False
    return current, total, True


def save_evidence(config, kind, image=None, current=None, total=None,
                  verified=False, detail='', final=False, now=None, target=None):
    """Persist a positively identified fresh page; None means capture failed.

    verified means only that its number was read reliably, not that every
    daily game task was completed. A failed later attempt retains the original
    screenshot/time and exposes a separate capture error.
    """
    if kind not in KINDS or not feedback_enabled(config):
        return False
    now = now or datetime.now()
    try:
        directory = _directory(config, now)
        image_name = None
        if image is not None:
            import cv2
            import numpy as np
            if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
                    or image.ndim != 3 or image.shape[2] != 3
                    or not 100 <= image.shape[0] <= 2160 or not 100 <= image.shape[1] <= 4096):
                raise ValueError('Invalid feedback screenshot')
            # The existing device screenshot API exposes RGB pixels.
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            if not ok or encoded.nbytes > MAX_IMAGE_BYTES:
                raise ValueError('Feedback screenshot too large')
            pixels = encoded.tobytes()
            image_name = f'{kind}-{now.strftime("%H%M%S%f")}-{hashlib.sha256(pixels).hexdigest()[:12]}.png'
            image_path = directory / image_name
            if image_path.exists():
                if image_path.is_symlink() or image_path.read_bytes() != pixels:
                    raise ValueError('Feedback screenshot identity collision')
            else:
                with atomic_write(str(image_path), overwrite=False, mode='wb') as stream:
                    stream.write(pixels)
        current, total, verified = _valid_counter(kind, current, total, verified)
        target = target if verified and type(target) is int and 1 <= target <= 300 else None
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            report = _load(directory, config, now)
            previous = report['evidence'].get(kind, {})
            if image_name is None:
                evidence = dict(previous) if isinstance(previous, dict) else {}
                evidence.setdefault('status', 'unavailable')
                evidence.setdefault('image', None)
                evidence.setdefault('current', None)
                evidence.setdefault('total', None)
                evidence.setdefault('target', None)
                evidence.setdefault('verified', False)
                evidence.setdefault('captured_at', None)
                evidence['capture_error'] = str(detail)[:200]
                evidence['last_attempt_at'] = now.isoformat(timespec='seconds')
                # A previous daytime picture must not become a final picture.
                evidence.setdefault('final', False)
            else:
                evidence = {'status': 'captured', 'captured_at': now.isoformat(timespec='seconds'),
                            'image': image_name, 'current': current, 'total': total,
                            'target': target,
                            'verified': verified, 'detail': str(detail)[:200],
                            'final': bool(final), 'capture_error': ''}
            report['evidence'][kind] = evidence
            _write(directory, report, now)
        return evidence
    except Exception:
        _warn('screenshot save')
        return False


def finalize_feedback(config, now=None):
    """Archive the coordinator's real outcomes after an automatic closeout."""
    if not feedback_enabled(config):
        return False
    now = now or datetime.now()
    try:
        from tasks.Component.daily_closeout import closeout_state, closeout_summary, closeout_dependencies
        state = closeout_state(config, now)
        if state.get('unavailable') or state.get('completed_date') != now.date().isoformat():
            return False
        summary = closeout_summary(config, now)
        directory = _directory(config, now)
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            report = _load(directory, config, now)
            report['closeout'] = {'phase': 'final', 'at': now.isoformat(timespec='seconds'),
                                  'required': list(closeout_dependencies(config, now)),
                                  'waiting': [item['task'] for item in summary['incomplete']
                                              if item.get('outcome') == 'unconfirmed'],
                                  'completed': [item['task'] for item in summary['completed']],
                                  'incomplete': summary['incomplete'],
                                  'reason': summary.get('reason', '')}
            _write(directory, report, now)
        maybe_notify_feedback(config, now=now)
        return True
    except Exception:
        _warn('final report')
        return False


def _valid_public_origin(origin):
    if not isinstance(origin, str) or re.search(r'[\s\\]', origin):
        return None
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
        if (parsed.scheme in ('http', 'https') and parsed.hostname
                and parsed.username is None and parsed.password is None
                and not parsed.netloc.endswith(':')
                and parsed.path in ('', '/') and not parsed.query and not parsed.fragment):
            return origin.rstrip('/')
    except ValueError:
        pass
    return None


def _configured_public_origin():
    return _valid_public_origin(os.environ.get('OAS_PUBLIC_FEEDBACK_ORIGIN')) or PUBLIC_ORIGIN


def _feedback_url(config, day):
    # The URL contains an account/date, never any push credentials.
    origin = _valid_public_origin(_options().get('public_origin')) or _configured_public_origin()
    return origin.rstrip('/') + '/daily-feedback?' + urlencode({'account': _account(config), 'date': day})


def _notification_content(config, report):
    evidence = report['evidence']
    parts = []
    for kind, label in (('talisman', '花合战今日经验'), ('collective', '寮集体任务')):
        task = getattr(config, 'talisman_pass' if kind == 'talisman' else 'collective_missions', None)
        if task is None or not getattr(task.scheduler, 'enable', False):
            continue
        if kind == 'collective':
            missions = task.missions_config
            if (getattr(missions, 'monday_to_thursday', False) or getattr(missions, 'run_after_dokan', False)) and datetime.fromisoformat(report['date']).weekday() >= 4:
                continue
        item = evidence.get(kind, {})
        if item.get('status') == 'captured' and item.get('verified') is True:
            value = str(item.get('current'))
            if item.get('total') is not None:
                value += '/' + str(item['total'])
            if kind == 'talisman' and item.get('target') is not None:
                value += f'（目标{item["target"]}）'
            parts.append(f'{label}：{value}（数字已核验）')
        elif item.get('image'):
            parts.append(f'{label}：已截图，数字未核验')
        else:
            parts.append(f'{label}：未获取反馈图')
    incomplete = report.get('closeout', {}).get('incomplete', [])
    collective = evidence.get('collective', {})
    if (collective.get('status') == 'captured' and collective.get('verified') is True
            and collective.get('final') is True and collective.get('current') == 30
            and collective.get('total') == 30):
        incomplete = [item for item in incomplete if item.get('task') != 'CollectiveMissions']
    if incomplete:
        outcome_labels = {'unconfirmed': '未核验', 'failed': '未完成', 'expired': '等待超时',
                          'skipped': '已跳过', 'blocked_by_dokan': '等待道馆完成'}
        parts.append('需关注：' + '、'.join(str(item.get('label', '未确认项目')) + '（' +
                     outcome_labels.get(item.get('outcome'), '未核验') + '）' for item in incomplete))
    parts.append('截图时间见验收页；活跃度不代表每项日常均完成。')
    return '\n'.join(parts)


def _push(config, title, content, url):
    # Clone the account notifier so this report cannot mutate its usual error
    # messages. Bound the request locally; OnePush's default has no timeout.
    import requests
    notifier = copy.deepcopy(config.notifier)
    if not notifier.enable or str(notifier.provider_name).lower() != 'bark':
        return None
    notifier.notifier.request = lambda method, endpoint, **kwargs: requests.request(
        method, endpoint, timeout=(3, 5), **kwargs)
    return notifier.push(title=title, content=content, url=url,
                         group='OAS每日验收', isarchive=1)


def maybe_notify_feedback(config, now=None):
    """At most three bounded attempts, with persistent three-minute backoff."""
    if not feedback_enabled(config):
        return False
    now = now or datetime.now()
    try:
        directory = _directory(config, now)
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            report = _load(directory, config, now)
            if report.get('closeout', {}).get('phase') != 'final':
                return False
            if report.get('capture_only_next_run'):
                return False
            notice = report.get('notification', {})
            if notice.get('status') in ('accepted', 'disabled', 'sending') or notice.get('attempts', 0) >= 3:
                return False
            at = notice.get('at')
            if at and now - datetime.fromisoformat(at) < timedelta(minutes=3):
                return False
            if _options().get('notify') is not True:
                report['notification'] = {'status': 'disabled', 'at': now.isoformat(timespec='seconds'),
                                           'attempts': notice.get('attempts', 0)}
                _write(directory, report, now)
                return False
            attempt = notice.get('attempts', 0) + 1
            report['notification'] = {'status': 'sending', 'at': now.isoformat(timespec='seconds'), 'attempts': attempt}
            _write(directory, report, now)
        content = _notification_content(config, report)
        try:
            result = _push(config, '每日验收反馈', content, _feedback_url(config, report['date']))
        except Exception:
            result = False
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            latest = _load(directory, config, now)
            if latest.get('notification', {}).get('attempts') != attempt:
                return False
            latest['notification'] = {'status': 'accepted' if result is True else 'disabled' if result is None else 'failed',
                                       'at': now.isoformat(timespec='seconds'), 'attempts': attempt}
            _write(directory, latest, now)
        return result is True
    except Exception:
        _warn('notification')
        return False


def consume_recapture_request(config, scheduled_at, now=None):
    """Claim an exact, same-day capture-only request without claiming rewards.

    Ordinary phone flash/manual TalismanPass requests retain their reward
    behavior. Keep its marker through normal game-restart recovery; only
    finish_recapture_request clears it after the capture run actually returns.
    """
    if not feedback_enabled(config):
        return False
    now = now or datetime.now()
    try:
        scheduled = datetime.fromisoformat(str(scheduled_at))
        if scheduled.date() != now.date() or not now - timedelta(minutes=15) <= scheduled <= now:
            return False
        directory = _directory(config, now)
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            report = _load(directory, config, now)
            marker = report.get('capture_only_next_run')
            if not marker or datetime.fromisoformat(marker) != scheduled:
                return False
            report.setdefault('capture_only_consumed_at', now.isoformat(timespec='seconds'))
            _write(directory, report, now)
        return True
    except Exception:
        _warn('capture request')
        return False


def finish_recapture_request(config, scheduled_at, now=None):
    if not feedback_enabled(config):
        return False
    now = now or datetime.now()
    try:
        scheduled = datetime.fromisoformat(str(scheduled_at))
        if scheduled.date() != now.date():
            return False
        directory = _directory(config, now)
        with FileLock(str(directory / 'report.json') + '.lock', timeout=5):
            report = _load(directory, config, now)
            marker = report.get('capture_only_next_run')
            if not marker or datetime.fromisoformat(marker) != scheduled:
                return False
            report['capture_only_next_run'] = ''
            report['capture_only_finished_at'] = now.isoformat(timespec='seconds')
            _write(directory, report, now)
        return True
    except Exception:
        _warn('capture completion')
        return False
