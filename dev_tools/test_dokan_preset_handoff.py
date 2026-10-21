"""Dokan preset selection must yield to battle/result frames at every stage."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from dev_tools.test_bondling_battle_entry import extract_methods
from dev_tools.test_demon_preset_transition import Harness, Logger, frame, menu, prepare
from tasks.Dokan.dokan_scene import DokanScene
from tasks.Dokan.settlement_recovery import CONTINUE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/Dokan/script_task.py'
EXTRA_MARKERS = ('I_RYOU_DOKAN_WIN', 'I_RYOU_DOKAN_BATTLE_OVER',
                 'I_RYOU_DOKAN_TOPPA_RANK', 'I_RYOU_DOKAN_SPOILS_OF_DOKAN',
                 'I_RYOU_DOKAN_CENTER_TOP', 'I_RYOU_DOKAN_IN_FIELD')


def harness(frames):
    h = Harness(frames, demon=False)
    for name in EXTRA_MARKERS:
        setattr(h, name, name)
    methods = extract_methods(SOURCE, 'ScriptTask',
        ('_dokan_preset_result_visible', '_dokan_preset_battle_visible',
         '_preset_selection_interrupted', 'dokan_battle'),
        {'logger': Logger(), 'Dokan': object, 'GeneralBattleConfig': object,
         'GeneralBattle': SimpleNamespace(I_WIN='I_WIN', I_FALSE='I_FALSE',
                                         I_REWARD_GOLD='I_REWARD_GOLD'),
         'sleep': lambda _: None})
    for name, method in methods.items():
        setattr(h, name, method.__get__(h))
    return h


class DokanPresetHandoffTests(unittest.TestCase):
    def test_battle_and_results_interrupt_all_preset_stages(self):
        for marker in ('I_BATTLE_INFO', 'I_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD',
                       'I_RYOU_DOKAN_WIN', 'I_RYOU_DOKAN_BATTLE_OVER',
                       'I_RYOU_DOKAN_TOPPA_RANK', 'I_RYOU_DOKAN_SPOILS_OF_DOKAN', CONTINUE):
            for prefix, expected in (([], []), ([menu()], []),
                                     ([menu(), menu()], []),
                                     ([menu(), menu(), menu()], ['team3'])):
                with self.subTest(marker=marker, stage=len(prefix)):
                    h = harness(prefix + [frame(marker)])
                    self.assertIsNone(h.switch_preset_team(True, 2, 3))
                    self.assertEqual(h.clicks, expected)
                    self.assertEqual(h.ocr_calls, [])

    def test_prepare_or_menu_with_battle_info_keeps_normal_preset_selection(self):
        for visible in (prepare('I_BATTLE_INFO'), menu(visible={'I_PRESET_ENSURE', 'I_BATTLE_INFO'})):
            h = harness([visible])
            self.assertFalse(h._preset_selection_interrupted())
        h = harness([menu(), menu(), menu(), menu(), frame()])
        self.assertIsNone(h.switch_preset_team(True, 2, 3))
        self.assertEqual(h.clicks, ['team3', 'I_PRESET_ENSURE'])

    def test_active_battle_handoff_does_not_wait_for_prepare_button(self):
        h = harness([frame('I_BATTLE_INFO'), frame('I_WIN')])
        h.wait_until_appear = Mock(side_effect=AssertionError('Waited for stale prepare UI'))
        h.stop_green_mark = Mock()
        cfg = SimpleNamespace(general_battle_config=SimpleNamespace(green_enable=False))
        self.assertTrue(h.dokan_battle(cfg))
        h.wait_until_appear.assert_not_called()
        h.stop_green_mark.assert_called_once()

    def test_result_arriving_before_battle_wait_returns_to_caller(self):
        h = harness([frame('I_WIN')])
        h.wait_until_appear = Mock(side_effect=AssertionError('Waited on a result frame'))
        cfg = SimpleNamespace(general_battle_config=SimpleNamespace(green_enable=False))
        self.assertFalse(h.dokan_battle(cfg))
        h.wait_until_appear.assert_not_called()
        self.assertFalse(h.clicks)

    def test_each_caller_returns_result_to_outer_scene_loop(self):
        class EndLoop(Exception):
            pass
        method = extract_methods(SOURCE, 'ScriptTask', ('run',),
            {'datetime': datetime, 'Dokan': object, 'DokanScene': DokanScene,
             'Timer': lambda *_: Mock(), 'logger': Logger(), 'sleep': lambda _: None})['run']
        for scene in (DokanScene.RYOU_DOKAN_SCENE_IN_FIELD,
                      DokanScene.RYOU_DOKAN_SCENE_BATTLE_MASTER_FIRST,
                      DokanScene.RYOU_DOKAN_SCENE_BATTLE_MASTER_SECOND):
            with self.subTest(scene=scene):
                cfg = SimpleNamespace(
                    dokan_config=SimpleNamespace(dokan_attack_priority=0, monday_to_thursday=False,
                        switch_preset_enable=True, preset_group_1='1,1', preset_group_2='1,2',
                        parse_preset_group=lambda _: (1, 1)),
                    attack_count_config=SimpleNamespace(init_attack_count=Mock(),
                        attack_dokan_master_count=lambda: 2))
                calls = iter([(True, scene), (True, scene),
                              (True, DokanScene.RYOU_DOKAN_SCENE_BATTLE_OVER)])
                def next_scene(_):
                    try:
                        return next(calls)
                    except StopIteration:
                        raise EndLoop()
                h = SimpleNamespace(config=SimpleNamespace(dokan=cfg, save=Mock()),
                    screenshot=Mock(), _cancel_cheering_exit=lambda: False,
                    get_current_scene=next_scene, switch_preset_team=Mock(),
                    _dokan_preset_result_visible=lambda: True,
                    dokan_battle=Mock(side_effect=AssertionError('Entered battle on result frame')),
                    click=Mock(), C_DOKAN_TOPPA_RANK_CLOSE_AREA='result_continue')
                with self.assertRaises(EndLoop):
                    method(h)
                h.switch_preset_team.assert_called_once()
                h.dokan_battle.assert_not_called()
                h.click.assert_called_once_with('result_continue', interval=2)

    def test_saved_account05_victory_exits_before_preset_ocr(self):
        from tasks.Dokan.assets import DokanAssets
        from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
        files = list((ROOT / 'log/error/1792582950791').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        image = cv2.cvtColor(cv2.imdecode(np.fromfile(files[0], np.uint8), cv2.IMREAD_COLOR),
                             cv2.COLOR_BGR2RGB)
        h = harness([frame()])
        for owner in (DokanAssets, GeneralBattleAssets):
            for name in dir(owner):
                if name.startswith('I_'):
                    setattr(h, name, getattr(owner, name))
        h.screenshot = Mock()
        h.appear = lambda rule, **kwargs: rule.match(image, threshold=kwargs.get('threshold'))
        h.ocr_appear = Mock(side_effect=AssertionError('Preset OCR reached on saved victory'))
        h.click = Mock()
        self.assertIsNone(h.switch_preset_team(True, 1, 1))
        h.ocr_appear.assert_not_called()
        h.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
