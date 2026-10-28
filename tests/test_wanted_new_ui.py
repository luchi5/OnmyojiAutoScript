import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2

from module.exception import GameStuckError, RequestHumanTakeover
from tasks.WantedQuests.new_ui import WantedNewUi


class WantedNewUiTests(unittest.TestCase):
    def test_covered_title_requires_all_home_controls(self):
        task = self.task('covered_home_controls')
        self.assertFalse(task.new_wanted_rules()['home'].match(task.device.image))
        self.assertTrue(task.new_wanted_home())
        for x,y,w,h in ((1190,45,85,90),(1010,610,115,105),(1125,575,125,125)):
            task.device.image = self.frame('covered_home_controls')
            task.device.image[y:y+h,x:x+w] = 0
            self.assertFalse(task.new_wanted_home())

    @patch('tasks.WantedQuests.new_ui.sleep')
    def test_transition_then_covered_home_tracks_and_enters_exploration(self, sleep):
        task = self.task('explore')
        frames = iter([self.frame(name) for name in
                       ('explore', 'covered_home_controls', 'traced', 'explore')])
        task.screenshot = lambda: setattr(task.device, 'image', next(frames))
        task.click = Mock()
        task.invite_new_wanted = Mock()
        task.close_completed_wanted_home = Mock()
        task.I_CHECK_EXPLORATION = object()
        task.appear = Mock(return_value=True)
        self.assertTrue(task.prepare_new_wanted())
        task.click.assert_called_once()
        self.assertEqual(task.click.call_args.args[0].name, 'TRACE')
        task.close_completed_wanted_home.assert_not_called()
        task.invite_new_wanted.assert_called_once()
        sleep.assert_called_once_with(0.5)

    def test_title_without_either_tracking_button_closes_and_finishes(self):
        task = self.task('home')
        task.device.image[575:700,1125:1250] = 0
        task.screenshot = Mock()
        task.click = Mock()
        task.close_completed_wanted_home = Mock()
        task.invite_new_wanted = Mock()
        with patch('tasks.WantedQuests.new_ui.monotonic', side_effect=[0,1,3]):
            self.assertFalse(task.prepare_new_wanted())
        self.assertTrue(task._wanted_home_complete)
        task.close_completed_wanted_home.assert_called_once()
        task.click.assert_not_called()
        task.invite_new_wanted.assert_not_called()

    def test_completed_home_close_confirms_title_disappears(self):
        task = self.task('home')
        frames = iter([self.frame('home'), self.frame('explore')])
        task.screenshot = lambda: setattr(task.device, 'image', next(frames))
        task.click = Mock()
        task.close_completed_wanted_home()
        task.click.assert_called_once()
        self.assertEqual(task.click.call_args.args[0].name, 'CLOSE_HOME')

    def test_invite_icon_does_not_require_text_and_finds_translated_cards(self):
        task = self.task('invite_home')
        task.device.image[398:438, 195:340] = 0
        buttons = task.new_wanted_invite_buttons()
        self.assertEqual(len(buttons), 1)
        self.assertLessEqual(abs(buttons[0].roi_front[0] - 154), 2)
        icon = buttons[0].image
        h, w = icon.shape[:2]
        task.device.image[399:399+h, 438:438+w] = icon
        self.assertEqual(len(task.new_wanted_invite_buttons()), 2)

    def test_invite_icon_dispatches_existing_random_invite_flow(self):
        task = self.task('invite_home')
        task.screenshot = Mock()
        task.need_invite_vip = Mock(return_value=False)
        task.invite_random = Mock(return_value=True)
        task.invite_new_wanted()
        task.invite_random.assert_called_once()
        self.assertTrue(task.invite_random.call_args.args[0].match(task.device.image))

    def test_cooperation_detail_uses_structure_instead_of_heading(self):
        for name in ('detail', 'cooperation_detail', 'cooperation_own_complete'):
            task = self.task(name)
            self.assertTrue(task.new_wanted_detail())
            task.device.image[145:200, 620:1150] = 0
            self.assertTrue(task.new_wanted_detail())
            task.device.image[90:175, 1165:1255] = 0
            self.assertFalse(task.new_wanted_detail())

    def test_already_open_cooperation_stops_list_clicks(self):
        task = self.task('cooperation_detail')
        task.screenshot = Mock()
        task.click = Mock()
        task.open_wanted_detail(object())
        task.click.assert_not_called()
        self.assertTrue(task._using_new_wanted)

    def test_unknown_detail_has_three_click_limit_and_saves_evidence(self):
        task = self.task('explore')
        task._using_new_wanted = True
        task.screenshot = Mock()
        task.click = Mock(return_value=True)
        task.device.image_save = Mock()
        with patch('tasks.WantedQuests.new_ui.Timer') as timer:
            timer.return_value.start.return_value.reached.side_effect = [False] * 8 + [True]
            with self.assertRaises(RequestHumanTakeover):
                task.open_wanted_detail(object())
        self.assertEqual(task.click.call_count, 3)
        task.device.image_save.assert_called_once()

    def test_own_progress_never_uses_partner_or_guessed_counter(self):
        self.assertEqual(WantedNewUi.parse_own_progress('自己击败任意式神', '0/6'), (0, 6))
        self.assertEqual(WantedNewUi.parse_own_progress('悬赏目标：击败式神', '12/12'), (12, 12))
        for goal, counter in [('好友击败式神', '0/6'), ('未知标题', '0/6'),
                              ('自己击败式神', '076'), ('自己击败式神', '7/6'),
                              ('自己击败式神', '0/0')]:
            with self.assertRaises(RequestHumanTakeover):
                WantedNewUi.parse_own_progress(goal, counter)

    def test_own_completed_objective_does_not_start_battle(self):
        from tasks.WantedQuests.script_task import ScriptTask
        task = ScriptTask.__new__(ScriptTask)
        task.open_wanted_detail = Mock()
        task._using_new_wanted = True
        task.read_own_wanted_progress = Mock(return_value=(6, 6))
        task.read_partner_wanted_progress = Mock(return_value=(0, 1))
        task.ui_click_until_disappear = Mock()
        task.explore = Mock()
        task.secret = Mock()
        task.challenge = Mock()
        self.assertFalse(task.execute_mission(object(), 6, 1951))
        task.explore.assert_not_called()
        task.secret.assert_not_called()
        task.challenge.assert_not_called()

    def test_disagreeing_own_progress_stops_before_combat(self):
        task = self.task('cooperation_detail')
        task.screenshot = Mock()
        goal = Mock()
        goal.ocr.return_value = '自己击败跳跳哥哥'
        progress = Mock()
        progress.ocr.side_effect = ['0/6', '1/6']
        with patch('tasks.WantedQuests.new_ui.text', side_effect=[goal, progress]):
            with self.assertRaises(RequestHumanTakeover):
                task.read_own_wanted_progress()

    def test_skip_tracking_stops_when_cancellation_not_confirmed(self):
        task = self.task('cooperation_detail')
        task._using_new_wanted = True
        task.screenshot = Mock()
        task.click = Mock(return_value=True)
        with patch('tasks.WantedQuests.new_ui.Timer') as timer:
            timer.return_value.start.return_value.reached.side_effect = [False] * 6 + [True]
            with self.assertRaises(RequestHumanTakeover):
                task.skip_wanted_detail()
        self.assertEqual(task.click.call_count, 2)

    def frame(self, name):
        return cv2.cvtColor(cv2.imread(f'tests/fixtures/wanted_202610/{name}.png'), cv2.COLOR_BGR2RGB)

    def task(self, name):
        task = WantedNewUi()
        task.device = SimpleNamespace(image=self.frame(name))
        return task

    def test_home_and_detail_do_not_confuse_other_screens(self):
        for name in ('home', 'traced', 'detail', 'explore'):
            task = self.task(name)
            self.assertEqual(task.new_wanted_home(), name in ('home', 'traced'))
            self.assertEqual(task.new_wanted_detail(), name == 'detail')
        for path in Path('tests/fixtures/draft_duel').rglob('*.png'):
            im = cv2.imread(str(path))
            if im is None or im.shape[:2] != (720, 1280):
                continue
            task.device.image = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
            self.assertFalse(task.new_wanted_home(), str(path))
            self.assertFalse(task.new_wanted_detail(), str(path))

    def test_tracking_state_and_route_click_positions(self):
        home, traced = self.task('home'), self.task('traced')
        self.assertTrue(home.new_wanted_rules()['trace'].match(home.device.image))
        self.assertFalse(home.new_wanted_rules()['traced'].match(home.device.image))
        self.assertTrue(traced.new_wanted_rules()['traced'].match(traced.device.image))
        self.assertFalse(traced.new_wanted_rules()['trace'].match(traced.device.image))
        task = self.task('detail')
        task.bind_new_wanted_detail()
        for n, y in enumerate((284, 359, 434, 509), 1):
            rule = getattr(task, f'I_GOTO_{n}')
            self.assertTrue(rule.match(task.device.image))
            x, cy = rule.front_center()
            self.assertTrue(1054 <= x <= 1152)
            self.assertTrue(y <= cy <= y + 45)
        self.assertFalse(hasattr(home, 'I_GOTO_1'))

    def test_cooperation_requires_separate_validation(self):
        task = self.task('home')
        task.click = Mock()
        with self.assertRaises(RequestHumanTakeover):
            task.prepare_new_wanted(cooperation_only=True)
        task.click.assert_not_called()

    @patch('tasks.WantedQuests.new_ui.sleep')
    def test_unknown_screen_never_counts_as_completed(self, sleep):
        task = self.task('explore')
        task.screenshot = Mock()
        task.click = Mock()
        with self.assertRaises(GameStuckError):
            task.prepare_new_wanted()
        task.click.assert_not_called()
        self.assertEqual(task.screenshot.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
