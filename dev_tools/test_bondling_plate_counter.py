"""Resource guard regressions without importing OAS, OCR or emulator code."""
from enum import Enum
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import (
    SCRIPT_FILE, FakeClock, FakeLogger, FakeTimer, GameStuckError, extract_methods,
)

ZERO = (0, 200, 200)
UNKNOWN = (0, 0, 0)
POSITIVE = (120, 80, 200)


class BondlingMode(str, Enum):
    MODE2 = 'low'
    MODE3 = 'medium'
    MODE4 = 'high'


class CounterHarness:
    def __init__(self, frames):
        self.frames = frames
        self.frame = {}
        self.screenshots = self.rechecks = self.evidence = self.challenges = 0
        self.clock, self.logger = FakeClock(), FakeLogger()
        self.device = SimpleNamespace(image=None)
        self.current_count = 131
        self.config = SimpleNamespace(config_name='08')
        for name in ('O_B_LOW_NUMBER', 'O_B_MEDIUM_NUMBER', 'O_B_HIGH_NUMBER'):
            setattr(self, name, SimpleNamespace(name=name, ocr=self.ocr))
        methods = extract_methods(SCRIPT_FILE, 'ScriptTask',
                                  ('_valid_plate_counter', 'check_plate_available', 'run_catch'),
                                  {'Timer': lambda limit: FakeTimer(self.clock, limit),
                                   'sleep': self.sleep, 'logger': self.logger,
                                   'GameStuckError': GameStuckError, 'BondlingMode': BondlingMode,
                                   'BondlingConfig': SimpleNamespace, 'BattleConfig': SimpleNamespace})
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def screenshot(self):
        self.clock.advance()
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.screenshots += 1
        self.device.image = self.frame

    def sleep(self, seconds):
        self.clock.now += seconds

    def ocr(self, image):
        return image.get('primary', UNKNOWN)

    def in_catch_ui(self):
        return self.frame.get('page', True)

    def _recheck_plate_counter(self, target):
        self.rechecks += 1
        return self.frame.get('verified', (UNKNOWN, UNKNOWN))

    def _save_plate_read_evidence(self, *args):
        self.evidence += 1

    def lock_team(self):
        pass

    def run_alone(self):
        self.challenges += 1
        raise AssertionError('Unconfirmed resources must not launch a battle')


def frame(primary=ZERO, verified=(ZERO, ZERO), page=True):
    return {'primary': primary, 'verified': verified, 'page': page}


class PlateCounterTests(unittest.TestCase):
    def test_positive_primary_is_fast_and_needs_no_extra_ocr(self):
        task = CounterHarness([frame(POSITIVE)])
        self.assertTrue(task.check_plate_available(task.O_B_LOW_NUMBER))
        self.assertEqual((task.screenshots, task.rechecks, task.evidence), (1, 0, 0))

    def test_single_zero_followed_by_positive_does_not_finish(self):
        task = CounterHarness([frame(), frame(POSITIVE)])
        self.assertTrue(task.check_plate_available(task.O_B_LOW_NUMBER))
        self.assertEqual(task.screenshots, 2)

    def test_wider_or_scaled_positive_overrules_zero_or_unknown_primary(self):
        for primary in (ZERO, UNKNOWN):
            for verified in ((POSITIVE, ZERO), (ZERO, POSITIVE)):
                with self.subTest(primary=primary, verified=verified):
                    task = CounterHarness([frame(primary, verified)])
                    self.assertTrue(task.check_plate_available(task.O_B_LOW_NUMBER))
                    self.assertEqual(task.screenshots, 1)

    def test_requires_three_new_frames_with_all_readers_agreeing_on_zero(self):
        task = CounterHarness([frame()])
        self.assertFalse(task.check_plate_available(task.O_B_LOW_NUMBER))
        self.assertEqual((task.screenshots, task.rechecks, task.evidence), (3, 3, 1))
        self.assertGreaterEqual(task.clock.now - 100, 2)

    def test_unknown_zero_and_unknown_alternation_never_confirms_empty(self):
        unknown = frame(UNKNOWN, (UNKNOWN, UNKNOWN))
        task = CounterHarness([frame(), unknown] * 20)
        with self.assertRaisesRegex(GameStuckError, 'not completed'):
            task.check_plate_available(task.O_B_LOW_NUMBER)
        self.assertEqual(task.challenges, 0)

    def test_unknown_is_not_zero_and_never_launches_challenge(self):
        for mode in BondlingMode:
            with self.subTest(mode=mode):
                task = CounterHarness([frame(UNKNOWN, (UNKNOWN, UNKNOWN))])
                config = SimpleNamespace(bondling_mode=mode)
                with self.assertRaisesRegex(GameStuckError, 'not completed'):
                    task.run_catch(config, SimpleNamespace())
                self.assertEqual(task.challenges, 0)

    def test_missing_capture_page_cannot_confirm_zero(self):
        task = CounterHarness([frame(page=False)])
        with self.assertRaises(GameStuckError):
            task.check_plate_available(task.O_B_LOW_NUMBER)
        self.assertEqual(task.rechecks, 0)

    def test_denominator_disagreement_does_not_confirm_zero(self):
        other_zero = (0, 100, 100)
        task = CounterHarness([frame(ZERO, (ZERO, other_zero))])
        with self.assertRaises(GameStuckError):
            task.check_plate_available(task.O_B_LOW_NUMBER)

    def test_zero_confirmation_resets_when_denominator_changes(self):
        other_zero = (0, 100, 100)
        task = CounterHarness([frame(), frame(other_zero, (other_zero, other_zero)), frame(), frame(), frame()])
        self.assertFalse(task.check_plate_available(task.O_B_LOW_NUMBER))
        self.assertEqual(task.screenshots, 5)

    def test_invalid_or_unreadable_counters_are_rejected(self):
        task = CounterHarness([frame()])
        for invalid in (UNKNOWN, (-1, 201, 200), (201, -1, 200), (0, 100, 200),
                        (0, 0, -1), None, (0, 0), ('0', 200, 200), (False, 200, 200)):
            with self.subTest(invalid=invalid):
                self.assertFalse(task._valid_plate_counter(invalid))
        self.assertTrue(task._valid_plate_counter(ZERO))
        self.assertTrue(task._valid_plate_counter(POSITIVE))

    def test_each_mode_checks_its_own_plate_without_challenging_when_empty(self):
        for mode, name in ((BondlingMode.MODE2, 'O_B_LOW_NUMBER'),
                           (BondlingMode.MODE3, 'O_B_MEDIUM_NUMBER'),
                           (BondlingMode.MODE4, 'O_B_HIGH_NUMBER')):
            with self.subTest(mode=mode):
                task = CounterHarness([frame()])
                checked = []
                guard = task.check_plate_available
                task.check_plate_available = lambda target: checked.append(target.name) or guard(target)
                self.assertFalse(task.run_catch(SimpleNamespace(bondling_mode=mode), SimpleNamespace()))
                self.assertEqual(checked, [name])
                self.assertEqual(task.screenshots, 3)
                self.assertEqual(task.challenges, 0)


if __name__ == '__main__':
    unittest.main()
