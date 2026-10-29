"""Versioned, isolated OCR installation; never changes the toolkit packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, root, timeout=1200, heartbeat=15):
    """Stream pip output while retaining a timeout even when the child is silent."""
    command = [str(a) for a in args]
    env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    started = time.monotonic()
    with subprocess.Popen(command, cwd=root, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding='utf-8', errors='replace', bufsize=1,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        output = queue.Queue()

        def read_output():
            try:
                for line in process.stdout:
                    output.put(line)
            finally:
                output.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        next_notice = started + heartbeat
        try:
            while True:
                now = time.monotonic()
                if now - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    line = output.get(timeout=min(0.5, timeout - (now - started)))
                except queue.Empty:
                    if now >= next_notice:
                        print(f'[OCR] 当前步骤仍在进行，已用时 {int(now - started)} 秒，请勿关闭窗口。', flush=True)
                        next_notice = now + heartbeat
                    continue
                if line is None:
                    break
                print(line, end='', flush=True)
                next_notice = time.monotonic() + heartbeat
            code = process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
            if code:
                raise subprocess.CalledProcessError(code, command)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)


def verify_wheels(folder, manifest):
    for entry in manifest['wheels']:
        name = entry['name']
        if Path(name).name != name:
            raise ValueError('Invalid wheel filename')
        path = folder / name
        if not path.is_file() or digest(path) != entry['sha256']:
            raise ValueError(f'Missing or damaged OCR wheel: {name}')


def ready(root, manifest_hash):
    """A relocated installation must recreate its venv, not reuse absolute paths."""
    try:
        data = json.loads((root / 'runtime/rapidocr/active.json').read_text('utf-8'))
        python = (root / data['python']).resolve()
        python.relative_to((root / 'runtime/rapidocr').resolve())
        if (data['manifest'] != manifest_hash or data['root'] != str(root.resolve())
                or data['base_python'] != str(Path(sys.executable).resolve())
                or not python.is_file()):
            return None
        for name, expected in data['models'].items():
            if digest(python.parent.parent / 'Lib/site-packages/rapidocr/models' / name) != expected:
                return None
        return str(python)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ensure_runtime(root=ROOT, wheelhouse=None, index_url=None):
    root = Path(root).resolve()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 10) or platform.architecture()[0] != '64bit':
        raise RuntimeError('Bundled OCR currently requires Windows x64 / Python 3.10')
    manifest_path = root / 'deploy/rapidocr-manifest.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    manifest_hash = digest(manifest_path)
    parent = root / 'runtime/rapidocr'
    parent.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock
    print('[OCR] 正在检查环境；若其他窗口正在安装，将等待其完成。', flush=True)
    with FileLock(str(parent / 'install.lock'), timeout=1500):
        existing = ready(root, manifest_hash)
        if existing:
            print('RapidOCR already verified; skip installation', flush=True)
            return existing
        print('[OCR] 首次准备新 OCR：下载 → 校验 → 创建环境 → 安装 → 识别自检。', flush=True)
        wheels = Path(wheelhouse) if wheelhouse else root / 'deploy/rapidocr-wheels'
        if wheels.is_dir():
            print('[OCR 1/5] 使用本地离线依赖包。', flush=True)
            print('[OCR 2/5] 校验依赖包完整性。', flush=True)
            verify_wheels(wheels, manifest)
        else:
            print('[OCR 1/5] 正在下载依赖，下面显示各文件下载进度（不是总进度）。', flush=True)
            wheels = parent / ('download-' + uuid.uuid4().hex)
            wheels.mkdir()
            args = [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--require-hashes', '--disable-pip-version-check', '--progress-bar', 'on', '--dest', wheels,
                    '-r', root / 'deploy/requirements-rapidocr-lock.txt']
            if index_url:
                args += ['--index-url', index_url]
            run(args, root)
            print('[OCR 2/5] 下载完成，校验依赖包完整性。', flush=True)
            verify_wheels(wheels, manifest)
        # Use the final directory name from the start: venv launchers embed paths.
        target = parent / (manifest_hash[:12] + '-' + uuid.uuid4().hex[:8])
        print('[OCR 3/5] 正在创建独立 Python 环境。', flush=True)
        run([sys.executable, '-m', 'venv', '--system-site-packages', target], root)
        python = target / 'Scripts/python.exe'
        print('[OCR 4/5] 正在安装依赖包。', flush=True)
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps',
             '--ignore-installed', '--disable-pip-version-check', '--require-hashes',
             '--find-links', wheels, '-r', root / 'deploy/requirements-rapidocr-lock.txt'], root)
        print('[OCR 5/5] 正在校验模型并运行识别自检。', flush=True)
        for name, expected in manifest['models'].items():
            if digest(target / 'Lib/site-packages/rapidocr/models' / name) != expected:
                raise ValueError(f'OCR model checksum mismatch: {name}')
        run([python, root / 'deploy/ocr_installer.py', '--self-test', '--root', root], root)
        data = {'schema': 1, 'manifest': manifest_hash, 'root': str(root),
                'base_python': str(Path(sys.executable).resolve()),
                'python': python.relative_to(root).as_posix(), 'models': manifest['models'],
                'installed_at': time.time()}
        temporary = parent / ('active-' + uuid.uuid4().hex + '.json')
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(temporary, parent / 'active.json')
        print('RapidOCR installation and recognition self-test passed', flush=True)
        return str(python)


def prepare_optional(config=None, root=ROOT):
    """Preserve explicit local account selection and legacy opt-out."""
    if os.environ.get('OAS_OCR_BACKEND') or not getattr(config, 'AutoInstallRapidOcr', True):
        return None
    if (Path(root) / 'config/ocr_runtime/accounts.json').exists():
        return None
    try:
        return ensure_runtime(root, index_url=getattr(config, 'PypiMirror', None))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('新 OCR 安装失败，保留旧 OCR；下次启动可重试：%s', exc)
        return None


def self_test(root):
    sys.path.insert(0, str(root))
    import cv2
    import numpy as np
    from module.ocr.rapidocr_adapter import RapidTextSystem
    cv2.setNumThreads(1)
    model = RapidTextSystem()
    image = np.full((100, 400, 3), 255, dtype=np.uint8)
    cv2.putText(image, '123456', (15, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    result = model.ocr_single_line(image)
    if '123456' not in str(result):
        raise RuntimeError(f'OCR recognition self-test failed: {result!r}')
    print('OCR_SELF_TEST_OK', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--index-url')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        ensure_runtime(args.root, args.wheelhouse, args.index_url)
