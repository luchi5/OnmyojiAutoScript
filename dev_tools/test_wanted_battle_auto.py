"""Offline regressions for manual-mode recovery in the wanted battle loop."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import extract_methods


ROOT = Path(__file__).resolve().parents[1]


class GameStuckError(Exception):
    pass


class Harness:
    def __init__(self, frames=None):
        self.frame = {'markers': {'I_BATTLE_INFO'}, 'mode': '手动'}
        self.frames = frames or []
        self.clock = 0.0
        self.shots = 0
        self.clicks = []
        self.ocr_calls = 0
        self.click_allowed = True
        self.logs = []
        self.settlements = []
        self.device = SimpleNamespace(
            image=self.frame, stuck_record_add=lambda name: None,
            click_record_clear=lambda: None,
        )
        for marker in ('I_SE_BATTLE_WIN', 'I_WIN', 'I_REWARD', 'I_FALSE'):
            setattr(self, marker, marker)

        owner = self

        class Ocr:
            def __init__(self, **kwargs):
                self.roi = kwargs['roi']

            def ocr(self, image):
                owner.ocr_calls += 1
                if self.roi == (18, 635, 98, 62):
                    return image.get('primary', image.get('mode', ''))
                return image.get('fallback', image.get('mode', ''))

        class Click:
            def __init__(self, **kwargs):
                self.roi_front = kwargs['roi_front']
                self.name = kwargs['name']

        logger = SimpleNamespace(info=self.logs.append, warning=self.logs.append)
        namespace = {
            'monotonic': lambda: self.clock, 'RuleOcr': Ocr,
            'RuleClick': Click, 'GameStuckError': GameStuckError, 'logger': logger,
        }
        tree = ast.parse((ROOT / 'tasks/WantedQuests/battle_auto.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(ROOT), 'exec'), namespace)
        self.guard_type = namespace['WantedBattleAuto']
        self.guard = self.guard_type()
        namespace['WantedBattleAuto'] = lambda: self.guard
        methods = extract_methods(ROOT / 'tasks/WantedQuests/script_task.py',
                                  'ScriptTask', ('battle_wait',), namespace)
        self.battle_wait = methods['battle_wait'].__get__(self)

    def inspect(self, advance=0):
        self.clock += advance
        self.device.image = self.frame
        return self.guard.inspect(self)

    def is_in_real_battle(self, screenshot=False):
        return 'I_BATTLE_INFO' in self.frame['markers']

    def is_in_prepare(self, screenshot=False):
        return 'I_PREPARE_HIGHLIGHT' in self.frame['markers']

    def click(self, target, interval):
        if not self.click_allowed:
            return False
        self.clicks.append((target.name, interval, target.roi_front))
        return True

    def screenshot(self):
        if self.shots >= 40:
            raise AssertionError('Wanted battle loop did not terminate')
        self.frame = self.frames[min(self.shots, len(self.frames) - 1)]
        self.device.image = self.frame
        self.clock += 1
        self.shots += 1

    def appear(self, marker, **kwargs):
        return marker in self.frame['markers']

    def appear_then_click(self, marker, **kwargs):
        if not self.appear(marker):
            return False
        self.settlements.append(marker)
        return True

    def ui_click_until_disappear(self, marker, **kwargs):
        self.settlements.append(marker)


def frame(*markers, mode='', **values):
    return {'markers': set(markers), 'mode': mode, **values}


class AutoModeTests(unittest.TestCase):
    def test_manual_switch_uses_control_interval_and_safe_button(self):
        h = Harness()
        self.assertTrue(h.inspect())
        self.assertEqual(h.clicks, [('wanted_battle_auto_switch', 2.0, (40, 650, 40, 28))])
        self.assertTrue(h.guard.pending)
        self.assertEqual(h.guard.attempts, 1)

    def test_confirmed_auto_is_never_toggled_back_off(self):
        h = Harness()
        h.inspect()
        h.frame['mode'] = '自动'
        self.assertFalse(h.inspect(2))
        self.assertFalse(h.inspect(2))
        self.assertEqual(len(h.clicks), 1)
        self.assertFalse(h.guard.pending)
        self.assertEqual(h.guard.attempts, 0)
        self.assertTrue(any('已确认自动' in item for item in h.logs))

    def test_initially_auto_has_no_click(self):
        h = Harness()
        h.frame['mode'] = '自动'
        self.assertFalse(h.inspect())
        self.assertEqual(h.clicks, [])

    def test_check_throttles_ocr_and_retries_a_swallowed_click(self):
        h = Harness()
        h.inspect()
        before = h.ocr_calls
        self.assertFalse(h.inspect(1))
        self.assertEqual(h.ocr_calls, before)
        self.assertTrue(h.inspect(1))
        self.assertEqual(len(h.clicks), 2)

    def test_only_confirmed_real_fights_are_inspected(self):
        for markers in (set(), {'I_REWARD'}, {'I_BATTLE_INFO', 'I_PREPARE_HIGHLIGHT'}):
            with self.subTest(markers=markers):
                h = Harness()
                h.frame['markers'] = markers
                self.assertFalse(h.inspect())
                self.assertEqual(h.ocr_calls, 0)
                self.assertEqual(h.clicks, [])

    def test_unknown_or_nonexact_text_never_clicks(self):
        for mode in ('', '手动操作', '自动上阵', None, 7):
            with self.subTest(mode=mode):
                h = Harness()
                h.frame['mode'] = mode
                self.assertFalse(h.inspect())
                self.assertEqual(h.clicks, [])

    def test_tight_label_fallback_and_spaces(self):
        h = Harness()
        h.frame.update(primary='装饰', fallback=' 手 动 ')
        self.assertTrue(h.inspect())
        self.assertEqual(h.ocr_calls, 2)

    def test_unreadable_after_click_is_not_claimed_as_confirmed_auto(self):
        h = Harness()
        h.inspect()
        h.frame['mode'] = ''
        self.assertFalse(h.inspect(2))
        self.assertTrue(h.guard.pending)
        self.assertFalse(any('已确认自动' in item for item in h.logs))

    def test_declined_click_does_not_consume_attempt(self):
        h = Harness()
        h.click_allowed = False
        self.assertFalse(h.inspect())
        self.assertEqual(h.guard.attempts, 0)
        self.assertFalse(h.guard.pending)

    def test_persisting_manual_has_bounded_retries(self):
        h = Harness()
        for _ in range(3):
            self.assertTrue(h.inspect(2))
        with self.assertRaisesRegex(GameStuckError, '仍显示手动'):
            h.inspect(2)
        self.assertEqual(len(h.clicks), 3)

    def test_later_manual_mode_is_recovered_again(self):
        h = Harness()
        h.inspect()
        h.frame['mode'] = '自动'
        h.inspect(2)
        h.frame['mode'] = '手动'
        self.assertTrue(h.inspect(2))
        self.assertEqual(len(h.clicks), 2)
        self.assertEqual(h.guard.attempts, 1)


class WantedLoopTests(unittest.TestCase):
    def test_real_wanted_loop_recovers_manual_and_finishes(self):
        h = Harness([
            frame('I_BATTLE_INFO', mode='手动'),
            frame('I_BATTLE_INFO', mode='自动'),
            frame('I_BATTLE_INFO', mode='自动'),
            frame('I_WIN'), frame('I_REWARD'),
        ])
        self.assertTrue(h.battle_wait(False))
        self.assertEqual(len(h.clicks), 1)
        self.assertEqual(h.settlements, ['I_WIN', 'I_REWARD'])
        self.assertTrue(any('已确认自动' in item for item in h.logs))

    def test_swallowed_switch_retries_in_actual_wait_loop(self):
        h = Harness([
            frame('I_BATTLE_INFO', mode='手动'),
            frame('I_BATTLE_INFO', mode='手动'),
            frame('I_BATTLE_INFO', mode='手动'),
            frame('I_BATTLE_INFO', mode='自动'),
            frame('I_REWARD'),
        ])
        self.assertTrue(h.battle_wait(False))
        self.assertEqual(len(h.clicks), 2)

    def test_original_settlement_paths_skip_auto_inspection(self):
        for marker, expected in (('I_SE_BATTLE_WIN', True), ('I_REWARD', True), ('I_FALSE', False)):
            with self.subTest(marker=marker):
                h = Harness([frame(marker, mode='手动')])
                self.assertEqual(h.battle_wait(False), expected)
                self.assertEqual(h.clicks, [])
                self.assertEqual(h.ocr_calls, 0)
                self.assertEqual(h.settlements, [marker])

    def test_running_auto_fight_keeps_original_win_flow(self):
        h = Harness([frame('I_BATTLE_INFO', mode='自动'), frame('I_WIN'), frame('I_REWARD')])
        self.assertTrue(h.battle_wait(True))
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.settlements, ['I_WIN', 'I_REWARD'])


if __name__ == '__main__':
    unittest.main()
