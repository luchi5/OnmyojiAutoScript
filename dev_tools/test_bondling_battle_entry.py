"""Offline regression checks for bondling entry and stale result screens.

Run with ``toolkit\\python.exe -m unittest dev_tools.test_bondling_battle_entry``.
Only the methods under test are compiled from their AST. No project imports,
backend, OCR model, emulator, or device connection is initialized.
"""

import ast
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
BATTLE_FILE = ROOT / "tasks/BondlingFairyland/battle.py"
SCRIPT_FILE = ROOT / "tasks/BondlingFairyland/script_task.py"
RESULT_MARKERS = (
    "I_REWARD", "I_WIN", "I_BATTLE_SUCCESS", "I_BATTLE_FAIL",
    "I_CAP_SUCCESS", "I_CAP_FAILURE", "I_BATTLE_FAIL_ABANDON", "I_CAP_AGAIN",
)
LOW_THRESHOLD_MARKERS = set(RESULT_MARKERS[:4])
MARKERS = RESULT_MARKERS + ("I_BUFF", "I_EXIT", "I_BALL_FIRE", "I_UI_CONFIRM")


class GameStuckError(Exception):
    pass


class BondlingNumberMax(Exception):
    pass


class UserStatus(str, Enum):
    ALONE = "alone"
    LEADER = "leader"
    MEMBER = "member"
    handoff1 = "handoff1"
    handoff2 = "handoff2"


class FakeClock:
    def __init__(self, step=0.5):
        self.now = 100.0
        self.step = step

    def advance(self):
        self.now += self.step


class FakeTimer:
    """Preserve Timer's elapsed-time and confirmation-count semantics."""

    def __init__(self, clock, limit, count=0):
        self.clock = clock
        self.limit = limit
        self.count = count
        self._started = None
        self._reach_count = count

    def start(self):
        if not self.started():
            self.reset()
        return self

    def started(self):
        return self._started is not None

    def current(self):
        return self.clock.now - self._started if self.started() else 0.0

    def reached(self):
        self._reach_count += 1
        elapsed = self.clock.now - (self._started or 0.0)
        return elapsed > self.limit and self._reach_count > self.count

    def reset(self):
        self._started = self.clock.now
        self._reach_count = 0
        return self

    def clear(self):
        self._started = None
        self._reach_count = self.count
        return self


class FakeLogger:
    def __init__(self):
        self.messages = []

    def __getattr__(self, level):
        return lambda *args, **kwargs: self.messages.append((level, args))


def extract_methods(path, class_name, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    source_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in source_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    missing = set(names) - {node.name for node in methods}
    if missing:
        raise AssertionError("Missing regression target methods: " + ", ".join(sorted(missing)))
    module = ast.Module(body=methods, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return {name: namespace[name] for name in names}


class Harness:
    """A deterministic sequence of visible frames, holding the final frame."""

    def __init__(self, frames, status=UserStatus.ALONE, step=0.5):
        self.clock = FakeClock(step)
        self.frames = frames
        self.frame = {}
        self.screenshots = 0
        self.clicks = []
        self.interval_seen = {}
        self.logger = FakeLogger()
        self.current_count = 7
        self.catch_calls = 0
        self.green_calls = []
        self.start_time = datetime.now()
        self.limit_time = timedelta(minutes=20)
        self.config = SimpleNamespace(
            task=SimpleNamespace(command="BondlingFairyland"),
            bondling_fairyland=SimpleNamespace(
                bondling_config=SimpleNamespace(user_status=status)))
        self.device = SimpleNamespace(
            stuck_record_add=lambda *args: None,
            stuck_record_clear=lambda: None,
            click_record_clear=lambda: None,
            image=None,
        )
        for marker in MARKERS:
            setattr(self, marker, marker)
        namespace = {
            "Timer": lambda limit, count=0: FakeTimer(self.clock, limit, count),
            "logger": self.logger,
            "UserStatus": UserStatus,
            "GameStuckError": GameStuckError,
            "BondlingNumberMax": BondlingNumberMax,
            "BattleConfig": SimpleNamespace,
            "datetime": datetime,
            "timedelta": timedelta,
            "I18n": SimpleNamespace(trans_zh_cn=lambda value: value),
        }
        methods = extract_methods(
            BATTLE_FILE, "BondlingBattle",
            ("_capture_result_visible", "check_load", "run_battle"), namespace)
        methods.update(extract_methods(SCRIPT_FILE, "ScriptTask", ("run_alone",), namespace))
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def screenshot(self):
        self.clock.advance()
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.device.image = self.frame
        if self.screenshots > 150:
            raise AssertionError("Entry loop failed to finish within simulated timeout")

    def appear(self, marker, interval=None, threshold=None):
        if interval and self.clock.now - self.interval_seen.get(marker, -1000) < interval:
            return False
        score = self.frame.get(marker, 0.0)
        if score <= (0.8 if threshold is None else threshold):
            return False
        if interval:
            self.interval_seen[marker] = self.clock.now
        return True

    def appear_then_click(self, marker, action=None, interval=None, threshold=None):
        if not self.appear(marker, interval=interval, threshold=threshold):
            return False
        self.clicks.append((marker, self.clock.now))
        return True

    def green_mark(self, *args):
        self.green_calls.append(args)

    def catch_battle_wait(self, *args):
        self.catch_calls += 1
        return True


def frame(*markers):
    return {marker: 1.0 for marker in markers}


def battle_config():
    return SimpleNamespace(green_enable=True, green_mark="main",
                           random_click_swipt_enable=False)


class EntryRegressionTests(unittest.TestCase):
    def test_all_result_markers_are_recognized_at_their_intended_thresholds(self):
        for marker in RESULT_MARKERS:
            with self.subTest(marker=marker):
                score = 0.65 if marker in LOW_THRESHOLD_MARKERS else 0.9
                harness = Harness([{marker: score}])
                harness.screenshot()
                self.assertTrue(harness._capture_result_visible())
        harness = Harness([frame("I_BALL_FIRE", "I_BUFF", "I_EXIT")])
        harness.screenshot()
        self.assertFalse(harness._capture_result_visible())

    def test_reward_and_failure_enter_settlement_without_waiting_for_battle(self):
        for marker in RESULT_MARKERS:
            with self.subTest(marker=marker):
                harness = Harness([frame(marker)])
                self.assertIs(harness.check_load(), False)
                self.assertEqual(harness.screenshots, 1)

    def test_result_screen_takes_priority_over_background_prepare_and_fire(self):
        harness = Harness([frame("I_REWARD", "I_BUFF", "I_BALL_FIRE")])
        self.assertIs(harness.check_load(), False)

    def test_preparation_and_active_battle_have_distinct_results(self):
        self.assertIs(Harness([frame("I_BUFF")]).check_load(), True)
        self.assertIs(Harness([frame("I_EXIT")]).check_load(), False)

    def test_confirm_dialog_precedes_returned_catch_page_detection(self):
        harness = Harness([frame("I_UI_CONFIRM", "I_BALL_FIRE")] * 10
                          + [frame("I_EXIT")])
        self.assertIs(harness.check_load(), False)
        self.assertTrue(harness.clicks)
        self.assertTrue(all(marker == "I_UI_CONFIRM" for marker, _ in harness.clicks))
        self.assertEqual(harness.screenshots, 11)

    def test_single_player_return_to_catch_page_is_confirmed_and_not_counted(self):
        harness = Harness([frame("I_BALL_FIRE")])
        self.assertIs(harness.run_battle(battle_config(), limit_count=220), False)
        self.assertGreaterEqual(harness.clock.now - 100.0, 3.0)
        self.assertGreater(harness.screenshots, 2)
        self.assertEqual(harness.current_count, 7)
        self.assertEqual(harness.catch_calls, 0)
        self.assertEqual(harness.green_calls, [])

    def test_transient_catch_frames_do_not_abort_entry(self):
        harness = Harness([frame("I_BALL_FIRE")] * 4 + [frame()]
                          + [frame("I_BALL_FIRE")] * 4 + [frame("I_EXIT")])
        self.assertIs(harness.check_load(), False)
        self.assertEqual(harness.screenshots, 10)

    def test_one_slow_catch_frame_does_not_confirm_return(self):
        harness = Harness([frame("I_BALL_FIRE"), frame("I_EXIT")], step=4.0)
        self.assertIs(harness.check_load(), False)
        self.assertEqual(harness.screenshots, 2)

    def test_team_roles_wait_through_catch_page_for_battle(self):
        for status in (UserStatus.LEADER, UserStatus.MEMBER, UserStatus.handoff1, UserStatus.handoff2):
            with self.subTest(status=status):
                harness = Harness([frame("I_BALL_FIRE")] * 10 + [frame("I_EXIT")], status=status)
                self.assertIs(harness.check_load(), False)
                self.assertEqual(harness.screenshots, 11)

    def test_unknown_page_times_out_without_counting_a_battle(self):
        harness = Harness([frame()])
        with self.assertRaises(GameStuckError):
            harness.run_battle(battle_config(), limit_count=220)
        self.assertGreaterEqual(harness.clock.now - 100.0, 30.0)
        self.assertLessEqual(harness.clock.now - 100.0, 32.0)
        self.assertEqual(harness.current_count, 7)
        self.assertEqual(harness.catch_calls, 0)

    def test_actual_entry_counts_once_and_marks_only_preparation(self):
        for marker, green_count in (("I_BUFF", 1), ("I_EXIT", 0), ("I_REWARD", 0)):
            with self.subTest(marker=marker):
                harness = Harness([frame(marker)])
                self.assertTrue(harness.run_battle(battle_config(), limit_count=220))
                self.assertEqual(harness.current_count, 8)
                self.assertEqual(harness.catch_calls, 1)
                self.assertEqual(len(harness.green_calls), green_count)

    def test_single_player_does_not_click_background_fire_on_result_or_battle(self):
        for marker in RESULT_MARKERS + ("I_BUFF", "I_EXIT"):
            with self.subTest(marker=marker):
                harness = Harness([frame(marker, "I_BALL_FIRE")])
                harness.run_alone()
                self.assertEqual(harness.clicks, [])
                self.assertEqual(harness.screenshots, 1)

    def test_single_player_confirm_dialog_is_clicked_before_background_fire(self):
        harness = Harness([frame("I_UI_CONFIRM", "I_BALL_FIRE"), frame("I_EXIT")])
        harness.run_alone()
        self.assertEqual([marker for marker, _ in harness.clicks], ["I_UI_CONFIRM"])

    def test_disappearing_fire_hands_off_to_battle_loading(self):
        harness = Harness([frame("I_BALL_FIRE"), frame()])
        harness.run_alone()
        self.assertEqual([marker for marker, _ in harness.clicks], ["I_BALL_FIRE"])
        self.assertEqual(harness.screenshots, 2)

    def test_failed_entry_is_bounded_and_never_reported_as_inventory_full(self):
        harness = Harness([frame("I_BALL_FIRE")])
        with self.assertRaises(GameStuckError) as raised:
            harness.run_alone()
        fire_times = [at for marker, at in harness.clicks if marker == "I_BALL_FIRE"]
        self.assertEqual(len(fire_times), 6)
        self.assertTrue(all(b - a >= 2 for a, b in zip(fire_times, fire_times[1:])))
        self.assertLessEqual(harness.clock.now - 100.0, 32.0)
        diagnostic = str(raised.exception) + repr(harness.logger.messages)
        self.assertNotIn("500", diagnostic)
        self.assertNotIn("Bondling number max", diagnostic)

    def test_persistent_confirmation_dialog_times_out_without_clicking_fire(self):
        harness = Harness([frame("I_UI_CONFIRM", "I_BALL_FIRE")])
        with self.assertRaises(GameStuckError):
            harness.run_alone()
        self.assertTrue(harness.clicks)
        self.assertTrue(all(marker == "I_UI_CONFIRM" for marker, _ in harness.clicks))
        self.assertGreaterEqual(harness.clock.now - 100.0, 30.0)
        self.assertLessEqual(harness.clock.now - 100.0, 32.0)


def static_asset_definitions():
    definitions = {}
    for relative in ("tasks/BondlingFairyland/assets.py", "tasks/Component/GeneralBattle/assets.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "RuleImage"):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = {
                        keyword.arg: ast.literal_eval(keyword.value) for keyword in node.value.keywords}
    return definitions


class SavedScreenshotRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cv2
        except ImportError:
            raise unittest.SkipTest("OpenCV is not installed; deterministic entry regressions still run")
        cls.cv2 = cv2
        cls.assets = static_asset_definitions()

    def screenshot_scores(self, relative):
        path = ROOT / relative
        if not path.is_file():
            self.skipTest("Saved local error screenshot is unavailable")
        source = self.cv2.imread(str(path))
        self.assertIsNotNone(source)
        scores = {}
        for name in MARKERS:
            if name not in self.assets:
                continue
            asset = self.assets[name]
            x, y, width, height = asset["roi_back"]
            crop = source[y:y + height, x:x + width]
            template = self.cv2.imread(str(ROOT / asset["file"]))
            self.assertIsNotNone(template, name)
            if template.shape[0] > crop.shape[0] or template.shape[1] > crop.shape[1]:
                scores[name] = -1.0
            else:
                result = self.cv2.matchTemplate(crop, template, self.cv2.TM_CCOEFF_NORMED)
                scores[name] = float(self.cv2.minMaxLoc(result)[1])
        return scores

    def test_08_reward_screenshot_reaches_settlement(self):
        scores = self.screenshot_scores("log/error/1791215972760/2026-10-05_23-59-32-598884.png")
        self.assertGreater(scores["I_REWARD"], 0.8)
        harness = Harness([scores])
        self.assertIs(harness.check_load(), False)
        self.assertEqual(harness.screenshots, 1)

    def test_08_returned_catch_screenshot_does_not_count_a_battle(self):
        scores = self.screenshot_scores("log/error/1791216058309/2026-10-06_00-00-58-225896.png")
        self.assertGreater(scores["I_BALL_FIRE"], 0.8)
        harness = Harness([scores])
        self.assertIs(harness.run_battle(battle_config(), limit_count=220), False)
        self.assertEqual(harness.current_count, 7)
        self.assertEqual(harness.catch_calls, 0)

    def test_09_continuous_failure_screenshot_is_a_result_not_full_inventory(self):
        scores = self.screenshot_scores("log/error/1791210692841/2026-10-05_22-31-32-786512.png")
        self.assertGreater(scores["I_CAP_AGAIN"], 0.8)
        self.assertGreater(scores["I_BATTLE_FAIL_ABANDON"], 0.8)
        harness = Harness([scores])
        harness.run_alone()
        self.assertEqual(harness.clicks, [])
        self.assertIs(harness.check_load(), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
