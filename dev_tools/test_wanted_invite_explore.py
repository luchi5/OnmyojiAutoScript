"""Regressions for wanted-specific invitations and selective exploration."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dev_tools.test_bondling_battle_entry import extract_methods


ROOT = Path(__file__).resolve().parents[1]
LOGGER = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)


class EndFrame(Exception):
    pass


class WantedInviteTests(unittest.TestCase):
    def invite(self, selected):
        markers = {'I_WQ_INVITE_1', 'I_WQ_INVITE_ENSURE'}
        if selected:
            markers.add('I_WQ_INVITE_SELECTED')
        clicks = []
        task = SimpleNamespace(
            screenshot=lambda: None,
            resume_exploration_encounter=lambda: False,
            appear=lambda marker: marker in markers,
            ui_click=lambda button, stop, **kwargs: clicks.append(button),
            click=clicks.append,
            ui_click_until_disappear=clicks.append,
        )
        for name in ('I_WQ_INVITE_1', 'I_WQ_INVITE_ENSURE',
                     'I_WQ_INVITE_SELECTED', 'I_SELECTED', 'I_INVITE_ENSURE'):
            setattr(task, name, name)
        for i in range(1, 6):
            setattr(task, f'I_WQ_FRIEND_{i}', f'I_WQ_FRIEND_{i}')
        method = extract_methods(
            ROOT / 'tasks/WantedQuests/script_task.py', 'ScriptTask',
            ('invite_random',), {'RuleImage': object, 'logger': LOGGER,
                                'sleep': lambda _: None},
        )['invite_random']
        result = method(task, task.I_WQ_INVITE_1)
        return result, clicks

    def test_wanted_selection_confirms_despite_no_team_selection_marker(self):
        result, clicks = self.invite(True)
        self.assertTrue(result)
        self.assertEqual(clicks[-1], 'I_WQ_INVITE_ENSURE')
        self.assertNotIn('I_INVITE_ENSURE', clicks)

    def test_no_selection_does_not_send_invitation(self):
        result, clicks = self.invite(False)
        self.assertFalse(result)
        self.assertNotIn('I_WQ_INVITE_ENSURE', clicks)


class WantedExploreTests(unittest.TestCase):
    def test_animated_map_without_targets_returns_before_repeated_swipe_guard(self):
        from tasks.Component.Costume.config import MainType
        scene = SimpleNamespace(ENTRANCE=1, WORLD=2, MAIN=3, BATTLE_PREPARE=4, UNKNOWN=0)
        timer = SimpleNamespace(reached=lambda: True, started=lambda: True, reset=lambda: None)
        swipes = []
        def swipe(*args, **kwargs):
            swipes.append(True)
            if len(swipes) >= 10:
                self.fail('Reached device repeated-swipe guard without checking wanted progress')
            return True
        task = SimpleNamespace(
            current_main_type=MainType.COSTUME_MAIN_16,
            TEMPLATE_GIF=SimpleNamespace(match_origin=lambda: None),
            device=SimpleNamespace(image=None),
            screenshot=lambda: None, resume_exploration_encounter=lambda: False,
            appear=lambda r: isinstance(r, str) and r in {'I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK'},
            get_current_scene=lambda **kwargs: scene.MAIN,
            _match_end=SimpleNamespace(stable=lambda *args, **kwargs: False, refresh=Mock()),
            swipe=swipe, quit_explore=Mock(), ui_click=Mock(return_value=True),
        )
        for name in ('I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK', 'I_BATTLE_REWARD',
                     'I_BOSS_BATTLE_BUTTON', 'I_CHECK_EXPLORATION', 'S_SWIPE_BACKGROUND_RIGHT'):
            setattr(task, name, name)
        method = extract_methods(ROOT / 'tasks/WantedQuests/explore.py', 'WQExplore', ('explore',),
            {'RuleImage': object, 'logger': LOGGER, 'Timer': lambda *args: timer,
             'Scene': scene, 'GameStuckError': RuntimeError})['explore']
        self.assertIsNone(method(task, 'goto', 3))
        self.assertLess(len(swipes), 10)
        task.quit_explore.assert_called_once()
        task.ui_click.assert_called_once()

    def explore_frame(self, marker):
        markers = {'I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK', marker}
        shots = 0
        attacks = []

        def screenshot():
            nonlocal shots
            shots += 1
            if shots > 2:
                raise EndFrame()

        class Timer:
            def __init__(self, *args, **kwargs):
                pass

            def reached(self):
                return False

            def started(self):
                return True

        from tasks.Component.Costume.config import MainType
        scene = SimpleNamespace(ENTRANCE=1, WORLD=2, MAIN=3,
                                BATTLE_PREPARE=4, UNKNOWN=0)

        def get_scene(**kwargs):
            if not kwargs.get("reuse_screenshot", False):
                screenshot()
            return scene.MAIN

        task = SimpleNamespace(
            current_main_type=MainType.COSTUME_MAIN_16,
            TEMPLATE_GIF=SimpleNamespace(match_origin=lambda: None,
                                         match_with_multi_scale=lambda: None),
            screenshot=screenshot,
            resume_exploration_encounter=lambda: False,
            appear=lambda rule: ((isinstance(rule, str) and rule in markers)
                                 or (rule is task.TEMPLATE_GIF and marker == 'TEMPLATE_GIF')),
            get_current_scene=get_scene,
            fire=lambda button: attacks.append(button) or True,
            minions_cnt=0,
        )
        for name in ('I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK',
                     'I_BATTLE_REWARD', 'I_BOSS_BATTLE_BUTTON',
                     'I_NORMAL_BATTLE_BUTTON'):
            setattr(task, name, name)
        method = extract_methods(
            ROOT / 'tasks/WantedQuests/explore.py', 'WQExplore', ('explore',),
            {'RuleImage': object, 'logger': LOGGER, 'Timer': Timer, 'Scene': scene},
        )['explore']
        with self.assertRaises(EndFrame):
            method(task, 'goto', 3)
        return attacks

    def test_visible_sword_without_wanted_glow_is_not_attacked(self):
        self.assertEqual(self.explore_frame('I_NORMAL_BATTLE_BUTTON'),
                         [])

    def test_wanted_glow_is_attacked(self):
        attacks = self.explore_frame('TEMPLATE_GIF')
        self.assertEqual(len(attacks), 1)
        self.assertTrue(hasattr(attacks[0], 'match_origin'))

    def test_boss_keeps_priority_over_minions(self):
        self.assertEqual(self.explore_frame('I_BOSS_BATTLE_BUTTON'),
                         ['I_BOSS_BATTLE_BUTTON'])

    def test_automatic_world_return_rechecks_progress_before_requested_rounds(self):
        scene = SimpleNamespace(ENTRANCE=1, WORLD=2, MAIN=3,
                                BATTLE_PREPARE=4, UNKNOWN=0)
        scenes = [scene.MAIN, scene.WORLD, scene.WORLD]
        attacks, refreshes = [], []

        def get_scene(**kwargs):
            if not scenes:
                self.fail('Exploration kept waiting on the world map')
            return scenes.pop(0)

        class Timer:
            def __init__(self, *args, **kwargs):
                pass

        from tasks.Component.Costume.config import MainType
        task = SimpleNamespace(
            current_main_type=MainType.COSTUME_MAIN_16,
            TEMPLATE_GIF=SimpleNamespace(match_origin=lambda: None),
            _match_end=SimpleNamespace(refresh=lambda: refreshes.append(True)),
            screenshot=lambda: None,
            resume_exploration_encounter=lambda: False,
            appear=lambda rule: isinstance(rule, str) and rule in {
                'I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK', 'I_BOSS_BATTLE_BUTTON'},
            get_current_scene=get_scene,
            fire=lambda button: attacks.append(button) or True,
            wait_until_stable=lambda *args, **kwargs: None,
            minions_cnt=0,
        )
        for name in ('I_UI_BACK_YELLOW', 'I_E_EXPLORATION_CLICK',
                     'I_BATTLE_REWARD', 'I_BOSS_BATTLE_BUTTON', 'I_CHECK_EXPLORATION'):
            setattr(task, name, name)
        method = extract_methods(
            ROOT / 'tasks/WantedQuests/explore.py', 'WQExplore', ('explore',),
            {'RuleImage': object, 'logger': LOGGER, 'Timer': Timer, 'Scene': scene},
        )['explore']
        self.assertIsNone(method(task, 'goto', 3))
        self.assertEqual(attacks, ['I_BOSS_BATTLE_BUTTON'])
        self.assertEqual(refreshes, [True])


if __name__ == '__main__':
    unittest.main()
