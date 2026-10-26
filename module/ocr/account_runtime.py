"""Account-scoped selection, frozen at first use and effective on new processes."""
from functools import lru_cache
import json
import multiprocessing
import os
from pathlib import Path

SELECTION_FILE = Path(__file__).resolve().parents[2] / "config/ocr_runtime/accounts.json"


def resolve_account_runtime(account, created_at, path=SELECTION_FILE):
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entry = data.get("accounts", {}).get(account)
    if entry is None and account and account not in {"MainProcess", "SpawnProcess"}:
        # Defaults apply only to real account files, never GUI/helper processes.
        config_dir = path.parent.parent.resolve()
        account_file = (config_dir / f"{account}.json").resolve()
        if account_file.parent == config_dir and account_file.is_file():
            entry = data.get("default")
    if entry is None or created_at < entry["enabled_after"]:
        return None
    if entry["backend"] == "legacy":
        return None
    if entry["backend"] != "rapid-small":
        raise ValueError(f"Unsupported account OCR backend: {entry['backend']}")
    python = Path(data["runtime_python"])
    if not python.is_file():
        raise FileNotFoundError(f"Missing isolated OCR runtime: {python}")
    return {"account": account, "python": str(python)}


@lru_cache(maxsize=1)
def current_account_runtime():
    account = os.environ.get("OAS_ACCOUNT_NAME", multiprocessing.current_process().name)
    # Ordinary GUI/server processes are not named after an account.
    if not SELECTION_FILE.exists():
        return None
    import psutil
    return resolve_account_runtime(account, psutil.Process().create_time())
