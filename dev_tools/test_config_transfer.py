"""Isolated transfer/API tests: temporary configs, no server manager/device imports.

Run: toolkit/python.exe dev_tools/test_config_transfer.py
"""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Production logger changes cwd and cleans old logs during import. Tests never load it.
fake_logger = types.ModuleType("module.logger")
fake_logger.logger = Mock()
sys.modules["module.logger"] = fake_logger
server_package = types.ModuleType("module.server")
server_package.__path__ = [str(ROOT / "module/server")]
sys.modules["module.server"] = server_package

from fastapi import FastAPI
from module.config.config_model import ConfigModel
from module.server.config_transfer import ConfigTransferError, ConfigTransferService, MAX_BYTES, REDACTED, parse_json_source
from module.server.config_transfer_router import create_config_transfer_router


class FakeManager:
    def __init__(self, root):
        self.root = Path(root)
        self.script_process = {}
        self._all_script_files = []
        self.added = []

    def all_script_files(self):
        return sorted(path.stem for path in self.root.glob("*.json"))

    def add_script_file(self, name):
        self.script_process[name] = types.SimpleNamespace(state=0, _process=None)
        self.added.append(name)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = FakeManager(self.root)
        self.service = ConfigTransferService(self.root, self.manager, ConfigModel)

    def create(self, name="account", data=None):
        return self.service.import_config(name, data or {"script": {"device": {"serial": "test-device"}}})

    def read(self, name="account"):
        return json.loads((self.root / f"{name}.json").read_text(encoding="utf8"))

    def test_new_config_uses_target_name_no_runtime_and_disables_every_schedule(self):
        with patch.object(ConfigModel, "read_json", side_effect=AssertionError("no private file reads")):
            self.create(data={"config_name": "old", "running_task": "Chess", "chess": {"scheduler": {"enable": True}}})
        data = self.read()
        self.assertEqual(data["config_name"], "account")
        self.assertEqual(data["running_task"], "")
        self.assertTrue(all(not task["scheduler"]["enable"] for task in data.values()
                            if isinstance(task, dict) and "scheduler" in task))
        self.assertEqual(self.manager.added, ["account"])
        self.assertEqual(self.manager._all_script_files, ["account"])

    def test_scalar_and_multi_courtyard_legacy_configs(self):
        for index, value in enumerate(("costume_main_13", ["costume_main_13", "costume_main_17"])):
            self.create(f"legacy{index}", {"global_game": {"costume_config": {"costume_main_type": value}}})
            actual = self.read(f"legacy{index}")["global_game"]["costume_config"]["costume_main_type"]
            self.assertEqual(actual, [value] if isinstance(value, str) else value)

    def test_full_backup_roundtrip_handles_numbered_mainline_groups(self):
        self.create()
        _, backup = self.service.export_config("account", "backup")
        backup["find_jade"]["invite_info_list_1"]["name"] = "synthetic-friend"
        self.create("restored", backup)
        self.assertEqual(self.read("restored")["find_jade"]["invite_info_list_1"]["name"], "synthetic-friend")

    def test_unknown_task_and_nested_fields_rejected_without_files(self):
        for data in ({"unsupported": {}}, {"chess": {"chess_config": {"wrong": "secret-value"}}}):
            with self.assertRaises(ConfigTransferError) as caught:
                self.create(data=data)
            self.assertTrue(caught.exception.fields)
            self.assertNotIn("secret-value", str(caught.exception))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_out_of_range_is_rejected_instead_of_ConfigBase_fallback(self):
        with self.assertRaises(ConfigTransferError) as caught:
            self.create(data={"chess": {"chess_config": {"matchmaking_timeout_seconds": 1}}})
        self.assertIn("chess.chess_config.matchmaking_timeout_seconds", caught.exception.fields)

    def test_invalid_enum_is_rejected_instead_of_costume_default(self):
        for field in ("costume_main_type", "costume_realm_type"):
            with self.assertRaises(ConfigTransferError):
                self.create(data={"global_game": {"costume_config": {field: "bad-option"}}})

    def test_invalid_interval_is_rejected_instead_of_one_day_default(self):
        with self.assertRaises(ConfigTransferError):
            self.create(data={"chess": {"scheduler": {"success_interval": "not-a-time"}}})

    def test_invalid_boolean_object_is_rejected(self):
        with self.assertRaises(ConfigTransferError):
            self.create(data={"chess": {"chess_config": {"rank_protection": {"bad": True}}}})

    def test_names_paths_reserved_template_and_unicode(self):
        for name in ("../evil", "C:\\evil", "a/b", "a.b", "test ", "", "CON", "lpt1", "Template", "Home", "a" * 65):
            with self.subTest(name=name), self.assertRaises(ConfigTransferError):
                self.create(name)
        self.create("测试账号.json")
        self.assertTrue((self.root / "测试账号.json").exists())

    def test_existing_full_import_conflict_leaves_original_identical(self):
        self.create()
        before = (self.root / "account.json").read_bytes()
        with self.assertRaises(ConfigTransferError) as caught:
            self.create(data={"chess": {}})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(before, (self.root / "account.json").read_bytes())

    def test_share_redacts_backup_and_copy_keep_values(self):
        self.create(data={"script": {"device": {"serial": "test-private-device"},
                                     "error": {"notify_config": "synthetic-secret"}}})
        _, shared = self.service.export_config("account")
        self.assertEqual(shared["script"]["device"]["serial"], REDACTED)
        self.assertEqual(shared["script"]["error"]["notify_config"], REDACTED)
        _, backup = self.service.export_config("account", "backup")
        self.assertEqual(backup["script"]["device"]["serial"], "test-private-device")

    def test_shared_task_import_preserves_local_secret(self):
        self.create()
        self.service.import_task("account", "Script", {"script": {"device": {"serial": REDACTED, "handle": REDACTED}}})
        self.assertEqual(self.read()["script"]["device"]["serial"], "test-device")

    def test_share_import_to_new_uses_safe_defaults_for_redacted_fields(self):
        self.create()
        _, data = self.service.export_config("account", "share")
        self.create("shared", data)
        self.assertEqual(self.read("shared")["script"]["device"]["serial"], "auto")

    def test_task_import_keeps_other_tasks_and_enable_and_cache_state(self):
        self.create()
        before = self.read()
        self.service.import_task("account", "Chess", {"Chess": {"scheduler": {"enable": True}, "chess_config": {"run_count": 7}}})
        after = self.read()
        self.assertEqual(after["chess"]["chess_config"]["run_count"], 7)
        self.assertFalse(after["chess"]["scheduler"]["enable"])
        self.assertEqual(after["orochi"], before["orochi"])
        self.assertEqual(self.manager.added, ["account"])

    def test_running_warning_updating_and_live_process_all_block_import(self):
        self.create()
        before = (self.root / "account.json").read_bytes()
        proc = self.manager.script_process["account"]
        for state in (1, 2, 3):
            proc.state = state
            with self.assertRaises(ConfigTransferError) as caught:
                self.service.import_task("account", "Chess", {"chess": {}})
            self.assertEqual(caught.exception.status, 409)
        proc.state = 0
        proc._process = types.SimpleNamespace(is_alive=lambda: True)
        with self.assertRaises(ConfigTransferError):
            self.service.import_task("account", "Chess", {"chess": {}})
        self.assertEqual(before, (self.root / "account.json").read_bytes())
        self.assertTrue(self.service.export_config("account", "backup"))

    def test_current_task_only_wrong_task_multiple_and_unknown_fail(self):
        self.create()
        for task, data in (("Chess", {"orochi": {}}), ("Chess", {"chess": {}, "orochi": {}}),
                           ("Unknown", {"unknown": {}}), ("Script", {"script": []})):
            with self.assertRaises(ConfigTransferError):
                self.service.import_task("account", task, data)

    def test_numbered_group_unknown_children_rejected(self):
        with self.assertRaises(ConfigTransferError):
            self.create(data={"find_jade": {"invite_info_list_1": {"unknown": "bad"}}})

    def test_legacy_dynamic_group_allocation_is_bounded(self):
        for data in ({"find_jade": {"find_jade_config": {"invite_info_count": 1000000000}}},
                     {"meta_demon": {"meta_demon_config": {"md_strategy_count": "1000000000"}}}):
            with self.assertRaises(ConfigTransferError):
                self.create(data=data)

    def test_single_task_transfer_does_not_normalize_unrelated_values(self):
        self.create()
        path = self.root / "account.json"
        data = self.read()
        data["meta_demon"]["meta_demon_config"]["auto_tea"] = True
        data["unrelated_legacy_task"] = {"legacy_value": "keep"}
        path.write_text(json.dumps(data), encoding="utf8")
        self.service.import_task("account", "Chess", {"chess": {"chess_config": {"run_count": 4}}})
        actual = self.read()
        self.assertTrue(actual["meta_demon"]["meta_demon_config"]["auto_tea"])
        self.assertEqual(actual["unrelated_legacy_task"], data["unrelated_legacy_task"])

    def test_running_guard_is_rechecked_before_atomic_creation(self):
        states = iter((0, 1))
        class ChangingProcess:
            _process = None
            @property
            def state(self):
                return next(states)
        self.manager.script_process["race"] = ChangingProcess()
        with self.assertRaises(ConfigTransferError) as caught:
            self.create("race")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_post_commit_cache_failure_is_reported_as_saved_with_warning(self):
        with patch.object(self.manager, "add_script_file", side_effect=OSError("synthetic resource limit")):
            result = self.create()
        self.assertTrue((self.root / "account.json").exists())
        self.assertEqual(result["name"], "account")
        self.assertEqual(len(result["warnings"]), 2)

    def test_atomic_replace_failure_keeps_old_file_and_removes_temp(self):
        self.create()
        before = (self.root / "account.json").read_bytes()
        with patch.object(sys.modules["module.server.config_transfer"].os, "replace", side_effect=OSError("synthetic")), self.assertRaises(ConfigTransferError):
            self.service.import_task("account", "Chess", {"chess": {"chess_config": {"run_count": 8}}})
        self.assertEqual(before, (self.root / "account.json").read_bytes())
        self.assertEqual([p.name for p in self.root.iterdir()], ["account.json"])

    def test_optimistic_file_conflict_does_not_replace_new_edit(self):
        self.create()
        _, path, original, digest = self.service._read("account")
        path.write_text('{"concurrent":true}', encoding="utf8")
        with self.assertRaises(ConfigTransferError) as caught:
            self.service._write("account", path, original, previous=digest)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(path.read_text(encoding="utf8"), '{"concurrent":true}')

    def test_utf8_bom_duplicates_nan_depth_and_size_limits(self):
        self.assertEqual(parse_json_source(file_content=b'\xef\xbb\xbf{"chess":{}}'), {"chess": {}})
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'[]', b'{}', b'\xff', b'{bad'):
            with self.subTest(raw=raw), self.assertRaises(ConfigTransferError):
                parse_json_source(file_content=raw)
        with self.assertRaises(ConfigTransferError) as caught:
            parse_json_source(file_content=b" " * (MAX_BYTES + 1))
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(ConfigTransferError):
            parse_json_source(json_text="{\"x\":" * 26 + "1" + "}" * 26)
        with self.assertRaises(ConfigTransferError):
            parse_json_source(file_content=b'{"x":"\\ud800"}')


async def asgi_request(app, method, target, body=b"", headers=None):
    path, _, query = target.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "headers": headers or [], "server": ("fixture", 80), "client": ("fixture", 123)}
    pending = True
    sent = []

    async def receive():
        nonlocal pending
        if pending:
            pending = False
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    raw = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), json.loads(raw)


def multipart(fields, file_data=None):
    boundary = "fixture-safe-transfer"
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    if file_data is not None:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ignored.json"\r\nContent-Type: application/json\r\n\r\n'.encode() + file_data + b"\r\n")
    chunks.append(f'--{boundary}--\r\n'.encode())
    body = b"".join(chunks)
    return body, [(b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                  (b"content-length", str(len(body)).encode())]


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = ConfigTransferService(self.temp.name, model_type=ConfigModel)
        self.app = FastAPI()
        self.app.include_router(create_config_transfer_router(self.service))

    async def test_capabilities_and_full_import_export_modes(self):
        status, _, data = await asgi_request(self.app, "GET", "/config/transfer/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(data["max_bytes"], MAX_BYTES)
        body, headers = multipart({"name": "api"}, b'{"script":{"device":{"serial":"fake-device"}}}')
        status, _, data = await asgi_request(self.app, "POST", "/config/import", body, headers)
        self.assertEqual((status, data["name"]), (200, "api"))
        status, headers, shared = await asgi_request(self.app, "GET", "/config/export?name=api")
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertIn(b"attachment", headers[b"content-disposition"])
        self.assertEqual(shared["script"]["device"]["serial"], REDACTED)
        status, _, backed = await asgi_request(self.app, "GET", "/config/export?name=api&mode=backup")
        self.assertEqual(backed["script"]["device"]["serial"], "fake-device")

    async def test_task_json_text_import_export_copy_and_bad_both_sources(self):
        self.service.import_config("api", {"chess": {}})
        body, headers = multipart({"config_name": "api", "task_name": "Chess", "json_text": '{"chess":{"chess_config":{"run_count":11}}}'})
        status, _, result = await asgi_request(self.app, "POST", "/config/task/import", body, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result["updated"])
        for route in ("export", "copy-json"):
            status, _, data = await asgi_request(self.app, "GET", f"/config/task/{route}?config_name=api&task_name=Chess")
            self.assertEqual(status, 200)
            self.assertEqual(data["chess"]["chess_config"]["run_count"], 11)
        body, headers = multipart({"config_name": "api", "task_name": "Chess", "json_text": '{"chess":{}}'}, b'{"chess":{}}')
        status, _, _ = await asgi_request(self.app, "POST", "/config/task/import", body, headers)
        self.assertEqual(status, 400)

    async def test_file_task_import_and_private_field_validation_error(self):
        self.service.import_config("api", {"chess": {}})
        body, headers = multipart({"config_name": "api", "task_name": "Chess"}, b'{"chess":{"chess_config":{"run_count":3}}}')
        status, _, _ = await asgi_request(self.app, "POST", "/config/task/import", body, headers)
        self.assertEqual(status, 200)
        body, headers = multipart({"name": "bad"}, b'{"script":{"device":{"secret_value":"DO_NOT_ECHO"}}}')
        status, _, error = await asgi_request(self.app, "POST", "/config/import", body, headers)
        self.assertEqual(status, 400)
        self.assertEqual(error["detail"]["fields"], ["script.device.secret_value"])
        self.assertNotIn("DO_NOT_ECHO", json.dumps(error))

    async def test_oversized_upload_and_existing_name_errors(self):
        body, headers = multipart({"name": "api"}, b" " * (MAX_BYTES + 1))
        status, _, _ = await asgi_request(self.app, "POST", "/config/import", body, headers)
        self.assertEqual(status, 413)
        self.service.import_config("api", {"chess": {}})
        body, headers = multipart({"name": "api"}, b'{"chess":{}}')
        status, _, _ = await asgi_request(self.app, "POST", "/config/import", body, headers)
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
