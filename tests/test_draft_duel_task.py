"""Entrypoint safety tests with no imports of OCR, ADB, devices or the runner.

Load only the task class AST and inject its boundaries. These tests verify the
entrypoint's decisions, not live image matching or navigation success.
"""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tasks.DraftDuel.config import DraftDuel, TeamRole
from tasks.DraftDuel.rules import GAME_TIMEZONE, next_closed_retry


ROOT = Path(__file__).resolve().parents[1]


class TaskFinished(Exception):
    pass


class DraftDuelTaskTests(unittest.TestCase):
    def make_task(self, observed, now):
        source = ROOT / "tasks" / "DraftDuel" / "script_task.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        definition = next(item for item in tree.body
                          if isinstance(item, ast.ClassDef) and item.name == "ScriptTask")
        module = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))

        class FrozenDatetime:
            @staticmethod
            def now(tz):
                return now.astimezone(tz)

        image = object()
        recognize = Mock(return_value=observed)
        logger = Mock()
        namespace = {
            "GameUi": object, "TaskEnd": TaskFinished,
            "datetime": FrozenDatetime, "GAME_TIMEZONE": GAME_TIMEZONE,
            "next_closed_retry": next_closed_retry, "observe_lobby": recognize,
            "page_draft_duel": "duel-lobby", "page_main": "courtyard", "logger": logger,
            "TeamRole": TeamRole,
        }
        exec(compile(module, str(source), "exec"), namespace)
        task = namespace["ScriptTask"]()
        task.device = SimpleNamespace(image=image, save_screenshot=Mock())
        task.goto_page = Mock()
        task.screenshot = Mock()
        task.set_next_run = Mock()
        task.click = Mock(side_effect=AssertionError("Entry preflight must not click gameplay controls"))
        task.appear_then_click = Mock(side_effect=AssertionError("No inferred gameplay controls"))
        return task, recognize, logger, image

    def at(self, hour, minute=0):
        return datetime(2026, 10, 18, hour, minute, tzinfo=GAME_TIMEZONE)

    def test_default_task_is_disabled_and_never_allows_paid_entry(self):
        config = DraftDuel()
        self.assertFalse(config.scheduler.enable)
        self.assertTrue(config.draft_duel_config.free_only)
        self.assertEqual(config.draft_duel_config.coin_budget, 0)
        self.assertEqual(config.draft_duel_config.partner_config, "")

    def test_configured_member_dispatches_to_team_stage_without_lobby_navigation(self):
        task, recognize, _, _ = self.make_task(SimpleNamespace(), self.at(12))
        task.config = SimpleNamespace(draft_duel=DraftDuel())
        task.config.draft_duel.draft_duel_config.user_status = TeamRole.MEMBER
        task.run_workflow = Mock(return_value=None)
        self.assertIsNone(task.run())
        task.run_workflow.assert_called_once_with()
        task.goto_page.assert_not_called()
        recognize.assert_not_called()

    def test_closed_lobby_defers_to_verified_opening_and_returns_to_courtyard(self):
        task, recognize, logger, image = self.make_task(
            SimpleNamespace(is_lobby=True, is_closed=True), self.at(3, 20))
        with self.assertRaises(TaskFinished):
            task.run()
        recognize.assert_called_once_with(image)
        task.screenshot.assert_called_once_with()
        self.assertEqual([call.args for call in task.goto_page.call_args_list],
                         [("duel-lobby",), ("courtyard",)])
        task.set_next_run.assert_called_once_with(task="DraftDuel", target=datetime(2026, 10, 18, 8))
        task.device.save_screenshot.assert_not_called()
        task.click.assert_not_called()
        logger.warning.assert_not_called()

    def test_ui_closed_during_scheduled_hours_still_defers(self):
        task, _, _, _ = self.make_task(SimpleNamespace(is_lobby=True, is_closed=True), self.at(12))
        with self.assertRaises(TaskFinished):
            task.run()
        task.set_next_run.assert_called_once_with(task="DraftDuel", target=datetime(2026, 10, 18, 12, 15))
        task.click.assert_not_called()

    def test_unknown_and_open_looking_screens_never_admit_or_report_challenge_success(self):
        for is_lobby, is_closed in ((False, False), (False, True), (True, False)):
            with self.subTest(is_lobby=is_lobby, is_closed=is_closed):
                task, _, logger, _ = self.make_task(
                    SimpleNamespace(is_lobby=is_lobby, is_closed=is_closed), self.at(12))
                # TaskEnd would make Script.run report success even when its
                # retry interval was set with success=False.
                self.assertIsNone(task.run())
                task.device.save_screenshot.assert_called_once_with(genre="draft_duel_unverified")
                task.set_next_run.assert_called_once_with(task="DraftDuel", success=False, finish=True)
                logger.warning.assert_called_once()
                task.click.assert_not_called()
                task.appear_then_click.assert_not_called()
                # A scheduled opening never upgrades an unverified screen.
                self.assertEqual(task.goto_page.call_count, 1)


if __name__ == "__main__":
    unittest.main()
