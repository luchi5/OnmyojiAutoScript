import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy import ocr_installer as rapidocr


def test_wheel_integrity_and_missing_file(tmp_path):
    wheel = tmp_path / 'example.whl'
    wheel.write_bytes(b'wheel')
    manifest = {'wheels': [{'name': wheel.name, 'sha256': rapidocr.digest(wheel)}]}
    rapidocr.verify_wheels(tmp_path, manifest)
    wheel.write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        rapidocr.verify_wheels(tmp_path, manifest)
    wheel.unlink()
    with pytest.raises(ValueError):
        rapidocr.verify_wheels(tmp_path, manifest)


def test_legacy_override_and_existing_account_policy_are_preserved(tmp_path, monkeypatch):
    install = Mock(side_effect=AssertionError('must not install'))
    monkeypatch.setattr(rapidocr, 'ensure_runtime', install)
    monkeypatch.setenv('OAS_OCR_BACKEND', 'legacy')
    assert rapidocr.prepare_optional(root=tmp_path) is None
    monkeypatch.delenv('OAS_OCR_BACKEND')
    assert rapidocr.prepare_optional(SimpleNamespace(AutoInstallRapidOcr=False), tmp_path) is None
    path = tmp_path / 'config/ocr_runtime/accounts.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    assert rapidocr.prepare_optional(root=tmp_path) is None
    install.assert_not_called()


def test_download_failure_does_not_activate_or_overwrite_policy(tmp_path, monkeypatch):
    monkeypatch.delenv('OAS_OCR_BACKEND', raising=False)
    monkeypatch.setattr(rapidocr, 'ensure_runtime', Mock(side_effect=OSError('offline')))
    assert rapidocr.prepare_optional(root=tmp_path) is None
    assert not (tmp_path / 'runtime/rapidocr/active.json').exists()
    assert not (tmp_path / 'config/ocr_runtime/accounts.json').exists()


def test_ready_rejects_relocation_tampering_and_external_path(tmp_path):
    parent = tmp_path / 'runtime/rapidocr'
    python = parent / 'v1/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    model = python.parent.parent / 'Lib/site-packages/rapidocr/models/test.onnx'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'model')
    record = {'manifest': 'v1', 'root': str(tmp_path.resolve()),
              'base_python': str(Path(sys.executable).resolve()),
              'python': python.relative_to(tmp_path).as_posix(),
              'models': {'test.onnx': rapidocr.digest(model)}}
    active = parent / 'active.json'
    active.write_text(json.dumps(record))
    assert rapidocr.ready(tmp_path, 'v1') == str(python.resolve())
    assert rapidocr.ready(tmp_path, 'v2') is None
    model.write_bytes(b'corrupt')
    assert rapidocr.ready(tmp_path, 'v1') is None
    record['root'] = str(tmp_path / 'old-location')
    active.write_text(json.dumps(record))
    assert rapidocr.ready(tmp_path, 'v1') is None
    record['root'] = str(tmp_path.resolve())
    record['python'] = '../../outside/python.exe'
    active.write_text(json.dumps(record))
    assert rapidocr.ready(tmp_path, 'v1') is None


def test_failed_self_test_preserves_previous_active_record(tmp_path, monkeypatch):
    monkeypatch.setattr(rapidocr.sys, 'platform', 'win32')
    monkeypatch.setattr(rapidocr.platform, 'architecture', lambda: ('64bit', ''))
    deploy = tmp_path / 'deploy'
    deploy.mkdir()
    (deploy / 'rapidocr-manifest.json').write_text(json.dumps({'wheels': [], 'models': {}}))
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    parent = tmp_path / 'runtime/rapidocr'
    parent.mkdir(parents=True)
    active = parent / 'active.json'
    active.write_text('{"old": true}')
    def run(args, root):
        if '--self-test' in args:
            raise RuntimeError('self-test failed')
    monkeypatch.setattr(rapidocr, 'run', run)
    with pytest.raises(RuntimeError, match='self-test failed'):
        rapidocr.ensure_runtime(tmp_path, wheels)
    assert active.read_text() == '{"old": true}'


def test_managed_backend_uses_isolated_worker_and_respects_disable(tmp_path, monkeypatch):
    from module.ocr import backend, account_runtime, isolated_rapid, ppocr
    monkeypatch.delenv('OAS_OCR_BACKEND', raising=False)
    monkeypatch.setattr(account_runtime, 'current_account_runtime', lambda: None)
    monkeypatch.setattr(account_runtime, 'SELECTION_FILE', tmp_path / 'missing.json')
    install = Mock(return_value='managed-python')
    worker = Mock(return_value='isolated')
    old = Mock(return_value='legacy')
    monkeypatch.setattr(backend, 'managed_runtime', install)
    monkeypatch.setattr(isolated_rapid, 'IsolatedRapidTextSystem', worker)
    monkeypatch.setattr(ppocr, 'TextSystem', old)
    assert backend.configured_text_system(SimpleNamespace()) == 'isolated'
    worker.assert_called_once_with('managed-python')
    install.reset_mock()
    assert backend.configured_text_system(SimpleNamespace(AutoInstallRapidOcr=False)) == 'legacy'
    install.assert_not_called()


def test_managed_download_failure_uses_legacy(tmp_path, monkeypatch):
    from module.ocr import backend, account_runtime, ppocr
    monkeypatch.delenv('OAS_OCR_BACKEND', raising=False)
    monkeypatch.setattr(account_runtime, 'current_account_runtime', lambda: None)
    monkeypatch.setattr(account_runtime, 'SELECTION_FILE', tmp_path / 'missing.json')
    monkeypatch.setattr(backend, 'managed_runtime', lambda mirror: None)
    monkeypatch.setattr(ppocr, 'TextSystem', Mock(return_value='legacy'))
    assert backend.configured_text_system(SimpleNamespace()) == 'legacy'
