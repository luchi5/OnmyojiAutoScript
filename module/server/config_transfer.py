"""Validated JSON transfer, independent of devices, schedulers and server startup."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from enum import Enum
from functools import cached_property
from pathlib import Path
from threading import RLock
from typing import get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic_core import SchemaValidator

MAX_BYTES = 2 * 1024 * 1024
REDACTED = "__OAS_REDACTED__"
_LOCK = RLock()
_MISSING = object()
_SENSITIVE = {
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "apikey", "access_key", "cookie", "authorization", "notify_config",
    "serial", "handle", "emulatorinfo_name", "emulatorinfo_path", "account",
    "account_alias", "username", "user_name", "character", "friend_list", "guild_member_list",
}


class ConfigTransferError(ValueError):
    def __init__(self, message, status=400, fields=None):
        super().__init__(message)
        self.status = status
        self.fields = fields or []


def _reject_constant(_):
    raise ConfigTransferError("JSON 不允许 NaN 或 Infinity")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigTransferError("JSON 包含重复的参数名称")
        result[key] = value
    return result


def parse_json_source(*, json_text=None, file_content=None):
    if (json_text is None) == (file_content is None):
        raise ConfigTransferError("请只提供一个 JSON 文件或 JSON 文本")
    try:
        raw = file_content if file_content is not None else json_text.encode("utf-8")
    except (UnicodeError, AttributeError):
        raise ConfigTransferError("请提供有效的 UTF-8 JSON 文本") from None
    if len(raw) > MAX_BYTES:
        raise ConfigTransferError("配置 JSON 不能超过 2 MiB", 413)
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except ConfigTransferError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ConfigTransferError("请提供有效的 UTF-8 JSON 对象") from None
    if not isinstance(value, dict) or not value:
        raise ConfigTransferError("配置必须是非空 JSON 对象")
    budget = [0]

    def check(item, depth=0):
        budget[0] += 1
        if depth > 24 or budget[0] > 50000:
            raise ConfigTransferError("配置 JSON 结构过深或参数过多", 413)
        if isinstance(item, dict):
            for key, child in item.items():
                if len(key) > 128:
                    raise ConfigTransferError("参数名称过长")
                try:
                    key.encode("utf-8")
                except UnicodeError:
                    raise ConfigTransferError("配置包含无效的 Unicode 字符") from None
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise ConfigTransferError("配置包含无效的 Unicode 字符") from None
    check(value)
    return value


def _safe_schema(schema):
    """Do not run ConfigModel's file-loading init or ConfigBase's range fallback."""
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "model":
                node["custom_init"] = False
                node["config"] = {**node.get("config", {}), "extra_fields_behavior": "forbid"}
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(result)
    return result


def _task_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value):
        raise ConfigTransferError("任务名称不合法")
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def _is_subclass(annotation, base):
    # Python 3.10 GenericAlias can report isinstance(alias, type) as true.
    return get_origin(annotation) is None and isinstance(annotation, type) and issubclass(annotation, base)


def _merge(base, incoming):
    if incoming == REDACTED:
        return copy.deepcopy(base) if base is not _MISSING else _MISSING
    if isinstance(incoming, dict):
        result = copy.deepcopy(base) if isinstance(base, dict) else {}
        for key, value in incoming.items():
            merged = _merge(result.get(key, _MISSING), value)
            if merged is not _MISSING:
                result[key] = merged
        return result
    if isinstance(incoming, list):
        result = []
        old = base if isinstance(base, list) else []
        for index, item in enumerate(incoming):
            merged = _merge(old[index] if index < len(old) else _MISSING, item)
            if merged is not _MISSING:
                result.append(merged)
        return result
    return copy.deepcopy(incoming)


def redact_config(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key).lower()
            sensitive = (normalized in _SENSITIVE or any(part in normalized for part in
                         ("password", "secret", "token", "cookie", "webhook")))
            result[key] = REDACTED if sensitive else redact_config(item)
        return result
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return copy.deepcopy(value)


class ConfigTransferService:
    def __init__(self, config_dir, manager=None, model_type=None):
        self.root = Path(config_dir).resolve()
        self.manager = manager
        self._model_type = model_type

    @cached_property
    def model_type(self):
        if self._model_type is not None:
            return self._model_type
        from module.config.config_model import ConfigModel
        return ConfigModel

    @cached_property
    def validator(self):
        return SchemaValidator(_safe_schema(self.model_type.__pydantic_core_schema__))

    def _name(self, name, *, write=False):
        if not isinstance(name, str):
            raise ConfigTransferError("配置名称不合法")
        if name.endswith(".json"):
            name = name[:-5]
        if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}", name):
            raise ConfigTransferError("配置名称仅支持中文、英文、数字、下划线或短横线")
        if name.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                          *(f"LPT{i}" for i in range(1, 10))}:
            raise ConfigTransferError("配置名称是系统保留名称")
        if name.casefold() == "home" or (write and name.casefold() == "template"):
            raise ConfigTransferError("不能导入或修改配置模板")
        path = self.root / f"{name}.json"
        if path.resolve().parent != self.root or path.is_symlink():
            raise ConfigTransferError("配置文件路径不合法")
        return name, path

    def _inactive(self, name):
        if self.manager is None:
            return
        proc = self.manager.script_process.get(name)
        if proc is not None:
            child = getattr(proc, "_process", None)
            if getattr(proc, "state", 0) != 0 or (child is not None and child.is_alive()):
                raise ConfigTransferError("此配置正在运行，请先停止该配置再导入", 409)

    def _normalize(self, data, model, path=""):
        """Restore mainline's numbered list groups before strict validation."""
        if not isinstance(data, dict):
            return copy.deepcopy(data)
        result = copy.deepcopy(data)
        for key, field in model.model_fields.items():
            annotation = field.annotation
            field_path = f"{path}.{key}".strip(".")
            if get_origin(annotation) is list and _is_subclass(get_args(annotation)[0], BaseModel):
                numbered = []
                for candidate in result:
                    match = re.fullmatch(re.escape(key) + r"_([1-9][0-9]*)", candidate)
                    if match:
                        numbered.append((int(match[1]), candidate))
                if numbered:
                    if key in result or len(numbered) > 128 or sorted(i for i, _ in numbered) != list(range(1, len(numbered) + 1)):
                        raise ConfigTransferError("任务配置组编号不连续或重复", fields=[field_path])
                    result[key] = [result.pop(candidate) for _, candidate in sorted(numbered)]
                if isinstance(result.get(key), list):
                    result[key] = [self._normalize(item, get_args(annotation)[0], f"{field_path}.{i}")
                                   for i, item in enumerate(result[key])]
            elif _is_subclass(annotation, BaseModel) and key in result:
                result[key] = self._normalize(result[key], annotation, field_path)
        return result

    def _check_structure(self, data, model, path=""):
        if data == REDACTED:
            return
        if not isinstance(data, dict):
            return  # the core schema reports this with the full field path
        for key, value in data.items():
            field_path = f"{path}.{key}".strip(".")
            if key not in model.model_fields:
                raise ConfigTransferError("此私库不支持部分配置参数", fields=[field_path])
            field = model.model_fields[key]
            annotation = field.annotation
            if value == REDACTED:
                continue
            if key in {"md_strategy_count", "invite_info_count", "sup_account_count"}:
                # These legacy before-validators allocate default models based on the count.
                try:
                    count = int(value)
                except (TypeError, ValueError, OverflowError):
                    count = -1
                if not 0 <= count <= 128:
                    raise ConfigTransferError("配置组数量应在 0 至 128 之间", fields=[field_path])
            if _is_subclass(annotation, BaseModel):
                self._check_structure(value, annotation, field_path)
            elif get_origin(annotation) is list:
                inner = get_args(annotation)[0]
                values = value if isinstance(value, list) else [value]
                for index, item in enumerate(values):
                    if _is_subclass(inner, BaseModel):
                        self._check_structure(item, inner, f"{field_path}.{index}")
                    elif _is_subclass(inner, Enum):
                        try:
                            inner(item)
                        except (ValueError, TypeError):
                            raise ConfigTransferError("配置选项不合法", fields=[field_path]) from None
            elif _is_subclass(annotation, Enum):
                try:
                    annotation(value)
                except (ValueError, TypeError):
                    raise ConfigTransferError("配置选项不合法", fields=[field_path]) from None
            for metadata in field.metadata:
                if getattr(getattr(metadata, "func", None), "__name__", "") == "datadelta_validator" and isinstance(value, str):
                    match = re.fullmatch(r"(\d{1,2}) (\d{2}):(\d{2}):(\d{2})", value)
                    if not match or not (int(match[2]) < 24 and int(match[3]) < 60 and int(match[4]) < 60):
                        raise ConfigTransferError("时间间隔格式应为 日数 时:分:秒", fields=[field_path])

    def _validated(self, data):
        data = self._normalize(data, self.model_type)
        self._check_structure(data, self.model_type)
        try:
            validated = self.validator.validate_python(data)
            return validated.model_dump(mode="json", warnings=False)
        except ValidationError as exc:
            fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors(include_input=False)]
            raise ConfigTransferError("配置参数的类型或取值不合法", fields=fields[:40]) from None

    def _validated_task(self, key, data):
        model = self.model_type.model_fields[key].annotation
        data = self._normalize(data, model, key)
        self._check_structure(data, model, key)
        try:
            result = SchemaValidator(_safe_schema(model.__pydantic_core_schema__)).validate_python(data)
            return result.model_dump(mode="json", warnings=False)
        except ValidationError as exc:
            fields = [".".join([key, *(str(part) for part in error["loc"])])
                      for error in exc.errors(include_input=False)]
            raise ConfigTransferError("配置参数的类型或取值不合法", fields=fields[:40]) from None

    def _read(self, name):
        name, path = self._name(name)
        try:
            if path.stat().st_size > MAX_BYTES:
                raise ConfigTransferError("配置 JSON 不能超过 2 MiB", 413)
            raw = path.read_bytes()
        except FileNotFoundError:
            raise ConfigTransferError("配置文件不存在", 404) from None
        except OSError:
            raise ConfigTransferError("无法读取配置文件", 409) from None
        return name, path, parse_json_source(file_content=raw), hashlib.sha256(raw).digest()

    def _write(self, name, path, data, *, previous=None):
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            raise ConfigTransferError("配置包含无法保存的 JSON 值") from None
        if len(content) > MAX_BYTES:
            raise ConfigTransferError("配置 JSON 不能超过 2 MiB", 413)
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".oas-import-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._inactive(name)
            self._name(name, write=True)
            if previous is None:
                # Atomic creation must never replace a config created by another request.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise ConfigTransferError("此配置名称已存在，请使用新名称", 409) from None
            else:
                if not path.exists() or hashlib.sha256(path.read_bytes()).digest() != previous:
                    raise ConfigTransferError("配置刚刚被修改，请刷新后重试", 409)
                os.replace(temporary, path)
        except ConfigTransferError:
            raise
        except OSError:
            raise ConfigTransferError("无法保存配置文件，请检查目录权限", 409) from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        warnings = []
        if self.manager is not None:
            try:
                if name not in self.manager.script_process:
                    self.manager.add_script_file(name)
                # MainManager.config_cache creates a fresh Config on every request.
                self.manager._all_script_files = self.manager.all_script_files()
            except (OSError, MemoryError):
                # The file is already committed. Never report a failed import here.
                warnings.append("配置已保存，但刷新配置列表失败；请刷新前端或稍后重开后台")
        return warnings

    def import_config(self, name, data):
        with _LOCK:
            name, path = self._name(name, write=True)
            self._inactive(name)
            if path.exists():
                raise ConfigTransferError("此配置名称已存在，请使用新名称", 409)
            data = self._normalize(data, self.model_type)
            self._check_structure(data, self.model_type)
            merged = _merge(self._normalize(self._validated({}), self.model_type), data)
            merged["config_name"] = name
            merged["running_task"] = ""
            validated = self._validated(merged)
            for task in validated.values():
                if isinstance(task, dict) and isinstance(task.get("scheduler"), dict):
                    task["scheduler"]["enable"] = False
            refresh_warnings = self._write(name, path, validated)
            return {"name": name, "file": f"{name}.json",
                    "warnings": ["已新建配置，所有任务默认关闭；请确认模拟器和参数后再启用", *refresh_warnings]}

    def _task(self, task_name):
        key = _task_key(task_name)
        field = self.model_type.model_fields.get(key)
        if field is None or key in {"config_name", "running_task"} or not (
                _is_subclass(field.annotation, BaseModel)):
            raise ConfigTransferError("此私库不支持该任务", fields=[key])
        return key

    def import_task(self, config_name, task_name, data):
        with _LOCK:
            name, path = self._name(config_name, write=True)
            self._inactive(name)
            key = self._task(task_name)
            if len(data) != 1 or _task_key(next(iter(data))) != key or not isinstance(next(iter(data.values())), dict):
                raise ConfigTransferError("单任务 JSON 必须只包含当前任务的配置对象", fields=[key])
            _, _, existing, digest = self._read(name)
            incoming = self._normalize({key: next(iter(data.values()))}, self.model_type)[key]
            self._check_structure({key: incoming}, self.model_type)
            model = self.model_type.model_fields[key].annotation
            previous_task = self._normalize(existing.get(key, {}), model, key)
            defaults = self._normalize(self._validated_task(key, {}), model, key)
            updated_task = self._validated_task(key, _merge(_merge(defaults, previous_task), incoming))
            merged = copy.deepcopy(existing)
            merged[key] = updated_task
            # Sharing/copying a task cannot turn on an inactive schedule implicitly.
            if isinstance(merged[key].get("scheduler"), dict):
                merged[key]["scheduler"]["enable"] = existing.get(key, {}).get("scheduler", {}).get("enable", False)
            merged["config_name"] = name
            merged["running_task"] = ""
            refresh_warnings = self._write(name, path, merged, previous=digest)
            return {"config_name": name, "task_name": key, "file": f"{name}.json", "updated": True,
                    "warnings": ["已更新当前任务参数，保留了此任务原有的启用状态及脱敏字段的本地值", *refresh_warnings]}

    def export_config(self, name, mode="share", task_name=None):
        if mode not in {"share", "backup"}:
            raise ConfigTransferError("导出模式应为 backup 或 share")
        name, _, data, _ = self._read(name)
        if task_name is not None:
            key = self._task(task_name)
            if key not in data:
                raise ConfigTransferError("当前配置中未找到该任务", 404)
            data = {key: data[key]}
        if mode == "share":
            data = redact_config(data)
        return name, data
