"""Offline XY -> current private schema converter; never writes or runs a config.

convert_xy_config(data, target_name) returns (candidate, value-free report).
Candidates retain compatible scheduler flags, but do not grant execution ownership.
Run a read-only aggregate audit:
    toolkit/python.exe -B dev_tools/xy_config_migration.py --dry-run SNAPSHOT_DIR

Import in an isolated process: quiet logger/package stubs prevent logger cleanup
and importing the server entrypoint. An already imported logger is never replaced.
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
import sys
import types
from typing import get_args, get_origin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _QuietLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


if "module.logger" not in sys.modules:
    _logger_module = types.ModuleType("module.logger")
    _logger_module.logger = _QuietLogger()
    sys.modules["module.logger"] = _logger_module
if "module.server" not in sys.modules:
    _server_package = types.ModuleType("module.server")
    _server_package.__path__ = [str(ROOT / "module/server")]
    sys.modules["module.server"] = _server_package

from pydantic import BaseModel
from module.config.config_model import ConfigModel
from module.server.config_transfer import (
    ConfigTransferError, ConfigTransferService, _is_subclass, parse_json_source,
)


def _enabled(task):
    return isinstance(task, dict) and _get(task, "scheduler.enable") is True


def _dict(value):
    return value if isinstance(value, dict) else {}


def _get(data, path, default=None):
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data


def _remove(data, path):
    parts = path.split(".")
    for part in parts[:-1]:
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return False
    if isinstance(data, dict) and parts[-1] in data:
        del data[parts[-1]]
        return True
    return False


class Converter:
    def __init__(self, model_type=ConfigModel):
        self.model_type = model_type
        # No config read, manager, scheduler, device, or custom ConfigModel init.
        self.service = ConfigTransferService(ROOT / "config", model_type=model_type)
        self.defaults = self.service._validated({})

    def convert(self, data, target_name):
        if not isinstance(data, dict):
            raise ConfigTransferError("源配置必须是 JSON 对象")
        self.service._name(target_name, write=True)  # validate only, never writes
        original = copy.deepcopy(data)
        source = copy.deepcopy(data)
        changes = []
        blocked = set()
        unsupported = []
        config_blocked = False

        def note(path, action, reason, target=None):
            item = {"path": path, "action": action, "reason": reason}
            if target:
                item["target_path"] = target
            changes.append(item)

        def block(task, path, reason):
            blocked.add(task)
            note(path, "disable_task", reason)

        # XY's idle controller has four modes; private merges emulator thresholds
        # into two modes. Both implementations use >0 for emulator auto-close and
        # <=0 for immediate game close. Plain XY modes never close the emulator.
        optimization = _get(source, "script.optimization", {})
        if isinstance(optimization, dict):
            mode = optimization.get("when_task_queue_empty")
            combined = {"close_emulator_or_goto_main": "goto_main",
                        "close_emulator_or_close_game": "close_game"}
            if mode in combined:
                optimization["when_task_queue_empty"] = combined[mode]
                note("script.optimization.when_task_queue_empty", "map",
                     "按旧等待控制器语义映射模拟器关闭与备用动作")
            if "close_game_limit_time" in optimization:
                optimization["close_game_wait_duration"] = optimization.pop("close_game_limit_time")
                note("script.optimization.close_game_limit_time", "rename",
                     "游戏阈值语义相同：零立即关闭，正值等待超阈值才关闭",
                     "script.optimization.close_game_wait_duration")
            if "close_emulator_limit_time" in optimization:
                old_limit = optimization.pop("close_emulator_limit_time")
                optimization["close_emulator_wait_duration"] = old_limit if mode in combined else "00:00:00"
                note("script.optimization.close_emulator_limit_time", "map",
                     "仅旧模拟器关闭模式保留阈值；普通模式不启用关闭；旧零阈值为禁用",
                     "script.optimization.close_emulator_wait_duration")

        banquet = source.get("guild_banquet", {})
        if isinstance(banquet, dict) and "guild_banquet_config" in banquet:
            if "guild_banquet_time" in banquet:
                block("guild_banquet", "guild_banquet.guild_banquet_config", "新旧寮宴配置组同时存在，需手动确认")
            else:
                banquet["guild_banquet_time"] = banquet.pop("guild_banquet_config")
                note("guild_banquet.guild_banquet_config", "rename", "保留寮宴时间与自动换式神",
                     "guild_banquet.guild_banquet_time")

        # Same-named tasks can still select fork-specific algorithms/loadouts.
        for task, paths, reason in (
            ("dokan", ("dokan_member_battle_conf", "dokan_owner_battle_conf",
                       "dokan_member_switch_soul", "dokan_owner_switch_soul"),
             "成员与馆主的独立阵容结构尚未移植，不能改用默认阵容运行"),
            ("abyss_shadows", ("process_manage", "saved_params"),
             "旧暗域流程与阵容策略不能由当前基础流程等价表达"),
            ("activity_shikigami", ("general_config",),
             "旧活动运行选择器与独立战斗配置尚未映射"),
        ):
            for path in paths:
                if path in _dict(source.get(task)):
                    block(task, f"{task}.{path}", reason)
                    break
        realm = _get(source, "six_realms.six_realms_gate.six_realms_type")
        if realm is not None and realm != "MoonSea":
            block("six_realms", "six_realms.six_realms_gate.six_realms_type", "当前仅支持月之海，不能更换旧所选六道区域")
        if _get(source, "auto_checkin_big_god.checkin_config.manual_claim") is True:
            block("auto_checkin_big_god", "auto_checkin_big_god.checkin_config.manual_claim", "大神手动领取模式未移植，不能自动改为当前签到方法")
        if _get(source, "demon_encounter.demon_battle_config.demon_kiryou_utahime_enable") is True:
            block("demon_encounter", "demon_encounter.demon_battle_config.demon_kiryou_utahime_enable", "普通歌姬独立战斗预设尚未移植")
        if _get(source, "area_boss.boss.Attack_60") is True:
            block("area_boss", "area_boss.boss.Attack_60", "当前不支持旧60级地域鬼王策略")
        boss_number = _get(source, "area_boss.boss.boss_number")
        if boss_number is not None and boss_number != 3:
            block("area_boss", "area_boss.boss.boss_number", "当前不能表达旧地域鬼王挑战数量")

        # weekly_purchase has additional shop/item selectors. Rename only a
        # payload that contains no field beyond the current RichMan schema.
        weekly = source.get("weekly_purchase")
        if isinstance(weekly, dict):
            rich_model = self.model_type.model_fields["rich_man"].annotation
            try:
                self.service._check_structure(weekly, rich_model, "weekly_purchase")
            except ConfigTransferError:
                pass  # handled below as an unsupported task, original retained in snapshot
            else:
                if "rich_man" not in source:
                    source["rich_man"] = source.pop("weekly_purchase")
                    note("weekly_purchase", "rename", "购物字段完全匹配当前结构", "rich_man")

        # Courtyard is now independent. Keep it explicitly off until an operator
        # sets its schedule; do not manufacture consent from target defaults.
        old_harvest = _dict(_get(original, "restart.harvest_config", {}))
        courtyard_eligible = (_enabled(original.get("restart")) and
                              isinstance(old_harvest, dict) and
                              old_harvest.get("enable") is True and
                              old_harvest.get("enable_courtyard_affairs") is True)
        if "courtyard_affairs" in source and not isinstance(source["courtyard_affairs"], dict):
            block("courtyard_affairs", "courtyard_affairs", "独立庭院配置结构非法，恢复默认并保持关闭")
            source.pop("courtyard_affairs")
        if "courtyard_affairs" not in source:
            source["courtyard_affairs"] = {"scheduler": copy.deepcopy(_dict(_get(original, "restart.scheduler", {})))}
        if not isinstance(source["courtyard_affairs"].get("scheduler", {}), dict):
            block("courtyard_affairs", "courtyard_affairs.scheduler", "庭院排程结构非法，恢复默认并保持关闭")
            source["courtyard_affairs"]["scheduler"] = {}
        source["courtyard_affairs"].setdefault("scheduler", {})["enable"] = False
        note("courtyard_affairs.scheduler.enable", "manual_setup", "独立庭院任务保持关闭，需手动设置时间并确认启用")
        if "enable_courtyard_affairs" in old_harvest:
            note("restart.harvest_config.enable_courtyard_affairs", "split", "旧庭院开关已改为独立任务，保留重启排程但不自动启用")
        if _get(original, "daily_trifles.trifles_config.courtyard_affairs") is True:
            note("daily_trifles.trifles_config.courtyard_affairs", "manual_setup", "旧日常庭院需在独立任务中设置；不自动开启")

        def prune(item, model, path=""):
            if not isinstance(item, dict):
                return copy.deepcopy(item)
            result = {}
            if path.endswith(".scheduler") and "enable" in item and type(item["enable"]) is not bool:
                block(path.split(".")[0], f"{path}.enable", "启用标志必须是真正的布尔值，不能按字符串自动启用")
            # Map bounded friend lists to old mainline's explicit slots.
            if "friend_list" in item and {"friend_1", "friend_2", "invite_number"} <= set(model.model_fields):
                friend_list = item["friend_list"]
                if isinstance(friend_list, str):
                    friends = [line.strip() for line in friend_list.splitlines() if line.strip()]
                    if len(friends) <= 2:
                        item = copy.deepcopy(item)
                        item.pop("friend_list")
                        item["friend_1"] = friends[0] if friends else ""
                        item["friend_2"] = friends[1] if len(friends) > 1 else ""
                        item["invite_number"] = "two" if len(friends) == 2 else "one"
                        note(f"{path}.friend_list", "map", "好友列表明确转换到当前一或两人邀请槽")
                    else:
                        block(path.split(".")[0], f"{path}.friend_list", "当前最多两位邀请好友，旧邀请列表超出范围")
            if item.get("green_enable") is True and item.get("green_mark_type") == "name" and "green_mark_type" not in model.model_fields:
                block(path.split(".")[0], f"{path}.green_mark_type", "按式神名字绿标未移植，不能改为默认位置绿标")
            if item.get("continuous_battle") is True and "continuous_battle" not in model.model_fields:
                # XY hardcodes True for NetherWorldBattleConfig. Current Hunt's
                # battle_wait already loops over prepare pages (script_task.py
                # 149-181), so its unlimited default is equivalent. A user set
                # cap or quick-exit remains unsupported and must not be ignored.
                if (path == "hunt.netherworld_battle_config" and
                        item.get("max_continuous", 0) == 0 and
                        item.get("quick_exit", False) is False):
                    note(f"{path}.continuous_battle", "equivalent", "当前阴界战斗等待已内置连续准备循环，保留无限默认模式")
                else:
                    block(path.split(".")[0], f"{path}.continuous_battle", "旧连续战斗控制未移植，不能忽略连战限制")
            for key, value in item.items():
                full = f"{path}.{key}".strip(".")
                if key not in model.model_fields:
                    if not path and isinstance(value, dict) and "scheduler" in value:
                        unsupported.append(key)
                        note(full, "unsupported_task", "私库没有此任务；原配置保留在迁移快照")
                        if _enabled(value):
                            blocked.add(key)
                    else:
                        note(full, "omit", "当前模型不支持此参数；原参数保留在迁移快照")
                    continue
                annotation = model.model_fields[key].annotation
                if _is_subclass(annotation, BaseModel):
                    value = prune(value, annotation, full)
                elif get_origin(annotation) is list and _is_subclass(get_args(annotation)[0], BaseModel) and isinstance(value, list):
                    value = [prune(child, get_args(annotation)[0], f"{full}.{index}") for index, child in enumerate(value)]
                result[key] = copy.deepcopy(value)
            return result

        # Normalize flattened list groups before pruning. Invalid group numbering
        # resets only the affected group and prevents that task from running.
        for _ in range(128):
            try:
                normalized = self.service._normalize(source, self.model_type)
                break
            except ConfigTransferError as exc:
                if not exc.fields:
                    raise
                for path in exc.fields:
                    task, _, group = path.partition(".")
                    block(task, path, "旧列表编号无效，采用当前默认组并停用受影响任务")
                    container = source
                    components = path.split(".")
                    for component in components[:-1]:
                        container = container.get(component, {}) if isinstance(container, dict) else {}
                    group = components[-1]
                    if isinstance(container, dict):
                        for key in list(container):
                            if key == group or key.startswith(group + "_"):
                                container.pop(key)
        else:
            raise ConfigTransferError("配置组无法完成转换")
        candidate = prune(normalized, self.model_type)
        candidate["config_name"] = target_name
        candidate["running_task"] = ""

        # Strictly validate and repair isolated invalid inputs. Do not use the
        # real ConfigModel constructor or ConfigBase's silent range fallback.
        for _ in range(256):
            try:
                candidate = self.service._validated(candidate)
                break
            except ConfigTransferError as exc:
                repaired = False
                for path in exc.fields:
                    task = path.split(".")[0]
                    if task in {"script", "global_game"}:
                        config_blocked = True
                        note(path, "reset_invalid", "全局或设备字段非法，使用默认值并阻止此账号所有任务")
                    else:
                        block(task, path, "参数不符合当前严格模型；该参数恢复默认并停用受影响任务")
                    if _remove(candidate, path):
                        repaired = True
                    elif task in candidate:
                        # Group-level validators may return an empty relative
                        # path; resetting just this task is safer than defaults
                        # that silently choose a different selected algorithm.
                        candidate[task] = copy.deepcopy(self.defaults[task])
                        repaired = True
                if not repaired:
                    raise ConfigTransferError("配置不能完成严格转换", fields=exc.fields) from None
        else:
            raise ConfigTransferError("配置修正次数超限")

        for task, value in candidate.items():
            if isinstance(value, dict) and "scheduler" in value:
                if task not in source:
                    value["scheduler"]["enable"] = False
                if config_blocked or task in blocked or task == "courtyard_affairs":
                    value["scheduler"]["enable"] = False
        candidate = self.service._validated(candidate)
        enabled_tasks = sorted(task for task, value in candidate.items() if _enabled(value))
        original_enabled = {task for task, value in original.items() if _enabled(value)}
        renamed_weekly = any(c["path"] == "weekly_purchase" and c["action"] == "rename" for c in changes)
        final_original_enabled = set(enabled_tasks)
        if renamed_weekly and "rich_man" in final_original_enabled:
            final_original_enabled.add("weekly_purchase")
        disabled_tasks = sorted(original_enabled - final_original_enabled)
        if config_blocked:
            disabled_tasks = sorted(original_enabled)
        report = {"version": 1, "validated": True, "blocked_config": config_blocked,
                  "enabled_tasks": enabled_tasks, "disabled_tasks": disabled_tasks,
                  "unsupported_tasks": sorted(unsupported), "field_changes": changes,
                  "courtyard_legacy_eligible": courtyard_eligible,
                  "manual_tasks": ["courtyard_affairs"],
                  "counts": {"source_enabled": len(original_enabled), "enabled": len(enabled_tasks),
                             "disabled": len(disabled_tasks), "unsupported": len(unsupported),
                             "field_changes": len(changes)}}
        return candidate, report


def convert_xy_config(data, target_name):
    return Converter().convert(data, target_name)


def dry_run(source_dir):
    converter = Converter()
    reports = []
    paths = sorted(path for path in Path(source_dir).glob("*.json") if path.stem.casefold() not in {"template", "home"})
    for index, path in enumerate(paths):
        data = parse_json_source(file_content=path.read_bytes())
        candidate, report = converter.convert(data, f"xy-audit-{index + 1}")
        # Exact preservation checks produce counts, never device/account values.
        for field in ("serial", "package_name", "handle", "emulatorinfo_name", "emulatorinfo_path"):
            old = _get(data, "script.device." + field)
            if old is not None and old != _get(candidate, "script.device." + field):
                raise ConfigTransferError("设备身份字段未精确保留", fields=["script.device." + field])
        reports.append(report)
    summary = {"accounts": len(reports), "validated": sum(r["validated"] for r in reports),
               "blocked_configs": sum(r["blocked_config"] for r in reports),
               "enabled_tasks": dict(Counter(task for r in reports for task in r["enabled_tasks"])),
               "disabled_tasks": dict(Counter(task for r in reports for task in r["disabled_tasks"])),
               "unsupported_tasks": dict(Counter(task for r in reports for task in r["unsupported_tasks"])),
               "field_changes": dict(Counter(c["path"] for r in reports for c in r["field_changes"])),
               "courtyard_legacy_eligible": sum(r["courtyard_legacy_eligible"] for r in reports),
               "device_identity_checks": len(reports) * 5}
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读验证 XY 配置，输出不含私值的汇总")
    parser.add_argument("--dry-run", required=True, metavar="SNAPSHOT_DIR")
    args = parser.parse_args()
    try:
        print(json.dumps(dry_run(args.dry_run), ensure_ascii=False, indent=2))
    except ConfigTransferError as error:
        print(json.dumps({"error": str(error), "fields": error.fields}, ensure_ascii=False))
        sys.exit(1)
