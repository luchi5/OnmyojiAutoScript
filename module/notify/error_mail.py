"""Optional Gmail error reports. Disabled by default; never imports game code.

Only structural traceback metadata crosses the child-process boundary. The
delivery child alone may read the explicitly configured Windows credential.
"""
import ctypes
from ctypes import wintypes
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
ADDRESS = 'your-account@gmail.com'
CREDENTIAL_TARGET = 'OAS/ErrorMail/Gmail/' + ADDRESS
SUBJECT_PREFIX = '[OAS-ERROR]'
MAX_FRAMES = 6
SMTP_TIMEOUT = 5
DELIVERY_TIMEOUT = 15
COOLDOWN = 900
ACCOUNT_INTERVAL = 300
HOURLY_LIMIT = 12


def _settings_enabled(root):
    try:
        value = json.loads((root / 'config' / 'error_mail' / 'settings.json').read_text(encoding='utf-8'))
        return (value.get('enabled') is True
                and value.get('sender') == ADDRESS
                and value.get('recipient') == ADDRESS
                and value.get('credential_target') == CREDENTIAL_TARGET)
    except Exception:
        return False


def _identifier(value, fallback):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value) else fallback


def build_report(config_name, task, error, root=ROOT):
    """No exception text, source lines, locals, logs, screenshots or device IDs."""
    match = re.match(r'^(\d{2})(?:-|$)', str(config_name))
    if match is None:
        return None
    account = match.group(1)
    # Task names must correspond to a project task directory, or initialization.
    task = _identifier(task, 'Unknown')
    if task != 'Initialize' and not (root / 'tasks' / task).is_dir():
        task = 'Unknown'
    frames = []
    tb = error.__traceback__
    while tb is not None:
        try:
            path = Path(tb.tb_frame.f_code.co_filename).resolve().relative_to(root.resolve())
            name = path.as_posix()
            if (name == 'script.py' or path.parts[0] in ('module', 'tasks')) and re.fullmatch(r'[A-Za-z0-9_./-]{1,160}\.py', name):
                frames.append({'file': name, 'function': _identifier(tb.tb_frame.f_code.co_name, 'unknown'),
                               'line': max(1, min(tb.tb_lineno, 999999))})
        except (ValueError, OSError):
            pass
        tb = tb.tb_next
    return {'account': account, 'task': task,
            'exception': _identifier(type(error).__name__, 'Exception'), 'frames': frames[-MAX_FRAMES:]}


def _validate_report(value):
    if not isinstance(value, dict) or set(value) != {'account', 'task', 'exception', 'frames'}:
        return False
    if not isinstance(value['account'], str) or not re.fullmatch(r'\d{2}', value['account']):
        return False
    for key in ('task', 'exception'):
        if _identifier(value[key], None) is None:
            return False
    if not isinstance(value['frames'], list) or len(value['frames']) > MAX_FRAMES:
        return False
    for frame in value['frames']:
        if not isinstance(frame, dict) or set(frame) != {'file', 'function', 'line'}:
            return False
        name = frame['file']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}\.py', name):
            return False
        parts = name.split('/')
        if '..' in parts or '.' in parts or not (name == 'script.py' or parts[0] in ('module', 'tasks')):
            return False
        if _identifier(frame['function'], None) is None or type(frame['line']) is not int or not 1 <= frame['line'] <= 999999:
            return False
    return True


def _message(report):
    message = EmailMessage()
    message['From'] = ADDRESS
    message['To'] = ADDRESS
    message['Subject'] = '{} {} {} {}'.format(SUBJECT_PREFIX, report['account'], report['task'], report['exception'])
    lines = ['Account: ' + report['account'], 'Task: ' + report['task'], 'Exception: ' + report['exception'], 'Stack:']
    lines.extend('{}:{} {}'.format(f['file'], f['line'], f['function']) for f in report['frames'])
    message.set_content('\n'.join(lines))
    return message


def _reserve(report, root, now=None):
    """Cross-process reservation: attempted deliveries consume quota too."""
    import msvcrt
    now = time.time() if now is None else now
    config = root / 'config' / 'error_mail'
    state_path = config / 'rate_state.json'
    with (config / 'rate_state.lock').open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        try:
            try:
                events = json.loads(state_path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                events = []
            # Malformed state fails closed via the delivery exception boundary.
            if not isinstance(events, list):
                return False
            events = [e for e in events if now - e['time'] < 3600]
            fingerprint = hashlib.sha256(json.dumps(report, sort_keys=True).encode('utf-8')).hexdigest()
            if len(events) >= HOURLY_LIMIT or any(
                (e['fingerprint'] == fingerprint and now - e['time'] < COOLDOWN)
                or (e['account'] == report['account'] and now - e['time'] < ACCOUNT_INTERVAL)
                for e in events
            ):
                return False
            events.append({'time': now, 'account': report['account'], 'fingerprint': fingerprint})
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=config, prefix='error_mail_', suffix='.json', delete=False) as out:
                    temporary = Path(out.name)
                    json.dump(events, out)
                os.replace(temporary, state_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return True
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


class _Credential(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def _read_password():
    # Called exclusively by the enabled delivery child, never during import/checks.
    api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    result = ctypes.POINTER(_Credential)()
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    if not api.CredReadW(CREDENTIAL_TARGET, 1, 0, ctypes.byref(result)):
        raise RuntimeError('Credential unavailable')
    try:
        credential = result.contents
        if credential.UserName != ADDRESS or credential.CredentialBlobSize != 32:
            raise RuntimeError('Credential does not match')
        password = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode('utf-16-le')
        if not re.fullmatch('[a-zA-Z]{16}', password):
            raise RuntimeError('Credential format rejected')
        return password
    finally:
        if result.contents.CredentialBlob:
            ctypes.memset(result.contents.CredentialBlob, 0, result.contents.CredentialBlobSize)
        api.CredFree(result)


def _deliver(report, root=ROOT):
    if not _settings_enabled(root):
        return 'disabled'
    if not _validate_report(report):
        return 'failed'
    try:
        if not _reserve(report, root):
            return 'skipped'
        password = _read_password()
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT, context=ssl.create_default_context()) as smtp:
                smtp.login(ADDRESS, password)
                smtp.send_message(_message(report), from_addr=ADDRESS, to_addrs=[ADDRESS])
        finally:
            password = None
        return 'sent'
    except Exception:
        # Never log SMTP exceptions, server responses or credential contents.
        return 'failed'


def notify_error_mail(config_name, task, error, root=ROOT):
    """Fail closed and bound the entire SMTP/DNS process to fifteen seconds."""
    try:
        if not _settings_enabled(root):
            return 'disabled'
        report = build_report(config_name, task, error, root)
        if report is None or not _validate_report(report):
            return 'skipped'
        # No password in argv, environment, stdin or the parent game process.
        completed = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve()), '--deliver'],
            input=json.dumps(report).encode('utf-8'), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=DELIVERY_TIMEOUT, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return {0: 'sent', 2: 'skipped', 3: 'disabled'}.get(completed.returncode, 'failed')
    except Exception:
        return 'failed'


def _test_status(root, status=None):
    path = root / 'config' / 'error_mail' / 'test_status.json'
    if status is None:
        return json.loads(path.read_text(encoding='utf-8'))['status']
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'status': status, 'updated_utc_epoch': time.time()}), encoding='utf-8')
    os.replace(temporary, path)
    return status


def _test_worker(root=ROOT):
    """One synthetic SMTP message, only after explicit local credential saving."""
    phase = 'failed_before_send'
    client = None
    password = None
    try:
        saved = json.loads((root / 'config' / 'error_mail' / 'setup_status.json').read_text(encoding='utf-8-sig'))
        if saved.get('status') != 'saved' or _test_status(root) != 'starting':
            return 'failed_before_send'
        _test_status(root, 'authenticating')
        password = _read_password()
        client = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
        client.login(ADDRESS, password)
        password = None
        message = EmailMessage()
        message['From'] = ADDRESS
        message['To'] = ADDRESS
        message['Subject'] = SUBJECT_PREFIX + ' TEST - local SMTP setup'
        message.set_content('This is the single approved OAS local SMTP setup test.\nSynthetic test only. No real logs, screenshots, account or device data.\nReal error email remains disabled.\n')
        _test_status(root, 'submitting')
        phase = 'uncertain'
        refused = client.send_message(message, from_addr=ADDRESS, to_addrs=[ADDRESS])
        phase = 'rejected' if refused else 'accepted'
        _test_status(root, phase)
    except Exception:
        try:
            _test_status(root, phase)
        except Exception:
            pass
    finally:
        password = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return phase


def run_test_once(root=ROOT):
    """Manual-only test. A prior/uncertain attempt is never automatically retried."""
    try:
        saved = json.loads((root / 'config' / 'error_mail' / 'setup_status.json').read_text(encoding='utf-8-sig'))
        if saved.get('status') != 'saved':
            return 'awaiting_local_save'
    except Exception:
        return 'awaiting_local_save'
    path = root / 'config' / 'error_mail' / 'test_status.json'
    try:
        with path.open('x', encoding='utf-8') as out:
            json.dump({'status': 'starting', 'updated_utc_epoch': time.time()}, out)
    except FileExistsError:
        return 'already_attempted_no_retry'
    except Exception:
        return 'failed_before_send'
    try:
        subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--test-worker'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=DELIVERY_TIMEOUT, check=False,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass
    try:
        status = _test_status(root)
        if status in ('accepted', 'rejected', 'failed_before_send', 'uncertain'):
            return status
        return _test_status(root, 'uncertain' if status == 'submitting' else 'failed_before_send')
    except Exception:
        return 'uncertain'


if __name__ == '__main__':
    if sys.argv[1:] == ['--test']:
        result = run_test_once()
        print(result)  # A fixed status only, never a server response or credential.
        raise SystemExit(0 if result == 'accepted' else 1)
    if sys.argv[1:] == ['--test-worker']:
        raise SystemExit(0 if _test_worker() == 'accepted' else 1)
    status = 'failed'
    if sys.argv[1:] == ['--deliver']:
        try:
            raw = sys.stdin.buffer.read(8193)
            if len(raw) <= 8192:
                status = _deliver(json.loads(raw.decode('utf-8')))
        except Exception:
            pass
    raise SystemExit({'sent': 0, 'skipped': 2, 'disabled': 3}.get(status, 1))
