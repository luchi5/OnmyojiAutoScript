"""Offline checks for realm-card reward ranges without a game or backend.

The real card-selection and activation methods are compiled from their AST.
Only card configuration is imported; OCR, screenshots, swipes and task exits
are deterministic fakes, so these checks cannot contact a device or send push.
"""

import ast
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path
import random
import re
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from tasks.KekkaiActivation.config import ActivationConfig, CardType


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/KekkaiActivation/script_task.py'


class TaskEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class FakeLogger:
    def __init__(self):
        self.messages = []

    def __getattr__(self, level):
        return lambda *args, **kwargs: self.messages.append((level, args))


class FakeRuleClick:
    def __init__(self, roi_front, roi_back, name):
        self.roi_front = roi_front
        self.roi_back = roi_back
        self.name = name


def task_class(logger):
    """Preserve real helpers while removing imports and device-heavy bases."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))
    cls = next(item for item in tree.body
               if isinstance(item, ast.ClassDef) and item.name == 'ScriptTask')
    cls.bases = []
    cls.keywords = []
    cls.decorator_list = []
    namespace = {
        'ActivationConfig': ActivationConfig, 'CardType': CardType,
        'CardClass': object, 'ImageGrid': object, 'RuleImage': object,
        'RuleClick': FakeRuleClick, 'TaskEnd': TaskEnd,
        'GameStuckError': GameStuckError, 'logger': logger,
        'cached_property': cached_property, 'datetime': datetime,
        'timedelta': timedelta, 're': re, 'random': random,
        'time': SimpleNamespace(sleep=lambda *args: None),
        'point2str': lambda x, y: f'({x}, {y})',
        'ShikigamiClass': SimpleNamespace(N='N'),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    return namespace['ScriptTask']


class Harness:
    def __init__(self, config, frames, status=False, effect=False):
        self.settings = config
        self.logger = FakeLogger()
        real_task = task_class(self.logger)
        self.real = real_task()
        self.real.config = SimpleNamespace(
            kekkai_activation=SimpleNamespace(activation_config=config),
            save=self.save,
        )
        self.frames = frames or [[]]
        self.current = []
        self.screenshots = 0
        self.swipes = []
        self.clicks = []
        self.saved = 0
        self.pushes = []
        self.next_runs = []
        self.not_found_calls = 0
        self.screening_calls = []
        self.status = status
        self.effect = effect
        self.real.device = SimpleNamespace(image='fake-frame', swipe_adb=self.swipe)
        self.real.O_CHECK_CARD_NUMBER = SimpleNamespace(
            roi=(305, 153, 107, 481), detect_and_ocr=lambda image: self.current,
        )
        self.real.screenshot = self.screenshot
        self.real.goto_cards = lambda: None
        self.real.check_card_status = lambda *args: self.status
        self.real.check_card_effect = lambda *args: self.effect
        self.real.appear = lambda *args, **kwargs: False
        self.real.appear_then_click = self.click
        self.real.click = self.click
        self.real.set_next_run = self.set_next_run
        self.real.ocr_time = lambda *args: timedelta(hours=5)
        self.real.save_image = self.save_image
        self.real.screening_card = self.screening_card
        self.real._card_not_found = self.card_not_found
        for name in ('I_A_ACTIVATE_YELLOW', 'I_A_DEMOUNT', 'I_A_INVITE',
                     'I_UI_CONFIRM', 'I_A_EMPTY'):
            setattr(self.real, name, name)

    def screenshot(self):
        texts = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.current = [SimpleNamespace(
            ocr_text=text,
            box=((0, index * 65), (100, index * 65),
                 (100, index * 65 + 35), (0, index * 65 + 35)),
        ) for index, text in enumerate(texts)]
        self.screenshots += 1
        if self.screenshots > 40:
            raise AssertionError('Card loop did not terminate')

    def swipe(self, start, end, duration=0):
        self.swipes.append((start, end, duration))

    def click(self, target, **kwargs):
        self.clicks.append(target)
        return True

    def save(self):
        self.saved += 1

    def save_image(self, **kwargs):
        self.pushes.append(kwargs)

    def set_next_run(self, task, target=None, **kwargs):
        self.next_runs.append((task, target, kwargs))

    def screening_card(self, rule):
        self.screening_calls.append(rule)
        raise AssertionError('Unexpected new card selection')

    def card_not_found(self):
        self.not_found_calls += 1
        raise AssertionError('Invalid bounds must not change card type')

    def selected_text(self, target):
        if target is None:
            return None
        index = (target.roi_front[1] - self.real.O_CHECK_CARD_NUMBER.roi[1]) // 65
        return self.current[index].ocr_text

    def choose(self):
        return self.selected_text(self.real.check_card_num())


def legacy_config(card_type=CardType.TAIKO, **changes):
    """Old cached models have no newly added maximum attributes."""
    settings = dict(card_type=card_type, min_taiko_num=8,
                    min_fish_num=16, card_not_found_count=0)
    settings.update(changes)
    return SimpleNamespace(**settings)


class RealmCardRangeSelectionTests(unittest.TestCase):
    def test_legacy_config_without_maximum_keeps_largest_reward(self):
        h = Harness(legacy_config(), [['勾玉7', '勾玉8', '勾玉16']])
        self.assertEqual(h.choose(), '勾玉16')
        self.assertEqual(h.swipes, [])

    def test_zero_maximum_is_unlimited(self):
        h = Harness(ActivationConfig(max_taiko_num=0), [['勾玉8', '勾玉64']])
        self.assertEqual(h.choose(), '勾玉64')

    def test_taiko_upper_and_lower_bounds_are_inclusive(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['勾玉7', '勾玉8', '勾玉10', '勾玉11']])
        self.assertEqual(h.choose(), '勾玉10')
        self.assertEqual(h.swipes, [])

    def test_exact_reward_range_excludes_every_other_reward(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=8)
        h = Harness(config, [['勾玉7', '勾玉9', '勾玉8']])
        self.assertEqual(h.choose(), '勾玉8')

    def test_lower_bound_can_be_selected(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['勾玉7', '勾玉8', '勾玉11']])
        self.assertEqual(h.choose(), '勾玉8')

    def test_fish_uses_its_own_range_and_ignores_taiko(self):
        config = ActivationConfig(card_type=CardType.FISH, min_taiko_num=8,
                                  max_taiko_num=8, min_fish_num=16,
                                  max_fish_num=20)
        h = Harness(config, [['勾玉8', '体力15', '体力16', '体力20', '体力21']])
        self.assertEqual(h.choose(), '体力20')

    def test_taiko_uses_its_own_range_and_ignores_fish(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=12,
                                  min_fish_num=16, max_fish_num=16)
        h = Harness(config, [['体力16', '勾玉10', '勾玉12', '勾玉13']])
        self.assertEqual(h.choose(), '勾玉12')

    def test_first_page_above_maximum_swipes_to_eligible_card(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['勾玉16', '勾玉14'], ['勾玉9', '勾玉8']])
        self.assertEqual(h.choose(), '勾玉9')
        self.assertEqual(len(h.swipes), 1)
        self.assertEqual(h.clicks, [])

    def test_only_rewards_above_maximum_never_return_click(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['勾玉16', '勾玉11']])
        self.assertIsNone(h.real.check_card_num())
        self.assertEqual(h.screenshots, 4)
        self.assertEqual(len(h.swipes), 3)
        self.assertEqual(h.clicks, [])

    def test_no_matching_resource_or_digits_does_not_select(self):
        h = Harness(ActivationConfig(max_taiko_num=10),
                    [['体力18', '勾玉识别失败', '金币1000']])
        self.assertIsNone(h.real.check_card_num())
        self.assertEqual(h.clicks, [])

    def test_existing_first_number_parsing_is_preserved(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['勾玉9 数量20 24小时', '勾玉11 数量1 6小时']])
        self.assertEqual(h.choose(), '勾玉9 数量20 24小时')

    def test_first_number_before_resource_keeps_existing_behavior(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=10)
        h = Harness(config, [['24小时 勾玉9', '勾玉8 数量4']])
        self.assertEqual(h.choose(), '勾玉8 数量4')

    def test_invalid_runtime_ranges_skip_for_three_hours_without_fallback(self):
        for card_type, prefix in ((CardType.TAIKO, 'taiko'), (CardType.FISH, 'fish')):
            for changes in (
                {f'min_{prefix}_num': -1},
                {f'max_{prefix}_num': -1},
                {f'max_{prefix}_num': 1},
                {f'min_{prefix}_num': '8'},
                {f'max_{prefix}_num': '0'},
                {f'min_{prefix}_num': 8.0},
                {f'max_{prefix}_num': 20.5},
                {f'min_{prefix}_num': True},
                {f'max_{prefix}_num': False},
                {f'max_{prefix}_num': None},
            ):
                with self.subTest(card_type=card_type, changes=changes):
                    config = legacy_config(card_type, **changes)
                    before_config = vars(config).copy()
                    h = Harness(config, [[]])
                    before = datetime.now()
                    with self.assertRaises(TaskEnd):
                        h.real.run_activation(config)
                    after = datetime.now()
                    self.assertEqual(vars(config), before_config)
                    self.assertEqual(h.screenshots, 0)
                    self.assertEqual(h.clicks, [])
                    self.assertEqual(h.swipes, [])
                    self.assertEqual(h.screening_calls, [])
                    self.assertEqual(h.not_found_calls, 0)
                    self.assertEqual(h.pushes, [])
                    self.assertEqual(len(h.next_runs), 1)
                    task, target, _ = h.next_runs[0]
                    self.assertEqual(task, 'KekkaiActivation')
                    self.assertLessEqual(before + timedelta(hours=3), target)
                    self.assertLessEqual(target, after + timedelta(hours=3))
                    self.assertTrue(any(level == 'warning' for level, _ in h.logger.messages))

    def test_direct_selection_also_rejects_invalid_runtime_range(self):
        config = legacy_config(max_taiko_num=7)
        h = Harness(config, [['勾玉8']])
        with self.assertRaises(TaskEnd):
            h.real.check_card_num()
        self.assertEqual(h.screenshots, 0)
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.not_found_calls, 0)
        self.assertEqual(len(h.next_runs), 1)

    def test_active_card_is_not_removed_or_replaced(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=8)
        h = Harness(config, [[]], status=True, effect=True)
        before = datetime.now()
        self.assertFalse(h.real.run_activation(config))
        after = datetime.now()
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.screening_calls, [])
        self.assertEqual(h.not_found_calls, 0)
        self.assertEqual(len(h.next_runs), 1)
        task, target, _ = h.next_runs[0]
        self.assertEqual(task, 'KekkaiActivation')
        self.assertLessEqual(before + timedelta(hours=5), target)
        self.assertLessEqual(target, after + timedelta(hours=5))


class RealmCardRangeConfigTests(unittest.TestCase):
    def test_defaults_preserve_existing_lower_limits_and_no_upper_limit(self):
        config = ActivationConfig()
        self.assertEqual((config.min_taiko_num, config.min_fish_num), (8, 16))
        self.assertEqual((config.max_taiko_num, config.max_fish_num), (0, 0))

    def test_exact_range_constructs_and_zero_maximum_remains_valid(self):
        config = ActivationConfig(min_taiko_num=8, max_taiko_num=8,
                                  min_fish_num=16, max_fish_num=16)
        self.assertEqual(config.max_taiko_num, config.min_taiko_num)
        config.max_taiko_num = 0
        config.min_taiko_num = 99
        self.assertEqual(config.max_taiko_num, 0)

    def test_negative_values_are_rejected_on_construction(self):
        for name in ('min_taiko_num', 'max_taiko_num',
                     'min_fish_num', 'max_fish_num'):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                ActivationConfig(**{name: -1})

    def test_upper_below_lower_is_rejected_on_construction(self):
        for changes in ({'min_taiko_num': 8, 'max_taiko_num': 7},
                        {'min_fish_num': 16, 'max_fish_num': 15}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ActivationConfig(**changes)

    def test_bad_assignment_does_not_leave_changed_values(self):
        config = ActivationConfig(max_taiko_num=10, max_fish_num=20)
        for name, value in (('min_taiko_num', 11), ('max_taiko_num', 7),
                            ('min_fish_num', 21), ('max_fish_num', 15),
                            ('min_taiko_num', -1), ('max_taiko_num', -1),
                            ('min_fish_num', -1), ('max_fish_num', -1)):
            with self.subTest(name=name, value=value):
                before = config.model_dump()
                with self.assertRaises(ValidationError):
                    setattr(config, name, value)
                self.assertEqual(config.model_dump(), before)

    def test_non_integral_numbers_and_invalid_strings_are_rejected(self):
        for name in ('min_taiko_num', 'max_taiko_num',
                     'min_fish_num', 'max_fish_num'):
            for value in (8.5, 'invalid'):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValidationError):
                        ActivationConfig(**{name: value})
                    config = ActivationConfig()
                    before = config.model_dump()
                    with self.assertRaises(ValidationError):
                        setattr(config, name, value)
                    self.assertEqual(config.model_dump(), before)

    def test_zero_lower_limit_and_exact_zero_upper_unlimited(self):
        config = ActivationConfig(min_taiko_num=0, max_taiko_num=0,
                                  min_fish_num=0, max_fish_num=0)
        h = Harness(config, [['勾玉1', '勾玉64']])
        self.assertEqual(h.choose(), '勾玉64')


if __name__ == '__main__':
    unittest.main()
