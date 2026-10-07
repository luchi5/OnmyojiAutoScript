"""Lightweight standard-library tests; never imports the running OAS modules."""
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("oas_log_stats_test_target", ROOT / "module/server/log_stats.py")
stats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stats
SPEC.loader.exec_module(stats)
DAY = date(2026, 10, 5)


def row(clock, message, level="INFO", day="2026-10-05"):
    return f"{day} {clock}.000 | script.py:0001 | {level} | {message}\n"


def snapshot(lines, day=DAY):
    parser = stats.LogStatsParser("09")
    parser.consume_lines(lines)
    return parser.snapshot(day, today=DAY)


class ParserTests(unittest.TestCase):
    def test_scheduler_end_closes_before_wait_and_must_match_task(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"),
            row("10:00:03", "Scheduler: End task `Orochi`"),
            row("10:00:10", "Scheduler: End task `Chess`"),
            row("11:00:00", "Goto main page during wait"),
        ])
        task = data["tasks"]["Chess"]
        self.assertEqual(task["total_duration_seconds"], 10)
        self.assertEqual(task["runs"][0]["status"], "completed")
        self.assertIsNone(task["runs"][0]["success_confirmed"])

    def test_unclosed_run_is_not_claimed_live_or_completed(self):
        data = snapshot([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:04", "poll")])
        self.assertEqual(data["completed_run_count"], 0)
        self.assertEqual(data["incomplete_run_count"], 1)
        self.assertEqual(data["tasks"]["Chess"]["runs"][0]["status"], "incomplete")

    def test_scheduler_restart_closes_previous_at_last_observed_time(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:04", "poll"),
            row("11:00:00", "Start scheduler loop: 09"),
            row("11:00:01", "Scheduler: Start task `Chess`"), row("11:00:05", "poll"),
        ])
        task = data["tasks"]["Chess"]
        self.assertEqual(task["run_count"], 2)
        self.assertEqual(task["total_duration_seconds"], 8)
        self.assertEqual(task["runs"][0]["status"], "interrupted")

    def test_process_exit_closes_as_interrupted(self):
        data = snapshot([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:04", "Script 09 process exit")])
        self.assertEqual(data["interrupted_run_count"], 1)
        self.assertEqual(data["incomplete_run_count"], 0)

    def test_start_separator_closes_before_startup_cleanup_and_owner(self):
        data = snapshot([
            row("17:53:48", "Scheduler: Start task `Chess`"),
            row("18:20:10", "last observation before shutdown"),
            "════════════════════════════════════════════════════════════════════════════════\n",
            "──────────────────────────────────── START ─────────────────────────────────────\n",
            "════════════════════════════════════════════════════════════════════════════════\n",
            row("19:04:57", "Log cleanup finished"),
            row("19:04:58", "Start scheduler loop: 09"),
            row("19:05:00", "Scheduler: Start task `Chess`"),
            row("19:05:05", "last observation"),
        ])
        runs = data["tasks"]["Chess"]["runs"]
        self.assertEqual(runs[0]["end_time"], "2026-10-05 18:20:10.000")
        self.assertEqual(runs[0]["duration_seconds"], 1582)
        self.assertEqual(runs[0]["status_reason"], "session_restart")
        self.assertEqual(data["total_runtime_seconds"], 1587)

    def test_exact_start_title_variants_close_session(self):
        for marker in ("════════════════ START ════════════════\n", row("19:04:57", "START")):
            with self.subTest(marker=marker):
                data = snapshot([
                    row("17:53:48", "Scheduler: Start task `Chess`"),
                    row("18:20:10", "last observation"), marker,
                    row("19:04:58", "Log cleanup finished"),
                ])
                self.assertEqual(data["tasks"]["Chess"]["runs"][0]["end_time"], "2026-10-05 18:20:10.000")
                self.assertEqual(data["interrupted_run_count"], 1)

    def test_non_start_title_does_not_close_active_session(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"),
            "──────────────── RESTART ────────────────\n",
            row("10:00:05", "restart preparation"),
        ])
        self.assertEqual(data["tasks"]["Chess"]["runs"][0]["status"], "incomplete")
        self.assertEqual(data["total_runtime_seconds"], 5)

    def test_error_then_scheduler_end_is_not_successful_completion(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"),
            row("10:00:02", "GameStuckError: Chess stuck", "ERROR"),
            row("10:00:04", "Scheduler: End task `Chess`"),
        ])
        self.assertEqual(data["interrupted_run_count"], 1)
        self.assertEqual(data["completed_run_count"], 0)

    def test_new_battle_results_deduplicate_completion_and_result_retries(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Orochi`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Win battle"),
            row("10:00:06", "Win battle"), row("10:00:07", "Battle completion process"),
            row("10:00:08", "Battle completion process"), row("10:00:10", "Start battle process"),
            row("10:00:15", "False battle", "WARNING"), row("10:00:16", "Scheduler: End task `Orochi`"),
        ])
        task = data["tasks"]["Orochi"]
        self.assertEqual(task["battle"]["count"], 2)
        self.assertEqual(task["battle_attempt_count"], 2)
        self.assertEqual(task["battle"]["avg_duration_seconds"], 4.5)

    def test_legacy_attempt_without_result_is_not_confirmed_battle(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Orochi`"),
            "──────────────── GENERAL BATTLE START ────────────────\n",
            row("10:00:01", "Current count: 1"), row("10:00:20", "Scheduler: End task `Orochi`"),
        ])
        task = data["tasks"]["Orochi"]
        self.assertEqual(task["battle_attempt_count"], 1)
        self.assertEqual(task["battle"]["count"], 0)
        self.assertIsNone(task["battle"]["avg_duration_seconds"])

    def test_legacy_and_new_markers_for_same_attempt_count_once(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Orochi`"),
            "──────────────── GENERAL BATTLE START ────────────────\n",
            row("10:00:01", "Current count: 1"), row("10:00:02", "Start battle process"),
            row("10:00:05", "Battle result is win"), row("10:00:06", "Scheduler: End task `Orochi`"),
        ])
        self.assertEqual(data["tasks"]["Orochi"]["battle_attempt_count"], 1)
        self.assertEqual(data["total_battle_count"], 1)

    def test_chess_cumulative_max_does_not_count_protection_exits(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"),
            row("10:00:03", "Chess game ended: 第3名"),
            row("10:00:04", "Chess completed games: 1/1, rank_protection_exits_pending=2"),
            row("10:00:05", "Chess game ended: result-page rank OCR unavailable [None]", "WARNING"),
            row("10:00:06", "Chess rank-protection exit completed: remaining=1/2, completed_games=1"),
            row("10:00:08", "Chess task loop finished: completed=1, target=1"),
            row("10:00:10", "Scheduler: End task `Chess`"),
        ])
        battle = data["tasks"]["Chess"]["battle"]
        self.assertEqual(battle["count"], 1)
        self.assertIsNone(battle["avg_duration_seconds"])
        self.assertNotIn("battle_avg_duration_seconds", data["tasks"]["Chess"]["available_metrics"])

    def test_chess_timed_result_and_cumulative_jump_have_unknown_full_average(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `Chess`"),
            row("10:00:01", "Chess matchmaking complete: entered battle"),
            row("10:00:04", "Chess completed games: 1/5"),
            row("10:00:07", "Chess completed games: 3/5"),
            row("10:00:10", "Scheduler: End task `Chess`"),
        ])
        battle = data["tasks"]["Chess"]["battle"]
        self.assertEqual(battle["count"], 3)
        self.assertEqual(battle["duration_sample_count"], 1)
        self.assertIsNone(battle["avg_duration_seconds"])

    def test_midnight_clips_runtime_but_assigns_result_to_result_day(self):
        parser = stats.LogStatsParser("09")
        parser.consume_lines([
            row("23:59:50", "Scheduler: Start task `Chess`", day="2026-10-04"),
            row("00:00:05", "Chess completed games: 1/1"),
            row("00:00:20", "Scheduler: End task `Chess`"),
        ])
        yesterday = parser.snapshot(date(2026, 10, 4))
        today = parser.snapshot(DAY)
        self.assertEqual(yesterday["total_runtime_seconds"], 10)
        self.assertEqual(today["total_runtime_seconds"], 20)
        self.assertEqual(yesterday["total_battle_count"], 0)
        self.assertEqual(today["total_battle_count"], 1)
        self.assertEqual(today["started_run_count"], 0)

    def test_other_owner_is_not_attributed_to_requested_config(self):
        data = snapshot([
            row("10:00:00", "Start scheduler loop: another"),
            row("10:00:01", "Scheduler: Start task `Chess`"),
            row("10:00:02", "Scheduler: End task `Chess`"),
        ])
        self.assertEqual(data["total_task_run_count"], 0)

    def test_exact_midnight_result_is_kept_on_zero_duration_day_part(self):
        parser = stats.LogStatsParser("09")
        parser.consume_lines([
            row("23:59:50", "Scheduler: Start task `Orochi`", day="2026-10-04"),
            row("23:59:51", "Start battle process", day="2026-10-04"),
            row("00:00:00", "Win battle"), row("00:00:00", "Scheduler: End task `Orochi`"),
        ])
        result = parser.snapshot(DAY)
        self.assertEqual(result["total_battle_count"], 1)
        self.assertEqual(result["total_runtime_seconds"], 0)


class BondlingParserTests(unittest.TestCase):
    """Result recognition uses exact task-specific settlement lines, not attempts."""

    def test_success_and_failure_each_confirm_one_settled_battle(self):
        for result in ("Catch success", "Catch failure"):
            with self.subTest(result=result):
                data = snapshot([
                    row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
                    row("10:00:01", "Start battle process"), row("10:00:05", result),
                    row("10:00:06", "Get reward"),
                    row("10:00:07", "Scheduler: End task `BondlingFairyland`"),
                ])
                task = data["tasks"]["BondlingFairyland"]
                self.assertEqual(task["battle"]["count"], 1)
                self.assertEqual(task["battle_attempt_count"], 1)
                self.assertEqual(task["battle"]["avg_duration_seconds"], 4)
                # A settlement count does not imply a successful capture.
                self.assertIsNone(task["runs"][0]["success_confirmed"])

    def test_duplicate_results_reward_and_outer_capture_message_count_once(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Catch success"),
            row("10:00:06", "Catch success"), row("10:00:07", "Catch failure"),
            row("10:00:08", "Get reward"), row("10:00:09", "Get reward"),
            row("10:00:10", "Catch successful and current ball number: 1"),
            row("10:00:11", "Battle completion process"),
            row("10:00:12", "Scheduler: End task `BondlingFairyland`"),
        ])
        self.assertEqual(data["total_battle_count"], 1)
        self.assertEqual(data["tasks"]["BondlingFairyland"]["battle_attempt_count"], 1)

    def test_two_new_battles_with_success_and_failure_count_two(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Catch success"),
            row("10:00:06", "Get reward"),
            row("10:00:10", "Start battle process"), row("10:00:15", "Catch failure"),
            row("10:00:16", "Get reward"),
            row("10:00:17", "Scheduler: End task `BondlingFairyland`"),
        ])
        battle = data["tasks"]["BondlingFairyland"]["battle"]
        self.assertEqual(battle["count"], 2)
        self.assertEqual(data["tasks"]["BondlingFairyland"]["battle_attempt_count"], 2)
        self.assertEqual(battle["avg_duration_seconds"], 4.5)

    def test_other_tasks_cannot_confirm_from_bondling_catch_messages(self):
        for task_name in ("Orochi", "Exploration", "Dokan"):
            with self.subTest(task_name=task_name):
                data = snapshot([
                    row("10:00:00", f"Scheduler: Start task `{task_name}`"),
                    row("10:00:01", "Start battle process"),
                    row("10:00:05", "Catch success"), row("10:00:06", "Catch failure"),
                    row("10:00:07", f"Scheduler: End task `{task_name}`"),
                ])
                self.assertEqual(data["tasks"][task_name]["battle"]["count"], 0)
                self.assertEqual(data["tasks"][task_name]["battle_attempt_count"], 1)

    def test_attempt_rewards_and_outer_message_without_exact_settlement_do_not_count(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Get reward"),
            row("10:00:06", "Catch successful and current ball number: 1"),
            row("10:00:07", "Scheduler: End task `BondlingFairyland`"),
        ])
        task = data["tasks"]["BondlingFairyland"]
        self.assertEqual(task["battle"]["count"], 0)
        self.assertEqual(task["battle_attempt_count"], 1)
        self.assertIsNone(task["battle"]["avg_duration_seconds"])

    def test_settlement_without_observed_start_counts_with_unknown_duration(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:05", "Catch success"),
            row("10:00:06", "Scheduler: End task `BondlingFairyland`"),
        ])
        task = data["tasks"]["BondlingFairyland"]
        self.assertEqual(task["battle"]["count"], 1)
        self.assertEqual(task["battle_attempt_count"], 0)
        self.assertEqual(task["battle"]["duration_sample_count"], 0)
        self.assertIsNone(task["battle"]["avg_duration_seconds"])

    def test_midnight_assigns_attempt_to_start_day_and_settlement_to_result_day(self):
        parser = stats.LogStatsParser("09")
        parser.consume_lines([
            row("23:59:50", "Scheduler: Start task `BondlingFairyland`", day="2026-10-04"),
            row("23:59:55", "Start battle process", day="2026-10-04"),
            row("00:00:04", "Catch failure"), row("00:00:05", "Get reward"),
            row("00:00:06", "Scheduler: End task `BondlingFairyland`"),
        ])
        yesterday = parser.snapshot(date(2026, 10, 4))["tasks"]["BondlingFairyland"]
        today = parser.snapshot(DAY)["tasks"]["BondlingFairyland"]
        self.assertEqual(yesterday["battle"]["count"], 0)
        self.assertEqual(yesterday["battle_attempt_count"], 1)
        self.assertEqual(today["battle"]["count"], 1)
        self.assertEqual(today["battle_attempt_count"], 0)
        self.assertEqual(today["battle"]["avg_duration_seconds"], 9)

    def test_confirmed_settlement_survives_restart_then_unsettled_attempt(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Catch success"),
            row("10:00:06", "Start scheduler loop: 09"),
            row("10:00:07", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:08", "Start battle process"),
            row("10:00:09", "Scheduler: End task `BondlingFairyland`"),
        ])
        task = data["tasks"]["BondlingFairyland"]
        self.assertEqual(task["run_count"], 2)
        self.assertEqual(task["battle"]["count"], 1)
        self.assertEqual(task["battle_attempt_count"], 2)
        self.assertEqual(task["runs"][0]["status"], "interrupted")
        self.assertEqual(task["runs"][0]["battle"]["count"], 1)
        self.assertEqual(task["runs"][1]["battle"]["count"], 0)

    def test_confirmed_settlement_survives_process_exit_or_task_error(self):
        for tail in ((row("10:00:06", "Script 09 process exit"),),
                     (row("10:00:06", "GameStuckError: synthetic interruption", "ERROR"),
                      row("10:00:07", "Scheduler: End task `BondlingFairyland`"))):
            with self.subTest(tail=tail):
                data = snapshot([
                    row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
                    row("10:00:01", "Start battle process"), row("10:00:05", "Catch failure"),
                    *tail,
                ])
                task = data["tasks"]["BondlingFairyland"]
                self.assertEqual(task["battle"]["count"], 1)
                self.assertEqual(task["interrupted_run_count"], 1)

    def test_unclosed_task_keeps_settlement_without_claiming_live_status(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `BondlingFairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:05", "Catch success"),
        ])
        task = data["tasks"]["BondlingFairyland"]
        self.assertEqual(task["battle"]["count"], 1)
        self.assertEqual(task["runs"][0]["status"], "incomplete")

    def test_task_matching_is_case_insensitive_but_result_message_is_exact(self):
        data = snapshot([
            row("10:00:00", "Scheduler: Start task `bondlingfairyland`"),
            row("10:00:01", "Start battle process"), row("10:00:02", "Catch successful"),
            row("10:00:03", "Catch success: extra context"), row("10:00:04", "Catch success"),
            row("10:00:05", "Scheduler: End task `bondlingfairyland`"),
        ])
        self.assertEqual(data["tasks"]["bondlingfairyland"]["battle"]["count"], 1)
        self.assertEqual(data["tasks"]["bondlingfairyland"]["battle"]["avg_duration_seconds"], 3)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "log").mkdir()
        (self.root / "config/09.json").write_text("{}", encoding="utf-8")
        self.path = self.root / "log/2026-10-05_09.txt"
        self.service = stats.LogStatsService(self.root, now=lambda: datetime(2026, 10, 5, 12))

    def write(self, lines):
        self.path.write_text("".join(lines), encoding="utf-8")

    def test_partial_line_and_unicode_append_are_only_consumed_when_complete(self):
        start = row("10:00:00", "Scheduler: Start task `Chess`")
        end = row("10:00:05", "Scheduler: End task `Chess`")
        self.path.write_bytes((start + end[:-6]).encode("utf-8"))
        before = self.service.build_stats("09", DAY)
        self.assertEqual(before["incomplete_run_count"], 1)
        self.assertEqual(before["total_runtime_seconds"], 0)
        with self.path.open("ab") as handle:
            handle.write(end[-6:].encode("utf-8"))
            handle.write(row("10:01:00", "任务结束").encode("utf-8")[:-2])
        after = self.service.build_stats("09", DAY)
        self.assertEqual(after["completed_run_count"], 1)
        self.assertEqual(after["total_runtime_seconds"], 5)
        self.assertEqual(after["source"]["partial_line_count"], 1)

    def test_unchanged_file_is_not_reopened(self):
        self.write([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:05", "Scheduler: End task `Chess`")])
        expected = self.service.build_stats("09", DAY)
        with patch.object(Path, "open", side_effect=AssertionError("unchanged log was reread")):
            self.assertEqual(self.service.build_stats("09", DAY), expected)

    def test_rotation_truncation_rebuilds_counts(self):
        self.write([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:05", "Scheduler: End task `Chess`")])
        self.assertEqual(self.service.build_stats("09", DAY)["total_runtime_seconds"], 5)
        self.write([row("11:00:00", "Scheduler: Start task `Chess`")])
        result = self.service.build_stats("09", DAY)
        self.assertEqual(result["total_task_run_count"], 1)
        self.assertEqual(result["completed_run_count"], 0)
        self.assertEqual(result["total_runtime_seconds"], 0)

    def test_same_size_replacement_rebuilds(self):
        first = [row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:05", "Scheduler: End task `Chess`")]
        self.write(first)
        self.service.build_stats("09", DAY)
        replacement = self.path.with_suffix(".replacement")
        replacement.write_text("".join(first).replace("10:00", "11:00"), encoding="utf-8")
        os.replace(replacement, self.path)
        run = self.service.build_stats("09", DAY)["tasks"]["Chess"]["runs"][0]
        self.assertTrue(run["start_time"].startswith("2026-10-05 11:00"))

    def test_larger_copytruncate_rebuilds_instead_of_appending_old_counts(self):
        self.write([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:05", "Scheduler: End task `Chess`")])
        self.service.build_stats("09", DAY)
        self.write([
            row("11:00:00", "Scheduler: Start task `Chess`"), row("11:00:08", "Scheduler: End task `Chess`"),
            row("11:00:09", "padding longer than old contents"),
        ])
        result = self.service.build_stats("09", DAY)
        self.assertEqual(result["total_task_run_count"], 1)
        self.assertEqual(result["total_runtime_seconds"], 8)

    def test_project_external_statistics_directory_is_rejected(self):
        self.service.config_root = self.root.parent
        with self.assertRaises(stats.StatisticsInputError) as context:
            self.service.list_available_dates("09")
        self.assertEqual(context.exception.status_code, 403)

    def test_actual_timestamp_dates_include_midnight_in_yesterday_file(self):
        yesterday = self.root / "log/2026-10-04_09.txt"
        yesterday.write_text(row("23:59:50", "Scheduler: Start task `Chess`", day="2026-10-04") + row("00:00:20", "Scheduler: End task `Chess`"), encoding="utf-8")
        self.assertEqual(self.service.list_available_dates("09")["dates"], ["2026-10-05", "2026-10-04"])
        self.assertEqual(self.service.build_stats("09", DAY)["total_runtime_seconds"], 20)

    def test_bad_names_unknown_config_and_underscore_collision_are_rejected(self):
        for name in ("../09", "09\\..", "09:other", "09\x00", " 09", "template"):
            with self.subTest(name=name), self.assertRaises(stats.StatisticsInputError):
                self.service.build_stats(name, DAY)
        with self.assertRaises(stats.StatisticsInputError) as context:
            self.service.build_stats("missing", DAY)
        self.assertEqual(context.exception.status_code, 404)
        (self.root / "config/A.json").write_text("{}", encoding="utf-8")
        (self.root / "config/A_B.json").write_text("{}", encoding="utf-8")
        for name in ("A", "A_B"):
            with self.assertRaises(stats.StatisticsInputError) as context:
                self.service.list_available_dates(name)
            self.assertEqual(context.exception.status_code, 409)

    def test_unique_underscore_alias_requires_full_owner_marker(self):
        (self.root / "config/A_B.json").write_text("{}", encoding="utf-8")
        path = self.root / "log/2026-10-05_A.txt"
        path.write_text(row("10:00:00", "Scheduler: Start task `Chess`") + row("10:00:10", "Scheduler: End task `Chess`"), encoding="utf-8")
        self.assertEqual(self.service.build_stats("A_B", DAY)["total_task_run_count"], 0)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(row("11:00:00", "Start scheduler loop: A_B") + row("11:00:01", "Scheduler: Start task `Chess`") + row("11:00:05", "Scheduler: End task `Chess`"))
        result = self.service.build_stats("A_B", DAY)
        self.assertEqual(result["script_name"], "A_B")
        self.assertEqual(result["total_runtime_seconds"], 4)

    def test_small_budget_backfills_incrementally_without_duplicate_counts(self):
        self.write([row("10:00:00", "Scheduler: Start task `Chess`"), row("10:00:05", "Scheduler: End task `Chess`")])
        self.service.READ_BUDGET = 50
        result = self.service.build_stats("09", DAY)
        self.assertTrue(result["source"]["backfill_pending"])
        self.assertFalse(result["statistics_complete"])
        self.assertEqual(result["available_metrics"], [])
        self.assertTrue(result["warnings"])
        for _ in range(10):
            result = self.service.build_stats("09", DAY)
            if not result["source"]["backfill_pending"]:
                break
        self.assertEqual(result["completed_run_count"], 1)
        self.assertEqual(result["total_runtime_seconds"], 5)

    def test_cache_is_bounded(self):
        self.service.MAX_SCRIPTS = 2
        for name in ("A", "B", "C"):
            (self.root / f"config/{name}.json").write_text("{}", encoding="utf-8")
            self.service.list_available_dates(name)
        self.assertEqual(list(self.service._cache), ["B", "C"])


if __name__ == "__main__":
    unittest.main()
