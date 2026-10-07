"""Read-only, bounded statistics from scheduler logs; never imports OAS runtime."""
from __future__ import annotations

import copy
import re
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DETAIL = (
    "按调度日志恢复任务区间；运行次数包含尚未闭合的区间，已结束仅表示调度返回。"
    "总时长为观测到的任务活动时长。确认结算按结果日志计数，保段位主动退出不计。"
    "日志时长包含准备或结果确认步骤；未记录的战斗时长不作推算。"
    "未闭合区间标为未记录结束，不能仅凭日志确认进程仍存活。"
)
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s*\|[^|]*\|\s*(\w+)\s*\|\s*(.*?)\s*$")
_TASK = re.compile(r"^Scheduler: (Start|End) task `([A-Za-z][A-Za-z0-9_]{0,127})`$")
_OWNER = re.compile(r"^Start scheduler loop: (.+?)\s*$")
_START_BOUNDARY = re.compile(r"^(?:─{10,}\s*START\s*─{10,}|═{10,}\s+START\s+═{10,})$")
_CHESS_COUNT = re.compile(r"^Chess (?:completed games:|task loop finished: completed=)\s*(\d+)\b")
_LOG_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)\.txt$")
_ERROR = re.compile(r"Game(?:Stuck|TooManyClick|PageUnknown|Bug)Error|Request human takeover|Task name mismatch|ScriptError", re.I)
_RESULTS = {"Win battle", "False battle", "Battle result is win", "Battle result is false", "Reconfirm the results of the battle"}
_BONDLING_RESULTS = {"Catch success", "Catch failure"}
_METRICS = ["run_count", "total_duration_seconds", "completed_run_count"]


def source(**extra: Any) -> dict[str, Any]:
    return {"kind": "local_logs", "label": "本地日志解析", "detail": SOURCE_DETAIL, **extra}


def stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class StatisticsInputError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class BattleSummary:
    count: int = 0
    attempts: int = 0
    duration_sum: float = 0.0
    duration_samples: int = 0

    def merge(self, other: "BattleSummary") -> None:
        self.count += other.count
        self.attempts += other.attempts
        self.duration_sum += other.duration_sum
        self.duration_samples += other.duration_samples

    def payload(self, supported: bool) -> dict[str, Any] | None:
        if not supported:
            return None
        known = self.count > 0 and self.duration_samples == self.count
        return {
            "count": self.count,
            "avg_duration_seconds": round(self.duration_sum / self.count, 3) if known else None,
            "duration_sample_count": self.duration_samples,
            "duration_recorded": known,
        }


@dataclass
class Run:
    name: str
    start: datetime
    last: datetime
    failed: bool = False
    battle_supported: bool = False
    battles: dict[str, BattleSummary] = field(default_factory=dict)
    battle_start: datetime | None = None
    battle_kind: str | None = None
    battle_confirmed: bool = False
    chess_completed: int = 0

    def battle_for(self, ts: datetime) -> BattleSummary:
        return self.battles.setdefault(ts.date().isoformat(), BattleSummary())


@dataclass
class TaskSummary:
    run_count: int = 0
    started_run_count: int = 0
    duration: float = 0.0
    statuses: dict[str, int] = field(default_factory=lambda: {"completed": 0, "running": 0, "interrupted": 0, "incomplete": 0})
    battle_supported: bool = False
    battle: BattleSummary = field(default_factory=BattleSummary)
    runs: deque = field(default_factory=lambda: deque(maxlen=200))

    def add(self, run: dict[str, Any], battle: BattleSummary, supported: bool) -> None:
        self.run_count += 1
        self.started_run_count += int(run["started_on_selected_date"])
        self.duration += run["duration_seconds"]
        self.statuses[run["status"]] += 1
        self.battle_supported |= supported
        self.battle.merge(battle)
        self.runs.append(run)

    def payload(self) -> dict[str, Any]:
        metrics = list(_METRICS)
        battle = self.battle.payload(self.battle_supported)
        if self.battle_supported:
            metrics.extend(["battle_count", "battle_attempt_count"])
        if battle and battle["duration_recorded"]:
            metrics.append("battle_avg_duration_seconds")
        return {
            "run_count": self.run_count,
            "started_run_count": self.started_run_count,
            "completed_run_count": self.statuses["completed"],
            "running_run_count": self.statuses["running"],
            "interrupted_run_count": self.statuses["interrupted"],
            "incomplete_run_count": self.statuses["incomplete"],
            "total_duration_seconds": round(self.duration, 3),
            "battle": battle,
            "battle_attempt_count": self.battle.attempts if self.battle_supported else None,
            "runs": list(self.runs),
            "runs_truncated": self.run_count > len(self.runs),
            "available_metrics": metrics,
        }


class LogStatsParser:
    """Consume complete lines in chronological file order, without retaining lines."""

    def __init__(self, script_name: str = "", *, trust_initial_owner: bool = True):
        self.script_name = script_name
        self.capture = trust_initial_owner
        self.active: Run | None = None
        self.summary: OrderedDict[str, dict[str, TaskSummary]] = OrderedDict()
        self.observed_days: set[str] = set()
        self.coverage_start: datetime | None = None
        self.coverage_end: datetime | None = None
        self.pending_legacy_start = False
        self.days_truncated = False

    def consume_line(self, line: str) -> None:
        stripped = line.strip()
        if _START_BOUNDARY.fullmatch(stripped):
            # Startup cleanup logs precede the scheduler-owner line. Close now,
            # before their timestamps can extend a run from an older session.
            self._close("interrupted", "session_restart")
            self.pending_legacy_start = False
            return
        if self.capture and "GENERAL BATTLE START" in stripped and set(stripped[:10]) <= {"─"}:
            self.pending_legacy_start = True
            return
        match = _TS.match(stripped)
        if not match:
            return
        try:
            ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return
        level, message = match.group(2), match.group(3)
        if message == "START":
            self._close("interrupted", "session_restart")
            self.pending_legacy_start = False
            return
        owner = _OWNER.match(message)
        if owner:
            self._close("interrupted", "scheduler_restarted")
            self.capture = not self.script_name or owner.group(1) == self.script_name
            self.pending_legacy_start = False
        if not self.capture:
            return
        self.observed_days.add(ts.date().isoformat())
        if len(self.observed_days) > 64:
            self.observed_days.remove(min(self.observed_days))
            self.days_truncated = True
        self.coverage_start = ts if self.coverage_start is None else min(ts, self.coverage_start)
        self.coverage_end = ts if self.coverage_end is None else max(ts, self.coverage_end)
        task = _TASK.match(message)
        if task and task.group(1) == "Start":
            self._close("interrupted", "next_task_started_without_end")
            self.active = Run(task.group(2), ts, ts, battle_supported=task.group(2).lower() == "chess")
            self.pending_legacy_start = False
            return
        run = self.active
        if run is None:
            return
        if ts >= run.last:
            run.last = ts
        if task and task.group(1) == "End":
            if task.group(2).lower() == run.name.lower():
                self._close("interrupted" if run.failed else "completed", "task_error" if run.failed else "scheduler_end")
            return
        if _ERROR.search(message) and level in {"ERROR", "CRITICAL"}:
            run.failed = True
        if ((message.startswith("Script ") and (" process exit" in message or "exiting gracefully" in message))
                or message.startswith("Run script ") and message.endswith(" error")):
            self._close("interrupted", "process_exit")
            return
        if run.name.lower() == "chess":
            self._consume_chess(run, ts, message)
            return
        if self.pending_legacy_start:
            self._start_battle(run, ts, "legacy")
            self.pending_legacy_start = False
        if message == "Start battle process":
            # Some legacy callers also emit the new setup hook for the same battle.
            if run.battle_kind == "legacy" and not run.battle_confirmed:
                run.battle_kind = "new"
            else:
                self._start_battle(run, ts, "new")
        elif message.startswith("Current count:") and run.battle_kind is None:
            self._start_battle(run, ts, "legacy")
        elif message in _RESULTS or (run.name.lower() == "bondlingfairyland" and message in _BONDLING_RESULTS):
            # BondlingBattle reports capture outcomes only after reaching rewards.
            # Its outer "Catch successful..." message describes returning to the
            # capture screen and must not be treated as a settled battle.
            if not run.battle_confirmed:
                self._confirm_battle(run, ts)
            # Completion-hook log lines are deliberately never counted.

    def consume_lines(self, lines) -> None:
        for line in lines:
            self.consume_line(line)

    def _start_battle(self, run: Run, ts: datetime, kind: str) -> None:
        run.battle_supported = True
        run.battle_for(ts).attempts += 1
        run.battle_start, run.battle_kind, run.battle_confirmed = ts, kind, False

    @staticmethod
    def _confirm_battle(run: Run, ts: datetime, count: int = 1, *, known_duration: bool = True) -> None:
        run.battle_supported = True
        summary = run.battle_for(ts)
        summary.count += count
        if known_duration and count == 1 and run.battle_start is not None and ts >= run.battle_start:
            summary.duration_sum += (ts - run.battle_start).total_seconds()
            summary.duration_samples += 1
        run.battle_confirmed = True

    def _consume_chess(self, run: Run, ts: datetime, message: str) -> None:
        if message == "Chess matchmaking complete: entered battle":
            self._start_battle(run, ts, "chess")
        elif message.startswith("Chess rank-protection exit completed:") or "Chess rank protection: actively exit" in message:
            run.battle_start, run.battle_kind = None, None
        matched = _CHESS_COUNT.match(message)
        if matched:
            completed = int(matched.group(1))
            delta = completed - run.chess_completed
            if delta > 0:
                self._confirm_battle(run, ts, delta, known_duration=run.battle_kind == "chess")
                run.chess_completed = completed
                run.battle_start, run.battle_kind = None, None

    def _run_parts(self, run: Run, status: str, reason: str):
        end = max(run.start, run.last)
        first = max(run.start.date(), end.date() - timedelta(days=63))
        if first > run.start.date():
            self.days_truncated = True
        day = first
        while day <= end.date():
            begin = max(run.start, datetime.combine(day, time.min))
            finish = min(end, datetime.combine(day + timedelta(days=1), time.min))
            day_key = day.isoformat()
            battle_summary = run.battles.get(day_key, BattleSummary())
            if begin == finish and day != run.start.date() and not (battle_summary.count or battle_summary.attempts):
                break
            battle = battle_summary.payload(run.battle_supported)
            metrics = ["duration_seconds"]
            if run.battle_supported:
                metrics.extend(["battle_count", "battle_attempt_count"])
            if battle and battle["duration_recorded"]:
                metrics.append("battle_avg_duration_seconds")
            payload = {
                "start_time": stamp(begin), "end_time": stamp(finish),
                "duration_seconds": round((finish - begin).total_seconds(), 3),
                "observed_start_time": stamp(run.start), "observed_end_time": stamp(end),
                "status": status, "status_reason": reason,
                "completion_confirmed": status == "completed", "success_confirmed": None,
                "ended_on_selected_date": reason == "scheduler_end" and day == end.date(),
                "started_on_selected_date": day == run.start.date(),
                "continues_from_previous_date": day > run.start.date(),
                "continues_to_next_date": day < end.date(),
                "battle": battle,
                "battle_attempt_count": battle_summary.attempts if run.battle_supported else None,
                "available_metrics": metrics,
            }
            yield day_key, payload, battle_summary
            day += timedelta(days=1)

    def _close(self, status: str, reason: str) -> None:
        if self.active is None:
            return
        run, self.active = self.active, None
        for day, payload, battle in self._run_parts(run, status, reason):
            task = self.summary.setdefault(day, {}).setdefault(run.name, TaskSummary())
            task.add(payload, battle, run.battle_supported)
            self.summary.move_to_end(day)
        while len(self.summary) > 64:
            self.summary.popitem(last=False)
            self.days_truncated = True
        self.pending_legacy_start = False

    def snapshot(self, target_day: date, *, today: date | None = None) -> dict[str, Any]:
        day_key = target_day.isoformat()
        tasks = copy.deepcopy(self.summary.get(day_key, {}))
        if self.active:
            status = "interrupted" if self.active.failed else "incomplete"
            reason = "task_error" if self.active.failed else "unclosed_interval"
            for day, payload, battle in self._run_parts(self.active, status, reason):
                if day == day_key:
                    tasks.setdefault(self.active.name, TaskSummary()).add(payload, battle, self.active.battle_supported)
        task_payloads = {name: task.payload() for name, task in sorted(tasks.items())}
        metrics = ["total_runtime_seconds", "total_task_run_count", "completed_run_count"]
        if any(task.battle_supported for task in tasks.values()):
            metrics.append("total_battle_count")
        return {
            "script_name": self.script_name, "date": day_key,
            "total_runtime_seconds": round(sum(task.duration for task in tasks.values()), 3),
            "total_task_run_count": sum(task.run_count for task in tasks.values()),
            "total_battle_count": sum(task.battle.count for task in tasks.values()),
            "started_run_count": sum(task.started_run_count for task in tasks.values()),
            "completed_run_count": sum(task.statuses["completed"] for task in tasks.values()),
            "running_run_count": sum(task.statuses["running"] for task in tasks.values()),
            "interrupted_run_count": sum(task.statuses["interrupted"] for task in tasks.values()),
            "incomplete_run_count": sum(task.statuses["incomplete"] for task in tasks.values()),
            "tasks": task_payloads, "available_metrics": metrics, "source": source(),
        }

    def dates(self) -> list[str]:
        days = self.observed_days | set(self.summary)
        if self.active:
            days.update(day for day, _, _ in self._run_parts(self.active, "incomplete", "unclosed_interval"))
        return sorted(days, reverse=True)


@dataclass
class FileCursor:
    position: int = 0
    pending: bytes = b""
    identity: tuple[int, int] | None = None
    mtime_ns: int = 0
    discarded_long_line: bool = False
    prefix: bytes = b""
    anchor: bytes = b""


@dataclass
class ScriptCache:
    parser: LogStatsParser
    cursors: OrderedDict[Path, FileCursor] = field(default_factory=OrderedDict)
    source_info: dict[str, Any] = field(default_factory=dict)


class LogStatsService:
    MAX_SCRIPTS = 8
    MAX_FILES = 32
    READ_BUDGET = 8 * 1024 * 1024
    MAX_LINE_BYTES = 1024 * 1024

    def __init__(self, project_root: Path | str = PROJECT_ROOT, *, now: Callable[[], datetime] = datetime.now):
        self.root = Path(project_root).resolve()
        self.log_root = (self.root / "log").resolve()
        self.config_root = (self.root / "config").resolve()
        self.now = now
        self._cache: OrderedDict[str, ScriptCache] = OrderedDict()
        self._lock = threading.RLock()

    def _config(self, requested: str) -> tuple[str, str]:
        self._ensure_roots()
        if (not requested or requested != requested.strip() or len(requested) > 160
                or requested in {".", "..", "template"} or requested.endswith((".", " "))
                or any(ord(char) < 32 or char in '/\\:*?"<>|' for char in requested)):
            raise StatisticsInputError("Invalid configuration name")
        names = [path.stem for path in self.config_root.glob("*.json")
                 if path.is_file() and path.resolve().parent == self.config_root and path.stem != "template"]
        actual = next((name for name in names if name.casefold() == requested.casefold()), None)
        if actual is None:
            raise StatisticsInputError("Configuration does not exist", 404)
        alias = actual.split("_", 1)[0]
        owners = [name for name in names if name.split("_", 1)[0].casefold() == alias.casefold()]
        if len(owners) != 1:
            raise StatisticsInputError("Multiple configurations share the logger name; their logs cannot be attributed safely", 409)
        return actual, alias

    def _ensure_roots(self) -> None:
        for name, directory in (("config", self.config_root), ("log", self.log_root)):
            current = (self.root / name).resolve()
            if self.root not in current.parents or current != directory or self.root not in directory.parents:
                raise StatisticsInputError("Statistics directories must remain inside the project", 403)

    def _files(self, alias: str) -> tuple[list[Path], int]:
        files = []
        if self.log_root.exists():
            for path in self.log_root.iterdir():
                matched = _LOG_NAME.match(path.name)
                if not matched or matched.group(2).casefold() != alias.casefold():
                    continue
                try:
                    date.fromisoformat(matched.group(1))
                    if path.is_file() and path.resolve().parent == self.log_root:
                        files.append(path)
                except (ValueError, OSError):
                    continue
        files.sort(key=lambda path: path.name)
        return files[-self.MAX_FILES:], max(0, len(files) - self.MAX_FILES)

    @staticmethod
    def _new_cache(name: str, alias: str) -> ScriptCache:
        return ScriptCache(LogStatsParser(name, trust_initial_owner=name == alias))

    def _refresh(self, name: str, alias: str) -> ScriptCache:
        files, omitted = self._files(alias)
        cache = self._cache.get(name)
        stats = {}
        for path in files:
            try:
                stats[path] = path.stat()
            except OSError:
                continue
        files = [path for path in files if path in stats]
        reset = cache is None
        if cache is not None:
            previous = list(cache.cursors)
            if files[:len(previous)] != previous:
                reset = True
            for index, path in enumerate(files):
                cursor = cache.cursors.get(path)
                if cursor is None:
                    continue
                stat = stats[path]
                changed = stat.st_mtime_ns != cursor.mtime_ns
                replaced = cursor.identity is not None and cursor.identity != (stat.st_dev, stat.st_ino)
                later_read = any(cache.cursors.get(later, FileCursor()).position for later in files[index + 1:])
                if replaced or stat.st_size < cursor.position or (changed and stat.st_size == cursor.position and cursor.position > 0):
                    reset = True
                if changed and stat.st_size > cursor.position and later_read:
                    reset = True
                if changed and stat.st_size > cursor.position and cursor.position and cursor.anchor:
                    # A copytruncate may already be larger than the old cursor by the next poll.
                    with path.open("rb") as handle:
                        prefix = handle.read(len(cursor.prefix))
                        handle.seek(cursor.position - len(cursor.anchor))
                        anchor = handle.read(len(cursor.anchor))
                    if prefix != cursor.prefix or anchor != cursor.anchor:
                        reset = True
        if reset:
            cache = self._new_cache(name, alias)
        assert cache is not None
        budget = self.READ_BUDGET
        discarded = False
        for path in files:
            cursor = cache.cursors.setdefault(path, FileCursor())
            stat = stats[path]
            cursor.identity = (stat.st_dev, stat.st_ino)
            if stat.st_size > cursor.position:
                if budget <= 0:
                    break
                try:
                    with path.open("rb") as handle:
                        handle.seek(cursor.position)
                        remaining = min(stat.st_size - cursor.position, budget)
                        while remaining > 0:
                            chunk = handle.read(min(65536, remaining))
                            if not chunk:
                                break
                            if len(cursor.prefix) < 128:
                                cursor.prefix += chunk[:128 - len(cursor.prefix)]
                            cursor.anchor = (cursor.anchor + chunk)[-128:]
                            cursor.position += len(chunk)
                            budget -= len(chunk)
                            remaining -= len(chunk)
                            parts = (cursor.pending + chunk).split(b"\n")
                            cursor.pending = parts.pop()
                            for line in parts:
                                if cursor.discarded_long_line:
                                    cursor.discarded_long_line = False
                                    discarded = True
                                    continue
                                cache.parser.consume_line(line.decode("utf-8", errors="replace").rstrip("\r"))
                            if len(cursor.pending) > self.MAX_LINE_BYTES:
                                cursor.pending = b""
                                cursor.discarded_long_line = True
                                discarded = True
                except OSError:
                    # Concurrent rotation/deletion is retried from a clean cache next time.
                    self._cache.pop(name, None)
                    raise
            cursor.mtime_ns = stat.st_mtime_ns
            if cursor.position < stat.st_size:
                break
        pending = any(cache.cursors.get(path, FileCursor()).position < stats[path].st_size for path in files)
        cache.source_info = source(
            log_alias=alias, file_count=len(files), files_truncated=omitted,
            backfill_pending=pending, days_truncated=cache.parser.days_truncated,
            partial_line_count=sum(bool(cursor.pending) for cursor in cache.cursors.values()),
            discarded_long_lines=discarded or cache.source_info.get("discarded_long_lines", False),
            coverage_start=stamp(cache.parser.coverage_start) if cache.parser.coverage_start else None,
            coverage_end=stamp(cache.parser.coverage_end) if cache.parser.coverage_end else None,
            runtime_scope="observed_task_intervals", process_liveness_confirmed=False,
        )
        self._cache[name] = cache
        self._cache.move_to_end(name)
        while len(self._cache) > self.MAX_SCRIPTS:
            self._cache.popitem(last=False)
        return cache

    def list_available_dates(self, script_name: str) -> dict[str, Any]:
        with self._lock:
            name, alias = self._config(script_name)
            cache = self._refresh(name, alias)
            return {"script_name": name, "dates": cache.parser.dates(), "source": copy.deepcopy(cache.source_info),
                    "warnings": self._warnings(cache), "statistics_complete": not cache.source_info["backfill_pending"]}

    @staticmethod
    def _warnings(cache: ScriptCache) -> list[str]:
        warnings = []
        if cache.source_info["backfill_pending"]:
            warnings.append("日志仍在分批读取，当前结果为部分数据；累计指标将在读取完成后显示。")
        if cache.source_info["files_truncated"] or cache.source_info["days_truncated"]:
            warnings.append("统计只覆盖最近保留的日志；更早数据未纳入当前结果。")
        if cache.source_info["discarded_long_lines"]:
            warnings.append("超长日志行已跳过，统计可能不完整。")
        return warnings

    def build_stats(self, script_name: str, target_day: date) -> dict[str, Any]:
        with self._lock:
            name, alias = self._config(script_name)
            cache = self._refresh(name, alias)
            payload = cache.parser.snapshot(target_day, today=self.now().date())
            payload["source"] = copy.deepcopy(cache.source_info)
            payload["warnings"] = self._warnings(cache)
            payload["statistics_complete"] = not cache.source_info["backfill_pending"]
            if cache.source_info["backfill_pending"]:
                payload["available_metrics"] = []
                for task in payload["tasks"].values():
                    task["available_metrics"] = []
            return payload


log_stats_service = LogStatsService()
