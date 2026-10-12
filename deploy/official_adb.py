"""Install the pinned Google Windows ADB bundle; never control an ADB server."""
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import urllib.request
import uuid
import zipfile

VERSION = '37.0.1'
ARCHIVE_URL = 'https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip'
ARCHIVE_SIZE = 8044989
ARCHIVE_SHA256 = '45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99'
# DLLs first, executable last: a new executable only appears with all its DLLs.
FILE_HASHES = {
    'AdbWinApi.dll': 'c1d653030b4bde65d3e07e4d0b0979e17be56df1436cdd15528630f27808050d',
    'AdbWinUsbApi.dll': '0710e894d9b40f71a670c13c694079d564c92c1279da382cfe4850983aaebe1b',
    'libwinpthread-1.dll': 'f2044c755e39efc5d47f47c5a942178dd6d79dc8945bb182dc8753d10e2a4269',
    'adb.exe': 'b4a6b455702684652cccf7b46258b29e653538904359a58fd4931cf3ef286b3f',
}
_ANY_PREIMAGE = object()


class OfficialAdbError(RuntimeError):
    pass


def _safe(path, file=False):
    """Reject redirected directories and shared files before reading/writing."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OfficialAdbError('ADB path contains a link/reparse point: ' + str(part))
        if part == path and file and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise OfficialAdbError('ADB file is not a private regular file: ' + str(part))
    return path


def _hash(path):
    path = _safe(path, file=True)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, data, expected_fingerprint=_ANY_PREIMAGE):
    """Write a complete sibling, fsync, then replace without truncating a file."""
    path = _safe(path, file=True)
    _safe(path.parent).mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path, file=True)
        if expected_fingerprint is not _ANY_PREIMAGE and _fingerprint(path) != expected_fingerprint:
            raise OfficialAdbError('Preserving a changed ADB target before atomic replacement: ' + path.name)
        _replace(temporary, path)
    finally:
        if temporary.exists():
            _safe(temporary, file=True).unlink()


def _replace(source, target):
    if os.name != 'nt':
        os.replace(source, target)
        return
    # MoveFileEx (os.replace) cannot replace a guarded open Windows file. The
    # Windows 10 rename API supports POSIX replacement: old guard handles remain
    # on the old inode while the complete new file is published at the path.
    name = str(Path(target).absolute())
    name_bytes = name.encode('utf-16-le')

    class RenameInfo(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * (len(name_bytes) // 2 + 1))]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(source), 0x10000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = RenameInfo()
        info.Flags = 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        info.FileNameLength = len(name_bytes)
        info.FileName = name
        if not kernel.SetFileInformationByHandle(handle, 22, ctypes.byref(info),
                                                  RenameInfo.FileName.offset + info.FileNameLength):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _json(path, value):
    _write(path, (json.dumps(value, indent=2) + '\n').encode('utf-8'))


@contextlib.contextmanager
def _install_lock(directory):
    path = _safe(directory / '.official-adb-install.lock', file=True)
    token = uuid.uuid4().hex
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise OfficialAdbError('Another ADB installation is locked; retry after it finishes.') from exc
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps({'owner_token': token, 'version': VERSION}).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        # Never steal an older lock or remove a different installer's lock.
        try:
            owner = json.loads(_safe(path, file=True).read_text(encoding='utf-8'))
            if owner.get('owner_token') == token:
                path.unlink()
        except (FileNotFoundError, ValueError):
            pass


def _download():
    request = urllib.request.Request(ARCHIVE_URL, headers={'User-Agent': 'OAS-ADB-Installer'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.geturl() != ARCHIVE_URL:
            raise OfficialAdbError('Unexpected redirect while downloading official ADB.')
        chunks, size = [], 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > ARCHIVE_SIZE:
                raise OfficialAdbError('Official ADB archive exceeds its pinned size.')
            chunks.append(chunk)
    return b''.join(chunks)


def _unpack(data):
    if len(data) != ARCHIVE_SIZE or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise OfficialAdbError('Official ADB archive checksum/size mismatch; no binaries changed.')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            required = ['platform-tools/' + name for name in FILE_HASHES]
            required += ['platform-tools/source.properties', 'platform-tools/NOTICE.txt']
            if any(names.count(name) != 1 for name in required):
                raise OfficialAdbError('Official ADB archive has missing or duplicate files.')
            # Read only explicit members; do not extract paths supplied by the ZIP.
            if any(archive.getinfo(name).file_size > 16 * 1024 * 1024 for name in required):
                raise OfficialAdbError('Official ADB member is unexpectedly large.')
            properties = archive.read('platform-tools/source.properties').decode('utf-8')
            if 'Pkg.Revision=' + VERSION not in properties.splitlines():
                raise OfficialAdbError('Official ADB archive version mismatch.')
            files = {name: archive.read('platform-tools/' + name) for name in FILE_HASHES}
            if any(hashlib.sha256(data).hexdigest() != FILE_HASHES[name] for name, data in files.items()):
                raise OfficialAdbError('Official ADB executable/DLL checksum mismatch.')
            notice = archive.read('platform-tools/NOTICE.txt')
            return files, notice
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise OfficialAdbError('Invalid official ADB archive.') from exc


def _adb_running():
    """Read the Windows process inventory, without starting/stopping anything."""
    if os.name != 'nt':
        raise OfficialAdbError('Official Windows ADB installation requires Windows.')

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OfficialAdbError('Cannot verify whether ADB is running.')
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OfficialAdbError('Cannot read the Windows process inventory.')
        while True:
            if entry.szExeFile.lower() in {'adb.exe', 'nox_adb.exe', 'hd-adb.exe'}:
                return True
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    raise OfficialAdbError('Incomplete Windows process inventory.')
                return False
    finally:
        kernel.CloseHandle(snapshot)


@contextlib.contextmanager
def _binary_guards(paths):
    """Deny new readers of old files while permitting atomic replacement.

    Loaded executables/DLLs cause acquisition to fail before any write. The old
    executable remains guarded until all new DLLs are in place and it is swapped
    last. No process is killed to obtain a write window.
    """
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []
    images = {}
    try:
        for path in paths:
            _safe(path, file=True)
            expected_fingerprint = _fingerprint(path)
            if not path.exists():
                if expected_fingerprint is not None:
                    raise OfficialAdbError('ADB target changed before acquiring its guard: ' + path.name)
                images[path.name] = (None, None)
                continue
            # Share DELETE permits os.replace while denying new read/execute.
            # Read the preimage through this handle, rather than opening it a
            # second time (which our own exclusive guard would also reject).
            handle = kernel.CreateFileW(str(path), 0x80000000, 4, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OfficialAdbError('ADB executable/DLL is in use; no binaries changed: ' + path.name)
            handles.append(handle)
            # DELETE sharing permits another operator to rename this path. Bind
            # the preimage to the actual opened file ID, never to a later path.
            handle_fingerprint = _handle_fingerprint(kernel, handle)
            if handle_fingerprint != expected_fingerprint or _fingerprint(path) != handle_fingerprint:
                raise OfficialAdbError('ADB target changed while acquiring its guard: ' + path.name)
            chunks, size = [], 0
            buffer = ctypes.create_string_buffer(65536)
            count = wintypes.DWORD()
            while True:
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                    raise OfficialAdbError('Cannot back up ADB file: ' + path.name)
                if not count.value:
                    break
                size += count.value
                if size > 32 * 1024 * 1024:
                    raise OfficialAdbError('Existing ADB file is unexpectedly large: ' + path.name)
                chunks.append(buffer.raw[:count.value])
            if _handle_fingerprint(kernel, handle) != handle_fingerprint or _fingerprint(path) != handle_fingerprint:
                raise OfficialAdbError('ADB target changed while reading its preimage: ' + path.name)
            images[path.name] = (b''.join(chunks), handle_fingerprint)
        yield images
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


def _handle_fingerprint(kernel, handle):
    class FileInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                    ('accessed', wintypes.FILETIME), ('modified', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise OfficialAdbError('Cannot identify the guarded ADB file.')
    modified_ticks = (info.modified.dwHighDateTime << 32) | info.modified.dwLowDateTime
    return (info.volume, (info.index_high << 32) | info.index_low,
            (info.size_high << 32) | info.size_low,
            modified_ticks * 100 - 11644473600000000000)


def _fingerprint(path):
    _safe(path, file=True)
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def ensure_official_adb(executable):
    """Ensure the managed default Windows ADB path has the exact pinned bundle.

    Existing exact files need no download or process actions. Failures never
    silently fall back to the obsolete dependency-provided executable.
    """
    executable = _safe(executable, file=True)
    if executable.name.lower() != 'adb.exe':
        raise OfficialAdbError('Expected the managed adb.exe path.')
    directory = _safe(executable.parent)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {name: directory / name for name in FILE_HASHES}
    with _install_lock(directory):
        if all(_hash(paths[name]) == digest for name, digest in FILE_HASHES.items()):
            return 'already_current'
        cache = _safe(directory / '.official-adb')
        cache.mkdir(exist_ok=True)
        archive_path = cache / ('platform-tools-' + VERSION + '.zip')
        data = _safe(archive_path, file=True).read_bytes() if archive_path.exists() else _download()
        files, notice = _unpack(data)
        if not archive_path.exists():
            _write(archive_path, data)
        # A fresh, separate destination cannot replace any running ADB image.
        if any(path.exists() for path in paths.values()) and _adb_running():
            raise OfficialAdbError('ADB is running. Close account tasks, emulators and the existing ADB service, then rerun the installer. No binaries changed.')
        with _binary_guards(paths.values()) as images:
            # Acquire the guards before capturing/checking preimages.
            before = {name: images[name][0] for name in paths}
            before_hashes = {name: hashlib.sha256(data).hexdigest() if data is not None else None
                             for name, data in before.items()}
            backup = _safe(cache / ('backup-' + uuid.uuid4().hex))
            backup.mkdir()
            for name, data in before.items():
                if data is not None:
                    _write(backup / name, data)
            _write(backup / 'official-NOTICE.txt', notice)
            journal = {'version': VERSION, 'archive_sha256': ARCHIVE_SHA256,
                       'before_sha256': before_hashes, 'after_sha256': FILE_HASHES,
                       'state': 'application_started'}
            _json(backup / 'application.json', journal)
            changed = []
            attempted = []
            try:
                for name, data in files.items():
                    if _fingerprint(paths[name]) != images[name][1]:
                        raise OfficialAdbError('ADB target changed during installation: ' + name)
                    attempted.append(name)
                    _write(paths[name], data, expected_fingerprint=images[name][1])
                    changed.append(name)
                if not all(_hash(paths[name]) == digest for name, digest in FILE_HASHES.items()):
                    raise OfficialAdbError('ADB installed file verification failed.')
            except Exception as exc:
                rollback_errors = []
                try:
                    # Guard the published candidate too. Never replace a file
                    # that a process has loaded or that another operator edited.
                    changed = [name for name in attempted if _fingerprint(paths[name]) != images[name][1]]
                    with _binary_guards([paths[name] for name in changed]) as restore_images:
                        for name in changed:  # DLLs first, executable last again
                            data, fingerprint = restore_images[name]
                            digest = hashlib.sha256(data).hexdigest() if data is not None else None
                            if digest != FILE_HASHES[name] or _fingerprint(paths[name]) != fingerprint:
                                raise OfficialAdbError('Preserving a later change to ' + name)
                        for name in changed:
                            if before[name] is None:
                                if _fingerprint(paths[name]) != restore_images[name][1]:
                                    raise OfficialAdbError('Preserving a changed ADB target during rollback: ' + name)
                                _safe(paths[name], file=True).unlink()
                            else:
                                _write(paths[name], before[name], expected_fingerprint=restore_images[name][1])
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
                journal.update(state='rollback_incomplete' if rollback_errors else 'rolled_back',
                               error=str(exc), rollback_errors=rollback_errors)
                _json(backup / 'application.json', journal)
                raise OfficialAdbError('Official ADB installation failed; backup: ' + str(backup)) from exc
            journal['state'] = 'applied'
            _json(backup / 'application.json', journal)
        return 'installed'
