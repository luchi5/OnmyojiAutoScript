"""Exercise task decisions with fake frames and clicks; never import ADB or OCR runtimes."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pydantic import BaseModel, Field, ValidationError


ROOT = Path(__file__).resolve().parents[1]
LOG = SimpleNamespace(**{key: Mock() for key in ('info', 'warning', 'error', 'hr')})


class UtilizeRule(str, Enum):
    DEFAULT = 'default'
    TAIKO = 'kaiko'
    FISH = 'fish'


class SelectFriendList(str, Enum):
    SAME_SERVER = 'same_server'
    DIFFERENT_SERVER = 'different_server'


class ShikigamiClass(str, Enum):
    N = 'n'


class CardClass(str, Enum):
    UNKNOWN = 'unknown'
    TAIKO4 = 'taiko_4'
    TAIKO5 = 'taiko_5'
    TAIKO6 = 'taiko_6'
    FISH4 = 'fish_4'
    FISH5 = 'fish_5'
    FISH6 = 'fish_6'


class TaskEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class ClockTimer:
    def __init__(self, seconds):
        self.checks = 0
        self.limit = 100 if seconds == 120 else 6

    def start(self):
        return self

    def reached(self):
        self.checks += 1
        return self.checks > self.limit


def task_methods(relative, names):
    source = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    cls = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == 'ScriptTask')
    selected = deepcopy(cls)
    selected.bases = []
    selected.body = [item for item in selected.body if isinstance(item, ast.FunctionDef) and item.name in names]
    env = dict(logger=LOG, Timer=ClockTimer, time=SimpleNamespace(monotonic=Mock(return_value=100), sleep=Mock()),
               datetime=datetime, timedelta=timedelta, random=SimpleNamespace(random=Mock(return_value=0)),
               UtilizeRule=UtilizeRule, SelectFriendList=SelectFriendList, ShikigamiClass=ShikigamiClass,
               CardClass=CardClass, TaskEnd=TaskEnd, GameStuckError=GameStuckError,
               page_main='main', page_guild='guild', target_to_card_class=lambda target: target)
    exec(compile(ast.Module(body=[selected], type_ignores=[]), relative, 'exec'), env)
    return env['ScriptTask'], env


KU, KU_ENV = task_methods('tasks/KekkaiUtilize/script_task.py', {
    '_card_meets_threshold', '_lazy_mode_weight', '_lazy_card_matches_rule', '_deduplicate_card_matches',
    '_scan_lazy_resource_cards', '_select_lazy_resource_card', '_select_optimal_resource_card',
    '_current_select_best', 'check_max_lv', 'run', 'recive_guild_ap_or_assets',
})
GB, GB_ENV = task_methods('tasks/GuildBanquet/script_task.py', {'check_full_experience', 'switch_shikigami'})
KA, KA_ENV = task_methods('tasks/KekkaiActivation/script_task.py', {'run'})


def config_class(relative, class_name, environment):
    source = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    env = dict(BaseModel=BaseModel, Field=Field, **environment)
    exec(compile(ast.Module(body=[cls], type_ignores=[]), relative, 'exec'), env)
    return env[class_name]


def ku_task(**settings):
    con = SimpleNamespace(min_taiko_value=0, min_fish_value=0, utilize_rule=UtilizeRule.DEFAULT,
                          utilize_enable=True, lazy_mode=False, lazy_mode_weight=1.0,
                          shikigami_class=ShikigamiClass.N, auto_fill=False, utilize_harvest=True,
                          box_ap_enable=True, box_exp_enable=True, box_exp_waste=True,
                          harvest_guild_max_times=3, guild_ap_enable=True, guild_assets_enable=True)
    con.__dict__.update(settings)
    task = KU()
    task.config = SimpleNamespace(kekkai_utilize=SimpleNamespace(utilize_config=con))
    task.device = SimpleNamespace(image=None, sleep=Mock())
    task.C_SELECT_CARD = SimpleNamespace(roi_front=None)
    task.I_U_EMPTY_CARD = 'empty'
    task.ap_max_num = task.jade_max_num = 0
    task.screenshot = Mock()
    task.click = Mock(return_value=True)
    task.appear = Mock(return_value=False)
    task.perform_swipe_action = Mock()
    task.save_image = Mock()
    task.goto_page = Mock()
    return task


class ConfigTests(unittest.TestCase):
    def test_legacy_defaults_and_validation(self):
        conf = config_class('tasks/KekkaiUtilize/config.py', 'UtilizeConfig',
                            dict(UtilizeRule=UtilizeRule, SelectFriendList=SelectFriendList,
                                 ShikigamiClass=ShikigamiClass, TimeDelta=timedelta, timedelta=timedelta))
        old = conf()
        self.assertFalse(old.auto_fill)
        self.assertFalse(old.lazy_mode)
        self.assertTrue(old.utilize_harvest)
        self.assertEqual((old.min_taiko_value, old.min_fish_value), (0, 0))
        for values in ({'min_taiko_value': -1}, {'min_fish_value': 201}, {'lazy_mode_weight': 1.01}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                conf(**values)

    def test_optional_fill_and_banquet_defaults_are_off(self):
        source = (ROOT / 'tasks/KekkaiActivation/config.py').read_text(encoding='utf-8')
        self.assertIn("auto_fill: bool = Field(default=False", source)
        tree = ast.parse((ROOT / 'tasks/GuildBanquet/config.py').read_text(encoding='utf-8'))
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'GuildBanquetTime')
        field = next(item for item in cls.body if isinstance(item, ast.AnnAssign) and item.target.id == 'auto_switch_shikigami')
        default = next(key.value for key in field.value.keywords if key.arg == 'default')
        self.assertFalse(ast.literal_eval(default))


class CardSelectionTests(unittest.TestCase):
    def test_assignment_that_bypasses_model_validation_cannot_select_invalid_thresholds(self):
        for minimum in ('bad', -1, 201, 60.5, float('nan'), float('inf')):
            with self.subTest(minimum=minimum):
                task = ku_task(min_taiko_value=minimum)
                self.assertFalse(task._card_meets_threshold('太鼓', 76))

    def test_invalid_probability_disables_lazy_mode(self):
        for weight in ('bad', -0.1, 1.1, float('nan'), float('inf')):
            with self.subTest(weight=weight):
                self.assertEqual(ku_task(lazy_mode_weight=weight)._lazy_mode_weight(), 0)
        self.assertEqual(ku_task(lazy_mode_weight='0.5')._lazy_mode_weight(), 0.5)

    def test_thresholds_are_independent_and_inclusive(self):
        task = ku_task(min_taiko_value=67, min_fish_value=134)
        self.assertTrue(task._card_meets_threshold('太鼓', 67))
        self.assertFalse(task._card_meets_threshold('太鼓', 66))
        self.assertTrue(task._card_meets_threshold('斗鱼', 134))
        self.assertFalse(task._card_meets_threshold('斗鱼', 133))
        self.assertFalse(task._card_meets_threshold('unknown', 999))
        self.assertFalse(task._card_meets_threshold('太鼓', 0))

    def test_normal_scan_does_not_accept_perfect_card_below_minimum(self):
        task = ku_task(min_taiko_value=77, min_fish_value=151)
        task.order_targets = SimpleNamespace(find_everyone=Mock(return_value=[
            (CardClass.TAIKO6, .99, (0, 0, 10, 10)),
            (CardClass.FISH6, .99, (0, 100, 10, 10)),
        ]))
        task.check_card_num = Mock(side_effect=[('太鼓', 76), ('斗鱼', 151)])
        self.assertTrue(task._current_select_best())
        self.assertEqual(task.jade_max_num, 0)
        self.assertEqual(task.ap_max_num, 151)

    def test_confirmation_scan_also_honors_changed_threshold(self):
        task = ku_task(min_taiko_value=77)
        task.order_targets = SimpleNamespace(find_everyone=Mock(side_effect=[
            [(CardClass.TAIKO6, .99, (0, 0, 10, 10))], None, None, None, None,
        ]))
        task.check_card_num = Mock(return_value=('太鼓', 76))
        self.assertIsNone(task._current_select_best('太鼓', 76, selected_card=True))
        task.save_image.assert_not_called()

    def test_changed_threshold_removes_old_record_before_selection(self):
        task = ku_task(min_taiko_value=77, min_fish_value=152)
        task.jade_max_num, task.ap_max_num = 76, 151
        task._current_select_best = Mock(return_value=False)
        self.assertFalse(task._select_optimal_resource_card())
        self.assertEqual((task.jade_max_num, task.ap_max_num), (0, 0))
        task._current_select_best.assert_called_once_with()

    def test_overlapping_templates_keep_highest_confidence(self):
        chosen = KU._deduplicate_card_matches([
            (CardClass.TAIKO5, .81, (0, 0, 60, 50)),
            (CardClass.TAIKO6, .96, (3, 1, 60, 50)),
            (CardClass.FISH4, .91, (0, 100, 60, 50)),
        ])
        self.assertEqual([item[0] for item in chosen], [CardClass.TAIKO6, CardClass.FISH4])

    def test_lazy_rule_and_star_filter(self):
        task = ku_task(utilize_rule=UtilizeRule.TAIKO)
        self.assertTrue(task._lazy_card_matches_rule(CardClass.TAIKO6, 5))
        self.assertFalse(task._lazy_card_matches_rule(CardClass.TAIKO4, 5))
        self.assertFalse(task._lazy_card_matches_rule(CardClass.FISH6, 5))
        self.assertTrue(task._lazy_card_matches_rule(CardClass.TAIKO4, 4, 4))

    def test_lazy_scan_skips_low_reward_then_selects_eligible(self):
        task = ku_task(min_taiko_value=67)
        task.lazy_scan_targets = SimpleNamespace(find_everyone=Mock(return_value=[
            (CardClass.TAIKO5, .99, (0, 0, 60, 50)),
            (CardClass.TAIKO6, .99, (0, 100, 60, 50)),
        ]))
        task.check_card_num = Mock(side_effect=[('太鼓', 60), ('太鼓', 67)])
        self.assertTrue(task._scan_lazy_resource_cards(5))
        self.assertEqual(task.C_SELECT_CARD.roi_front, (0, 100, 60, 50))

    def test_lazy_four_star_fallback_stays_in_same_friend_group(self):
        task = ku_task()
        task._reset_utilize_friend_list = Mock()
        task._scan_lazy_resource_cards = Mock(side_effect=[None, True])
        self.assertTrue(task._select_lazy_resource_card(SelectFriendList.DIFFERENT_SERVER))
        self.assertEqual(task._reset_utilize_friend_list.call_count, 2)
        for call in task._reset_utilize_friend_list.call_args_list:
            self.assertEqual(call.args, (SelectFriendList.DIFFERENT_SERVER,))
        self.assertEqual([call.args for call in task._scan_lazy_resource_cards.call_args_list], [(5,), (4, 4)])

    def test_incomplete_lazy_scan_does_not_fallback_to_four_stars(self):
        task = ku_task()
        task._reset_utilize_friend_list = Mock()
        task._scan_lazy_resource_cards = Mock(return_value=False)
        self.assertFalse(task._select_lazy_resource_card(SelectFriendList.SAME_SERVER))
        task._scan_lazy_resource_cards.assert_called_once_with(5)

    def test_lazy_scan_has_a_swipe_limit(self):
        task = ku_task()
        task.lazy_scan_targets = SimpleNamespace(find_everyone=Mock(return_value=[
            (CardClass.TAIKO4, .99, (0, 0, 60, 50)),
        ]))
        self.assertIs(task._scan_lazy_resource_cards(5), False)
        self.assertLessEqual(task.perform_swipe_action.call_count, 21)


class TaskControlTests(unittest.TestCase):
    def test_activation_legacy_config_and_cached_parent_signature_remain_compatible(self):
        task = KA()
        task.config = SimpleNamespace(kekkai_activation=SimpleNamespace(activation_config=SimpleNamespace(
            exchange_before=True, exchange_max=True, shikigami_class=ShikigamiClass.N)))
        calls = []
        # The old cached KekkaiUtilize accepts only one parameter.
        task.check_max_lv = lambda shikigami_class: calls.append(shikigami_class)
        task.I_REALM_SHIN = 'realm'
        task.appear = Mock(return_value=True)
        for method in ('goto_page', 'goto_realm', 'harvest_card', 'run_activation', 'screenshot'):
            setattr(task, method, Mock())
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(calls, [ShikigamiClass.N, ShikigamiClass.N])

    def test_harvest_switch_and_auto_fill_reach_execution(self):
        for harvest in (False, True):
            with self.subTest(harvest=harvest):
                task = ku_task(utilize_harvest=harvest, auto_fill=True)
                for name in ('goto_realm', 'check_utilize_add', 'check_max_lv', 'check_utilize_harvest',
                             'check_box_ap_or_exp', 'recive_guild_ap_or_assets', 'set_next_run'):
                    setattr(task, name, Mock())
                with self.assertRaises(TaskEnd):
                    task.run()
                self.assertEqual(task.check_utilize_harvest.call_count, int(harvest))
                task.check_max_lv.assert_called_once_with(ShikigamiClass.N, True)

    def test_smart_fill_uses_game_button_and_never_manual_slots(self):
        task = ku_task()
        task.realm_goto_grown = Mock()
        task.ui_click = Mock(return_value=True)
        task.I_AUTO_FILL, task.I_REMOVE_ALL = 'fill', 'remove'
        task.I_REALM_SHIN, task.I_SHI_GROWN = 'realm', 'grown'
        task.appear = Mock(return_value=True)
        task.appear_multi_scale = Mock(return_value=True)
        task.detect_no_shikigami = Mock()
        task.switch_shikigami_class = Mock()
        task.check_max_lv(ShikigamiClass.N, True)
        task.ui_click.assert_called_once_with('fill', 'remove', interval=1.5, timeout=20)
        task.detect_no_shikigami.assert_not_called()
        task.switch_shikigami_class.assert_not_called()

    def test_smart_fill_timeout_requests_recovery(self):
        task = ku_task()
        task.realm_goto_grown = Mock()
        task.ui_click = Mock(return_value=False)
        task.I_AUTO_FILL, task.I_REMOVE_ALL = 'fill', 'remove'
        with self.assertRaises(GameStuckError):
            task.check_max_lv(ShikigamiClass.N, True)

    def test_preexisting_live_config_objects_keep_the_old_defaults(self):
        task = ku_task()
        for field in ('auto_fill', 'lazy_mode', 'lazy_mode_weight', 'utilize_harvest',
                      'min_taiko_value', 'min_fish_value'):
            delattr(task.config.kekkai_utilize.utilize_config, field)
        for name in ('goto_realm', 'check_utilize_add', 'check_max_lv', 'check_utilize_harvest',
                     'check_box_ap_or_exp', 'recive_guild_ap_or_assets', 'set_next_run'):
            setattr(task, name, Mock())
        self.assertTrue(task._card_meets_threshold('太鼓', 50))
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertFalse(task.utilize_lazy_mode_active)
        task.check_max_lv.assert_called_once_with(ShikigamiClass.N, False)
        task.check_utilize_harvest.assert_called_once_with()


def banquet_task():
    task = GB()
    task.run_time = SimpleNamespace(auto_switch_shikigami=True)
    task._banquet_full_samples = 0
    task._banquet_auto_switch_failed = False
    task._banquet_last_switch = float('-inf')
    task.device = SimpleNamespace(image=None, sleep=Mock(), stuck_record_clear=Mock(), stuck_record_add=Mock())
    task.appear = Mock(return_value=True)
    return task


class BanquetTests(unittest.TestCase):
    def test_old_live_banquet_config_leaves_replacement_disabled(self):
        task = banquet_task()
        delattr(task.run_time, 'auto_switch_shikigami')
        task.switch_shikigami = Mock()
        self.assertFalse(task.check_full_experience())
        task.switch_shikigami.assert_not_called()

    def test_full_experience_requires_two_real_checks(self):
        task = banquet_task()
        task.I_BANQUET_EXP_FULL = 'full'
        task.switch_shikigami = Mock(return_value=True)
        self.assertFalse(task.check_full_experience())
        self.assertTrue(task.check_full_experience())
        task.switch_shikigami.assert_called_once_with()
        self.assertFalse(task.check_full_experience())
        self.assertFalse(task.check_full_experience())
        task.switch_shikigami.assert_called_once_with()

    def test_transient_full_indicator_resets_debounce(self):
        task = banquet_task()
        task.I_BANQUET_EXP_FULL = 'full'
        task.appear = Mock(side_effect=[True, False, True])
        task.switch_shikigami = Mock()
        for _ in range(3):
            self.assertFalse(task.check_full_experience())
        task.switch_shikigami.assert_not_called()

    def test_failed_replacement_disables_repeated_clears(self):
        task = banquet_task()
        task.I_BANQUET_EXP_FULL = 'full'
        task.switch_shikigami = Mock(return_value=False)
        for _ in range(5):
            self.assertFalse(task.check_full_experience())
        task.switch_shikigami.assert_called_once_with()

    def replacement(self, counts):
        task = banquet_task()
        names = ('I_BANQUET_EXP_FULL', 'I_BANQUET_SWITCH', 'I_BANQUET_CLEAR_ALL',
                 'I_BANQUET_ALL_PUT', 'I_BANQUET_CONFIRM', 'I_UI_BACK_RED', 'I_UI_BACK_YELLOW', 'I_FLAG')
        for name in names:
            setattr(task, name, name)
        task.ui_click = Mock(return_value=True)
        task.appear_then_click = Mock(return_value=True)
        task.screenshot = Mock()
        task.goto_page = Mock()
        task.appear = Mock(side_effect=lambda rule: rule == task.I_FLAG)
        task.O_BANQUET_SHIKIGAMI_NUM = SimpleNamespace(
            ocr_digit_counter=Mock(side_effect=[(0, 6, 6), (0, 6, 6)] + counts))
        return task

    def test_empty_ocr_never_confirms_empty_team(self):
        task = self.replacement([(0, 0, 0)] * 6)
        self.assertFalse(task.switch_shikigami())
        self.assertFalse(any(call.args[0] == task.I_BANQUET_CONFIRM for call in task.ui_click.call_args_list))

    def test_stable_nonempty_team_confirms_with_mainline_intervals(self):
        task = self.replacement([(6, 0, 6)] * 2)
        self.assertTrue(task.switch_shikigami())
        confirms = [call for call in task.ui_click.call_args_list if call.args[0] == task.I_BANQUET_CONFIRM]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0].kwargs, dict(interval=1.5, timeout=15))
        self.assertTrue(all(call.kwargs['interval'] == 1.5 for call in task.appear_then_click.call_args_list))

    def test_partial_nonempty_team_can_be_kept(self):
        task = self.replacement([(3, 3, 6)] * 2)
        self.assertTrue(task.switch_shikigami())

    def test_unstable_or_invalid_counter_must_settle_first(self):
        task = self.replacement([(6, 0, 6), (0, 0, 0), (4, 2, 6), (4, 2, 6)])
        self.assertTrue(task.switch_shikigami())
        self.assertEqual(task.O_BANQUET_SHIKIGAMI_NUM.ocr_digit_counter.call_count, 6)

    def test_failed_clear_never_fills_or_confirms_old_full_lineup(self):
        task = self.replacement([])
        task.O_BANQUET_SHIKIGAMI_NUM.ocr_digit_counter = Mock(return_value=(6, 0, 6))
        self.assertFalse(task.switch_shikigami())
        self.assertFalse(any(call.args[0] == task.I_BANQUET_ALL_PUT for call in task.appear_then_click.call_args_list))
        self.assertFalse(any(call.args[0] == task.I_BANQUET_CONFIRM for call in task.ui_click.call_args_list))


class AssetTests(unittest.TestCase):
    def test_adapted_assets_use_mainline_constructors_and_exist(self):
        for relative in ('tasks/KekkaiUtilize/assets.py', 'tasks/GuildBanquet/assets.py'):
            tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'RuleImage':
                    continue
                fields = {key.arg: key.value for key in node.keywords}
                self.assertNotIn('profile', fields)
                self.assertTrue((ROOT / ast.literal_eval(fields['file'])).is_file())


if __name__ == '__main__':
    unittest.main()
