"""Task-local courtyard skin choices and reward boundaries, without a device.

Only configuration and image rules are imported. The scheduler entry point is
compiled from its AST; scripted frames exercise real claim-loop decisions while
screenshots, timers and input remain deterministic fakes.
"""

import ast
from copy import copy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from pydantic import ValidationError

from module.config.config_model import ConfigModel
from tasks.CourtyardAffairs.config import (
    CourtyardAffairs, CourtyardAffairsConfig, CourtyardSkin)
from tasks.CourtyardAffairs.completion_state import (
    BlueCompletionDetector, CompletionAwareCourtyardMixin, CourtyardEmptyTasksRule)
from tasks.CourtyardAffairs.skin_assets import (
    BlueCourtyardAssets, CourtyardSkinRule)


ROOT = Path(__file__).resolve().parents[1]


class ImageRule:
    def __init__(self, name, roi=(1, 2, 3, 4)):
        self.name = name
        self.roi_front = list(roi)
        self.roi_back = (0, 0, 1280, 720)
        self.threshold = .85
        self.method = 'Template matching'
        self.file = f'{name}.png'

    def match(self, image, threshold=None):
        matched = self.name in image
        if matched:
            # Real RuleImage localization changes its own front list.
            self.roi_front[:2] = [101, 202]
        return matched


def page_rule(skin):
    return CourtyardSkinRule(ImageRule('default-page'), ImageRule('blue-page'),
                             skin=skin)


def action_rule(skin):
    return CourtyardSkinRule(
        ImageRule('default-page'), ImageRule('blue-page'),
        ImageRule('default-action', (1100, 590, 70, 80)),
        ImageRule('blue-action', (1120, 599, 77, 75)),
        ImageRule('daily'), skin=skin)


def empty_rule(skin):
    return CourtyardEmptyTasksRule(
        ImageRule('empty'), ImageRule('default-page'), ImageRule('blue-page'),
        ImageRule('daily'), ImageRule('inactive'), (ImageRule('other-page'),),
        skin=skin)


class OfflineTaskEnd(Exception):
    pass


def scheduler_run():
    source = ROOT / 'tasks/CourtyardAffairs/script_task.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    cls = next(item for item in tree.body
               if isinstance(item, ast.ClassDef) and item.name == 'ScriptTask')
    run = next(item for item in cls.body
               if isinstance(item, ast.FunctionDef) and item.name == 'run')
    run.decorator_list = []
    namespace = {'TaskEnd': OfflineTaskEnd, 'page_main': object(),
                 'CourtyardSkin': CourtyardSkin}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[run], type_ignores=[])),
                 str(source), 'exec'), namespace)
    return namespace['run']


class CourtyardSkinConfigTests(unittest.TestCase):
    def test_old_task_settings_default_to_automatic_detection(self):
        config = CourtyardAffairsConfig.model_validate({
            'timeout_seconds': 60, 'max_complete_clicks': 3})
        self.assertEqual(config.courtyard_skin, CourtyardSkin.AUTO)
        self.assertEqual(CourtyardAffairs().courtyard_affairs_config.courtyard_skin,
                         CourtyardSkin.AUTO)

    def test_all_stored_skin_values_round_trip(self):
        for skin in CourtyardSkin:
            with self.subTest(skin=skin):
                config = CourtyardAffairsConfig(courtyard_skin=skin.value)
                self.assertEqual(config.courtyard_skin, skin)
                self.assertEqual(config.model_dump(mode='json')['courtyard_skin'],
                                 skin.value)

    def test_unknown_skin_is_rejected_on_loading_and_assignment(self):
        for value in ('unknown-skin', None, 1, ['courtyard_affairs_blue']):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    CourtyardAffairsConfig(courtyard_skin=value)
                config = CourtyardAffairsConfig(courtyard_skin=CourtyardSkin.BLUE)
                before = config.model_dump()
                with self.assertRaises(ValidationError):
                    config.courtyard_skin = value
                self.assertEqual(config.model_dump(), before)

    def test_oas_schema_exposes_three_skin_dropdown_options(self):
        # No ConfigModel constructor: it reads and may write account files.
        task = CourtyardAffairs(courtyard_affairs_config=CourtyardAffairsConfig(
            courtyard_skin=CourtyardSkin.BLUE))
        schema = ConfigModel.script_task(SimpleNamespace(courtyard_affairs=task),
                                        'CourtyardAffairs')
        row = next(item for item in schema['courtyard_affairs_config']
                   if item['name'] == 'courtyard_skin')
        self.assertEqual(row['type'], 'enum')
        self.assertEqual(row['enumEnum'], [skin.value for skin in CourtyardSkin])
        self.assertEqual(row['value'], CourtyardSkin.BLUE.value)
        self.assertEqual(row['default'], CourtyardSkin.AUTO.value)

    def test_scheduler_forwards_every_selected_skin(self):
        run = scheduler_run()
        for skin in CourtyardSkin:
            with self.subTest(skin=skin):
                options = CourtyardAffairsConfig(courtyard_skin=skin)
                events = []
                task = SimpleNamespace(
                    config=SimpleNamespace(courtyard_affairs=SimpleNamespace(
                        courtyard_affairs_config=options)),
                    configure_courtyard_skin=Mock(side_effect=lambda value: events.append('configure')),
                    goto_page=Mock(side_effect=lambda page: events.append('navigate')),
                    harvest_courtyard_affairs=Mock(side_effect=lambda **kwargs: events.append('claim') or True),
                    set_next_run=Mock())
                with self.assertRaises(OfflineTaskEnd):
                    run(task)
                task.harvest_courtyard_affairs.assert_called_once_with(
                    timeout_seconds=60, max_complete_clicks=3, courtyard_skin=skin)
                task.configure_courtyard_skin.assert_called_once_with(skin)
                self.assertEqual(events, ['configure', 'navigate', 'claim', 'navigate'])
                task.set_next_run.assert_called_once_with(
                    'CourtyardAffairs', success=True, finish=True, server=True)

    def test_old_cached_options_without_skin_remain_usable(self):
        task = SimpleNamespace(
            config=SimpleNamespace(courtyard_affairs=SimpleNamespace(
                courtyard_affairs_config=SimpleNamespace(
                    timeout_seconds=60, max_complete_clicks=3))),
            configure_courtyard_skin=Mock(),
            goto_page=Mock(), harvest_courtyard_affairs=Mock(return_value=False),
            set_next_run=Mock())
        with self.assertRaises(OfflineTaskEnd):
            scheduler_run()(task)
        task.harvest_courtyard_affairs.assert_called_once_with(
            timeout_seconds=60, max_complete_clicks=3, courtyard_skin=CourtyardSkin.AUTO)
        task.set_next_run.assert_called_once_with('CourtyardAffairs',
                                                success=False, finish=False, server=False)


class CourtyardForcedSkinRulesTests(unittest.TestCase):
    def test_automatic_page_detection_retains_both_skins(self):
        for frame in ({'default-page'}, {'blue-page'}):
            with self.subTest(frame=frame):
                self.assertTrue(page_rule(CourtyardSkin.AUTO).match(frame))

    def test_forced_skin_refuses_other_skin_entry(self):
        for skin, own, other in ((CourtyardSkin.DEFAULT, 'default-page', 'blue-page'),
                                 (CourtyardSkin.BLUE, 'blue-page', 'default-page')):
            with self.subTest(skin=skin):
                rule = page_rule(skin)
                self.assertTrue(rule.match({own}))
                self.assertFalse(rule.match({other}))

    def test_forced_skin_never_uses_the_other_skin_claim_button(self):
        for skin, frame in (
            (CourtyardSkin.DEFAULT, {'blue-page', 'daily', 'blue-action', 'default-action'}),
            (CourtyardSkin.BLUE, {'default-page', 'default-action', 'blue-action', 'daily'}),
        ):
            with self.subTest(skin=skin):
                self.assertFalse(action_rule(skin).match(frame))

    def test_blue_selection_still_requires_daily_tab_and_matched_button(self):
        for frame in ({'blue-page', 'blue-action'}, {'blue-page', 'daily'},
                      {'daily', 'blue-action'}):
            with self.subTest(frame=frame):
                self.assertFalse(action_rule(CourtyardSkin.BLUE).match(frame))
        self.assertTrue(action_rule(CourtyardSkin.BLUE).match(
            {'blue-page', 'daily', 'blue-action'}))

    def test_forced_matching_does_not_mutate_original_assets(self):
        for skin, frame in ((CourtyardSkin.BLUE, {'blue-page', 'daily', 'blue-action'}),
                             (CourtyardSkin.DEFAULT, {'default-page', 'default-action'})):
            originals = [ImageRule('default-page'), ImageRule('blue-page'),
                         ImageRule('default-action'), ImageRule('blue-action'),
                         ImageRule('daily')]
            saved = [copy(rule.roi_front) for rule in originals]
            with self.subTest(skin=skin):
                self.assertTrue(CourtyardSkinRule(*originals, skin=skin).match(frame))
                self.assertEqual([rule.roi_front for rule in originals], saved)

    def test_ambiguous_blue_tab_cannot_claim_special_affairs(self):
        rule = CourtyardSkinRule(
            ImageRule('default-page'), ImageRule('blue-page'),
            ImageRule('default-action'), ImageRule('blue-action'), ImageRule('daily'),
            skin=CourtyardSkin.BLUE, daily_inactive=ImageRule('inactive'))
        self.assertFalse(rule.match({'blue-page', 'daily', 'inactive', 'blue-action'}))

    def test_forced_completion_rejects_context_from_other_skin(self):
        for skin, other in ((CourtyardSkin.BLUE, {'default-page'}),
                             (CourtyardSkin.DEFAULT, {'blue-page', 'daily'})):
            with self.subTest(skin=skin):
                rule = empty_rule(skin)
                self.assertFalse(rule.match(other))
                self.assertFalse(rule.match({'empty'}))

    def test_cross_skin_transition_clears_previously_confirmed_context(self):
        for skin, own, other in (
            (CourtyardSkin.BLUE, {'blue-page', 'daily'}, {'default-page'}),
            (CourtyardSkin.DEFAULT, {'default-page'}, {'blue-page', 'daily'}),
        ):
            with self.subTest(skin=skin):
                rule = empty_rule(skin)
                rule.match(own)
                self.assertTrue(rule.match({'empty'}))
                self.assertFalse(rule.match(other | {'empty'}))
                self.assertFalse(rule.match({'empty'}))

    def test_reward_overlay_preserves_same_skin_daily_context(self):
        for skin, own in ((CourtyardSkin.BLUE, {'blue-page', 'daily'}),
                           (CourtyardSkin.DEFAULT, {'default-page'})):
            with self.subTest(skin=skin):
                rule = empty_rule(skin)
                rule.match(own)
                self.assertFalse(rule.match({'reward'}))
                self.assertTrue(rule.match({'empty'}))

    def test_inactive_blue_daily_tab_and_other_pages_clear_context(self):
        for frame in ({'blue-page', 'inactive'}, {'other-page'}):
            with self.subTest(frame=frame):
                rule = empty_rule(CourtyardSkin.BLUE)
                rule.match({'blue-page', 'daily'})
                rule.match(frame)
                self.assertFalse(rule.match({'empty'}))


class ClaimFrames(CompletionAwareCourtyardMixin):
    def __init__(self, frames, entry):
        self.frames = [set(frame) for frame in frames]
        self.current = set(entry)
        self.device = SimpleNamespace(image=self.current)
        self.actions = []
        for name in ('I_NOTE', 'I_PAGE', 'I_NO_TASKS', 'I_HARVEST_SOUL_2',
                     'I_HARVEST_SOUL_3', 'I_UI_AWARD', 'I_CONFIRM', 'I_DAILY',
                     'I_SUCCESS_CLAIMED', 'I_SKIP', 'I_LOGIN_RED_CLOSE',
                     'I_COMPLETE_TASKS', 'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED',
                     'I_BLUE_DAILY_INACTIVE', 'I_BLUE_COMPLETE_TASKS',
                     'I_BLUE_SUCCESS_CLAIMED', 'I_UI_BACK_YELLOW',
                     'I_CHECK_MAIN', 'I_CHECK_EXPLORATION', 'I_CHECK_RECORDS',
                     'I_CHECK_GUILD'):
            setattr(self, name, ImageRule(name))
        self.C_BLUE_REWARD_CLOSE = SimpleNamespace(name='C_BLUE_REWARD_CLOSE')
        self.ui_click_multi_scale = Mock(
            side_effect=lambda target, stop, **kwargs: stop.match(self.current))

    def screenshot(self):
        if not self.frames:
            raise AssertionError('Frame script exhausted without termination')
        self.current = self.frames.pop(0)
        self.device.image = self.current

    def appear(self, rule, **kwargs):
        return rule.match(self.current)

    def appear_then_click(self, rule, interval=None, **kwargs):
        if self.appear(rule):
            self.actions.append(kwargs.get('action', rule).name)
            return True
        return False

    def click(self, rule, *args, **kwargs):
        self.actions.append(rule.name)
        return True

    def ui_reward_appear_click(self):
        return 'generic-reward' in self.current


class CourtyardSkinClaimLoopTests(unittest.TestCase):
    def run_frames(self, frames, skin=CourtyardSkin.BLUE, entry=None, timeout=False):
        task = ClaimFrames(frames, entry if entry is not None else {'I_BLUE_PAGE'})
        timer = Mock()
        timer.start.return_value = timer
        if isinstance(timeout, (list, tuple)):
            timer.reached.side_effect = timeout
        else:
            timer.reached.return_value = timeout
        detector = Mock()
        detector.ready.side_effect = lambda image: 'done' in image
        with patch('tasks.CourtyardAffairs.selected_skin.Timer', return_value=timer), \
                patch('tasks.CourtyardAffairs.selected_skin.BlueCompletionDetector',
                      return_value=detector):
            result = task.harvest_courtyard_affairs(courtyard_skin=skin)
        return result, task, detector

    def test_selected_skin_rejects_wrong_entry_without_claiming(self):
        for skin, entry in ((CourtyardSkin.BLUE, {'I_PAGE'}),
                             (CourtyardSkin.DEFAULT, {'I_BLUE_PAGE'})):
            with self.subTest(skin=skin):
                success, task, detector = self.run_frames([], skin=skin, entry=entry)
                self.assertFalse(success)
                self.assertEqual(task.actions, [])
                detector.ready.assert_not_called()

    def test_blue_reward_title_closes_popup_before_completion_and_rechecks(self):
        success, task, detector = self.run_frames([
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_COMPLETE_TASKS'},
            {'I_BLUE_SUCCESS_CLAIMED', 'done'},
            {'done'}, {'done'},
        ])
        self.assertTrue(success)
        self.assertEqual(task.actions, ['I_COMPLETE_TASKS', 'C_BLUE_REWARD_CLOSE'])
        self.assertEqual(detector.ready.call_count, 3)
        self.assertEqual(task.frames, [])

    def test_reward_title_is_not_itself_a_success_signal(self):
        success, task, _ = self.run_frames([
            {'I_BLUE_SUCCESS_CLAIMED'}], timeout=True)
        self.assertFalse(success)
        self.assertNotIn('I_COMPLETE_TASKS', task.actions)

    def test_default_mode_does_not_use_blue_done_detector(self):
        success, task, detector = self.run_frames([
            {'done'}, {'unknown'}], skin=CourtyardSkin.DEFAULT, entry={'I_PAGE'},
            timeout=[False, True])
        self.assertFalse(success)
        self.assertEqual(task.actions, [])
        detector.ready.assert_not_called()

    def test_blue_done_confirmations_do_not_mutate_class_assets(self):
        names = ('I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED', 'I_BLUE_DAILY_INACTIVE',
                 'I_BLUE_COMPLETE_TASKS', 'I_BLUE_SUCCESS_CLAIMED')
        before = {name: copy(getattr(BlueCourtyardAssets, name).roi_front)
                  for name in names}
        success, task, _ = self.run_frames([{'done'}, {'done'}])
        self.assertTrue(success)
        self.assertEqual(task.actions, [])
        self.assertEqual({name: getattr(BlueCourtyardAssets, name).roi_front
                          for name in names}, before)


class CourtyardNavigationScopeTests(unittest.TestCase):
    def task(self):
        task = ClaimFrames([], {'I_BLUE_PAGE'})
        unrelated = object()
        closers = [unrelated]
        task.navigator = SimpleNamespace(local_unknown_closers=closers,
                                         add_unknown_closer=lambda *actions: closers.extend(actions))
        return task, unrelated

    def test_blue_task_registers_result_close_then_courtyard_back(self):
        task, unrelated = self.task()
        task.configure_courtyard_skin(CourtyardSkin.BLUE)
        closers = task.navigator.local_unknown_closers
        self.assertIs(closers[0], unrelated)
        self.assertEqual([item.action.name for item in closers[1:]],
                         ['C_BLUE_REWARD_CLOSE', 'I_UI_BACK_YELLOW'])
        result, page = closers[1:]
        self.assertTrue(result.condition.target.match({'I_BLUE_SUCCESS_CLAIMED'}))
        self.assertFalse(result.condition.target.match({'I_BLUE_PAGE'}))
        self.assertTrue(page.condition.target.match({'I_BLUE_PAGE'}))
        self.assertFalse(page.condition.target.match({'I_PAGE'}))

    def test_same_skin_reconfiguration_does_not_accumulate_closers(self):
        task, _ = self.task()
        task.configure_courtyard_skin(CourtyardSkin.BLUE)
        first = list(task.navigator.local_unknown_closers)
        task.configure_courtyard_skin(CourtyardSkin.BLUE)
        self.assertEqual(len(task.navigator.local_unknown_closers), len(first))
        self.assertTrue(all(a is b for a, b in zip(first, task.navigator.local_unknown_closers)))

    def test_skin_switch_replaces_only_its_own_closers_and_original_page_rules(self):
        task, unrelated = self.task()
        task.configure_courtyard_skin(CourtyardSkin.BLUE)
        task.I_PAGE.match({'I_BLUE_PAGE'})
        task.configure_courtyard_skin(CourtyardSkin.DEFAULT)
        closers = task.navigator.local_unknown_closers
        self.assertEqual(len(closers), 2)
        self.assertIs(closers[0], unrelated)
        self.assertEqual(closers[1].action.name, 'I_UI_BACK_YELLOW')
        self.assertTrue(task.I_PAGE.match({'I_PAGE'}))
        self.assertFalse(task.I_PAGE.match({'I_BLUE_PAGE'}))
        task.configure_courtyard_skin(CourtyardSkin.BLUE)
        self.assertEqual(len(task.navigator.local_unknown_closers), 3)
        self.assertTrue(task.I_PAGE.match({'I_BLUE_PAGE'}))
        self.assertFalse(task.I_PAGE.match({'I_PAGE'}))

    def test_one_account_skin_selection_does_not_change_another_task(self):
        blue, _ = self.task()
        default, _ = self.task()
        blue.configure_courtyard_skin(CourtyardSkin.BLUE)
        default.configure_courtyard_skin(CourtyardSkin.DEFAULT)
        blue.I_PAGE.match({'I_BLUE_PAGE'})
        self.assertIsNot(blue.I_PAGE, default.I_PAGE)
        self.assertTrue(default.I_PAGE.match({'I_PAGE'}))
        self.assertFalse(default.I_PAGE.match({'I_BLUE_PAGE'}))
        self.assertEqual(len(default.navigator.local_unknown_closers), 2)


class CourtyardClaimedFrameTests(unittest.TestCase):
    def image(self, name):
        path = ROOT / 'tests/fixtures/courtyard_affairs' / f'{name}.png'
        crop = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(crop)
        self.assertEqual(crop.shape[:2], (650, 555))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[48:698, 690:1245] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return image

    def test_real_claimed_title_is_distinct_from_daily_page(self):
        rule = copy(BlueCourtyardAssets.I_BLUE_SUCCESS_CLAIMED)
        rule.roi_front = copy(rule.roi_front)
        self.assertTrue(rule.match(self.image('claimed')))
        self.assertFalse(rule.match(self.image('before')))
        self.assertFalse(rule.match(self.image('post')))

    def test_real_claimed_popup_cannot_report_daily_completion(self):
        self.assertFalse(BlueCompletionDetector().ready(self.image('claimed')))

    def test_inactive_tab_evidence_overrides_otherwise_completed_daily_frame(self):
        post = self.image('post')
        detector = BlueCompletionDetector()
        self.assertTrue(detector.ready(post))
        detector.inactive = Mock()
        detector.inactive.match.return_value = True
        self.assertFalse(detector.ready(post))

    def test_reward_close_click_is_outside_daily_claim_and_special_controls(self):
        # The recorded reward is dismissed in its blank bottom region. Keep
        # every random point clear of the lower-right one-click/task buttons.
        x, y, width, height = BlueCourtyardAssets.C_BLUE_REWARD_CLOSE.roi_front
        self.assertEqual((x, y, width, height), (610, 674, 90, 26))
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + width, 1280)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(y + height, 720)
        self.assertLess(x + width, 1100)


class CourtyardCachedWorkerTests(unittest.TestCase):
    def test_fresh_task_runs_with_real_legacy_modules_still_cached(self):
        # An account worker may retain these exact pre-change modules while
        # loading a fresh script_task.py. Execute in a disposable interpreter
        # so injecting the old modules cannot affect any other test or worker.
        fixture = ROOT / 'tests/fixtures/courtyard_affairs/legacy_worker'
        program = r'''
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

root = Path(sys.argv[1])
fixture = Path(sys.argv[2])

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

import tasks.CourtyardAffairs
old = {}
for name in ('config', 'skin_assets', 'completion_state'):
    old[name] = load('tasks.CourtyardAffairs.' + name,
                     fixture / ('legacy_' + name + '.py'))
assert not hasattr(old['config'], 'CourtyardSkin')
assert not hasattr(old['skin_assets'].BlueCourtyardAssets, 'I_BLUE_SUCCESS_CLAIMED')
assert not hasattr(old['completion_state'].CompletionAwareCourtyardMixin,
                   'configure_courtyard_skin')

fresh = load('courtyard_cached_script_task_test',
             root / 'tasks/CourtyardAffairs/script_task.py')
assert hasattr(fresh.ScriptTask, 'configure_courtyard_skin')
assert hasattr(fresh.ScriptTask, 'I_BLUE_SUCCESS_CLAIMED')
assert fresh.ScriptTask.I_BLUE_SUCCESS_CLAIMED.file.endswith('blue_success_claimed.png')

class Rule:
    def __init__(self, name):
        self.name = name
        self.roi_front = [1, 2, 3, 4]
        self.roi_back = (0, 0, 1280, 720)
        self.threshold = .85
        self.method = 'Template matching'
        self.file = name + '.png'
    def match(self, image, threshold=None):
        return self.name in image

class Harness(fresh.ScriptTask):
    def __init__(self):
        self.config = SimpleNamespace(courtyard_affairs=SimpleNamespace(
            courtyard_affairs_config=old['config'].CourtyardAffairsConfig()))
        self.frames = [
            {'I_BLUE_SUCCESS_CLAIMED'},
            {'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED'},
            {'I_NO_TASKS'},
        ]
        self.current = {'I_BLUE_PAGE'}
        self.device = SimpleNamespace(image=self.current)
        self.actions = []
        for name in ('I_NOTE', 'I_PAGE', 'I_NO_TASKS', 'I_HARVEST_SOUL_2',
                     'I_HARVEST_SOUL_3', 'I_UI_AWARD', 'I_CONFIRM', 'I_DAILY',
                     'I_SUCCESS_CLAIMED', 'I_SKIP', 'I_LOGIN_RED_CLOSE',
                     'I_COMPLETE_TASKS', 'I_BLUE_PAGE', 'I_BLUE_DAILY_SELECTED',
                     'I_BLUE_DAILY_INACTIVE', 'I_BLUE_COMPLETE_TASKS',
                     'I_BLUE_SUCCESS_CLAIMED', 'I_UI_BACK_YELLOW',
                     'I_CHECK_MAIN', 'I_CHECK_EXPLORATION', 'I_CHECK_RECORDS',
                     'I_CHECK_GUILD'):
            setattr(self, name, Rule(name))
        self.C_BLUE_REWARD_CLOSE = SimpleNamespace(name='blue-safe-close')
        local = []
        self.navigator = SimpleNamespace(local_unknown_closers=local,
            add_unknown_closer=lambda *actions: local.extend(actions))
        self.goto_page = Mock()
        self.set_next_run = Mock()
        self.ui_click_multi_scale = lambda target, stop, **kwargs: stop.match(self.current)
    def screenshot(self):
        assert self.frames, 'Fresh claim loop did not terminate'
        self.current = self.frames.pop(0)
        self.device.image = self.current
    def appear(self, rule, **kwargs):
        return rule.match(self.current)
    def appear_then_click(self, rule, interval=None, action=None):
        if self.appear(rule):
            self.actions.append((action if action is not None else rule).name)
            return True
        return False
    def ui_reward_appear_click(self):
        return False

timer = Mock()
timer.start.return_value = timer
timer.reached.return_value = False
detector = Mock()
detector.ready.return_value = False
task = Harness()
with patch('tasks.CourtyardAffairs.selected_skin.Timer', return_value=timer), \
        patch('tasks.CourtyardAffairs.selected_skin.BlueCompletionDetector', return_value=detector):
    try:
        task.run()
    except fresh.TaskEnd:
        pass
    else:
        raise AssertionError('TaskEnd expected')
assert not task.frames
assert task.actions == ['blue-safe-close']
assert task._courtyard_skin == fresh.CourtyardSkin.AUTO
assert task.goto_page.call_count == 2
assert len(task.navigator.local_unknown_closers) == 2
task.set_next_run.assert_called_once_with('CourtyardAffairs', success=True,
                                        finish=True, server=True)
for name, module in old.items():
    assert sys.modules['tasks.CourtyardAffairs.' + name] is module
assert not hasattr(old['config'], 'CourtyardSkin')
print('CACHED_WORKER_OK')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', program,
                                 str(ROOT), str(fixture)], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('CACHED_WORKER_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
