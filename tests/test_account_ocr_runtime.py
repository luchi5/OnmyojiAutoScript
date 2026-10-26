import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.ocr.account_runtime import resolve_account_runtime
from module.ocr.backend import configured_text_system


@pytest.fixture
def selection(tmp_path):
    python = tmp_path / "python.exe"
    python.touch()
    path = tmp_path / "ocr_accounts.json"
    path.write_text(json.dumps({"runtime_python": str(python), "accounts": {
        "selected-a": {"backend": "rapid-small", "enabled_after": 100},
        "selected-b": {"backend": "rapid-small", "enabled_after": 100},
        "selected-c": {"backend": "rapid-small", "enabled_after": 100}}}), encoding="utf-8")
    return path


@pytest.mark.parametrize("account", ["selected-a", "selected-b", "selected-c"])
def test_only_new_selected_account_processes_enable_small(selection, account):
    assert resolve_account_runtime(account, 99, selection) is None
    assert resolve_account_runtime(account, 101, selection)["account"] == account


@pytest.mark.parametrize("account", ["unselected-a", "unselected-b", "unselected-c", "selected", "selected-a-other", "MainProcess"])
def test_selection_requires_exact_account_name(selection, account):
    assert resolve_account_runtime(account, 101, selection) is None


def test_missing_runtime_fails_only_for_opted_in_new_process(selection):
    Path(json.loads(selection.read_text())["runtime_python"]).unlink()
    assert resolve_account_runtime("unselected-a", 101, selection) is None
    assert resolve_account_runtime("selected-a", 99, selection) is None
    with pytest.raises(FileNotFoundError):
        resolve_account_runtime("selected-a", 101, selection)


def test_explicit_legacy_override_bypasses_account_selection(monkeypatch):
    import module.ocr.account_runtime as runtime
    import module.ocr.ppocr as legacy
    select = Mock(side_effect=AssertionError("selection must not run"))
    create = Mock(return_value="legacy")
    monkeypatch.setattr(runtime, "current_account_runtime", select)
    monkeypatch.setattr(legacy, "TextSystem", create)
    monkeypatch.setenv("OAS_OCR_BACKEND", "legacy")
    assert configured_text_system(SimpleNamespace(OcrBackend="rapid-small")) == "legacy"
    create.assert_called_once_with()


def test_default_covers_existing_and_future_accounts_but_not_helpers(tmp_path):
    config = tmp_path / "config"
    selection_dir = config / "ocr_runtime"
    selection_dir.mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.touch()
    path = selection_dir / "accounts.json"
    path.write_text(json.dumps({"runtime_python": str(python),
        "default": {"backend": "rapid-small", "enabled_after": 100},
        "accounts": {"old": {"backend": "rapid-small", "enabled_after": 50},
                     "except": {"backend": "legacy", "enabled_after": 0}}}))
    (config / "existing.json").touch()
    assert resolve_account_runtime("existing", 99, path) is None
    assert resolve_account_runtime("existing", 101, path)["account"] == "existing"
    (config / "future.json").touch()
    assert resolve_account_runtime("future", 101, path)["account"] == "future"
    assert resolve_account_runtime("old", 60, path)["account"] == "old"
    assert resolve_account_runtime("except", 101, path) is None
    for name in ("MainProcess", "SpawnProcess-1", "missing", "../python"):
        assert resolve_account_runtime(name, 101, path) is None
