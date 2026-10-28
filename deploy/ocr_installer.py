"""Versioned, isolated OCR installation; never changes the toolkit packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, root):
    result = subprocess.run([str(a) for a in args], cwd=root, timeout=1200,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print(result.stdout, flush=True)
    result.check_returncode()


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
    with FileLock(str(parent / 'install.lock'), timeout=1500):
        existing = ready(root, manifest_hash)
        if existing:
            print('RapidOCR already verified; skip installation', flush=True)
            return existing
        print('Preparing isolated RapidOCR environment...', flush=True)
        wheels = Path(wheelhouse) if wheelhouse else root / 'deploy/rapidocr-wheels'
        if wheels.is_dir():
            verify_wheels(wheels, manifest)
        else:
            wheels = parent / ('download-' + uuid.uuid4().hex)
            wheels.mkdir()
            args = [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--require-hashes', '--disable-pip-version-check', '--dest', wheels,
                    '-r', root / 'deploy/requirements-rapidocr-lock.txt']
            if index_url:
                args += ['--index-url', index_url]
            run(args, root)
            verify_wheels(wheels, manifest)
        # Use the final directory name from the start: venv launchers embed paths.
        target = parent / (manifest_hash[:12] + '-' + uuid.uuid4().hex[:8])
        run([sys.executable, '-m', 'venv', '--system-site-packages', target], root)
        python = target / 'Scripts/python.exe'
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps',
             '--ignore-installed', '--disable-pip-version-check', '--require-hashes',
             '--find-links', wheels, '-r', root / 'deploy/requirements-rapidocr-lock.txt'], root)
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
