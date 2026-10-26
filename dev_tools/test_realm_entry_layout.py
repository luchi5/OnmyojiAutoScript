"""Known scene layouts supplement legacy realm entry recognition, without devices."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.atom.image import RuleImage
from module.atom.gif import RuleGif
from module.config.config_model import ConfigModel
from module.exception import GameStuckError
from tasks.Component.Costume.config import CostumeConfig, RealmType
from tasks.Component.CostumeRealm.entry import PROFILES, detect_realm_entry
from tasks.KekkaiActivation.script_task import ScriptTask
from dev_tools.test_bondling_battle_entry import extract_methods, FakeClock, FakeTimer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/KekkaiActivation/script_task.py'


def load(path):
    return cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), 1), cv2.COLOR_BGR2RGB)


def make_task(image):
    model = ConfigModel()
    model.global_game.costume_config.costume_realm_type = RealmType.COSTUME_REALM_SHEN_AN
    config = SimpleNamespace(model=model, global_game=model.global_game, config_name='offline')
    # Production assets are shared with navigation. Give this offline task
    # private costume rules so replaying Shen An cannot contaminate later suites.
    task = ScriptTask.__new__(ScriptTask)
    for key in ('I_CHECK_MAIN', 'I_MAIN_GOTO_EXPLORATION', 'I_MAIN_GOTO_SUMMON',
                'I_MAIN_GOTO_TOWN', 'I_PET_HOUSE', 'I_REALM_SHIN', 'I_SHI_DEFENSE',
                'I_SHI_GROWN', 'I_SHI_CARD', 'I_CARD_EXP', 'I_BOX_EXP', 'I_BOX_AP',
                'I_UTILIZE_EXP', 'I_BOX_EXP_MAX'):
        if not hasattr(task, key):
            continue
        rule = getattr(task, key)
        def clone(item):
            return RuleImage(tuple(item.roi_front), tuple(item.roi_back),
                             item.method, item.threshold, item.file)
        fresh = clone(rule)
        if hasattr(rule, 'targets'):
            RuleGif.attach_to(fresh, [clone(item) for item in rule.targets])
            fresh.preserve_variant_rules = getattr(rule, 'preserve_variant_rules', False)
        setattr(task, key, fresh)
    ScriptTask.__init__(task, config, SimpleNamespace(image=image))
    return task


def replay(task, *, show_panel=True):
    clock = FakeClock(2)
    clicks = []
    appear = task.appear
    def visible(rule, **kwargs):
        if rule is task.I_A_CHECK_CARD and show_panel and clicks:
            return True
        return appear(rule, **kwargs)
    task.appear = visible
    task.screenshot = clock.advance
    task.click = lambda target, **kw: clicks.append((target.name, tuple(target.roi_front)))
    method = extract_methods(SOURCE, 'ScriptTask', ('goto_cards',), {
        'RuleImage': RuleImage, 'Timer': lambda n: FakeTimer(clock, n),
        'time': SimpleNamespace(monotonic=lambda: clock.now,
                                sleep=lambda n: setattr(clock, 'now', clock.now + n)),
        'GameStuckError': GameStuckError, 'logger': Mock(),
    })['goto_cards']
    return clicks, lambda: method(task)


class RealmEntryLayoutTests(unittest.TestCase):
    def incident(self):
        # Only scene/entry crops at their original positions; no account UI.
        return load(ROOT / 'dev_tools/fixtures/realm10_collected_entry.png')

    def test_new_settings_are_optional_and_exposed_as_boolean_and_multi_enum(self):
        model = ConfigModel()
        args = model.script_task('GlobalGame')['costume_config']
        by_name = {row['name']: row for row in args}
        self.assertEqual(by_name['realm_entry_auto_detect']['type'], 'boolean')
        self.assertEqual(by_name['realm_entry_candidates']['type'], 'multi_enum')
        self.assertTrue(CostumeConfig().realm_entry_auto_detect)
        self.assertEqual(CostumeConfig(realm_entry_candidates='["costume_realm_shen_an"]')
                         .realm_entry_candidates, ['costume_realm_shen_an'])

    def test_incident_matches_scene_and_anchor_and_clicks_then_confirms_panel(self):
        image = self.incident()
        task = make_task(image)
        self.assertFalse(task.appear_multi_scale(task.I_SHI_CARD))
        match = detect_realm_entry(image, ['costume_realm_shen_an'])
        self.assertIsNotNone(match)
        self.assertGreater(match.scene_score, .99)
        self.assertGreater(match.anchor_score, .90)
        self.assertEqual(match.rect, (767, 493, 13, 38))
        clicks, run = replay(task)
        run()
        self.assertEqual(clicks, [('realm_card_entry_costume_realm_shen_an', match.rect)])

    def test_anchor_on_another_background_does_not_identify_the_skin(self):
        image = self.incident()
        image[170:270, 520:640] = 0
        self.assertIsNone(detect_realm_entry(image, ['costume_realm_shen_an']))

    def test_scene_without_anchor_or_with_anchor_in_wrong_place_is_rejected(self):
        image = self.incident()
        anchor = image[488:536, 632:649].copy()
        image[470:560, 610:675] = 0
        self.assertIsNone(detect_realm_entry(image, ['costume_realm_shen_an']))
        image[300:348, 632:649] = anchor
        self.assertIsNone(detect_realm_entry(image, ['costume_realm_shen_an']))

    def test_best_score_is_selected_only_among_qualified_candidates(self):
        p = PROFILES[0]
        profiles = (replace(p, key='a', scene_file='a'),
                    replace(p, key='b', scene_file='b'),
                    replace(p, key='c', scene_file='c'))
        def score(image, filename, roi):
            return ({'a': .91, 'b': .98, 'c': .89}.get(filename, .95), (632, 488, 17, 48))
        with patch('tasks.Component.CostumeRealm.entry._score', side_effect=score):
            match = detect_realm_entry(np.zeros((720, 1280, 3), np.uint8),
                                       ['a', 'b', 'c'], profiles=profiles)
        self.assertEqual(match.skin, 'b')

    def test_original_entry_still_works_without_a_known_skin_signature(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        rule = RuleImage((891, 300, 31, 55), (800, 200, 300, 300),
                         'Template matching', .8, './tasks/KekkaiUtilize/realm/realm_shi_card.png')
        h, w = rule.image.shape[:2]
        image[300:300+h, 891:891+w] = rule.image
        task = make_task(image)
        task._realm_entry_fallback_target = Mock(side_effect=AssertionError('Bypassed legacy entry'))
        clicks, run = replay(task)
        run()
        self.assertEqual(len(clicks), 1)
        task._realm_entry_fallback_target.assert_not_called()

    def test_collected_screen_realm_card_enters_using_unobscured_label(self):
        image = load(ROOT / 'dev_tools/fixtures/realm_card_screen_collected.png')
        task = make_task(image)
        task._realm_entry_fallback_target = Mock(side_effect=AssertionError('Bypassed label entry'))
        clicks, run = replay(task)
        run()
        self.assertEqual(len(clicks), 1)
        _, (x, y, width, height) = clicks[0]
        self.assertEqual((x, y, width, height), (794, 318, 13, 32))
        task._realm_entry_fallback_target.assert_not_called()

    def test_collected_screen_realm_label_still_requires_panel_confirmation(self):
        task = make_task(load(ROOT / 'dev_tools/fixtures/realm_card_screen_collected.png'))
        clicks, run = replay(task, show_panel=False)
        with self.assertRaisesRegex(GameStuckError, '45 seconds'):
            run()
        self.assertEqual(len(clicks), 3)

    def test_switch_off_candidates_and_growth_overlay_suppress_layout(self):
        task = make_task(self.incident())
        costume = task.config.global_game.costume_config
        costume.realm_entry_auto_detect = False
        self.assertIsNone(task._realm_entry_fallback_target())
        costume.realm_entry_auto_detect = True
        appear = task.appear
        task.appear = lambda rule, **kw: True if rule is task.I_UI_BACK_BLUE else appear(rule, **kw)
        self.assertIsNone(task._realm_entry_fallback_target())
        self.assertIsNone(detect_realm_entry(self.incident(), []))

    def test_two_clicks_without_open_panel_are_a_failure(self):
        task = make_task(self.incident())
        clicks, run = replay(task, show_panel=False)
        with self.assertRaisesRegex(GameStuckError, 'after two clicks'):
            run()
        self.assertEqual(len(clicks), 2)

    def test_old_realm_skins_and_unrelated_pages_do_not_use_new_layout(self):
        for name in ('realm_banner15_current.png', 'realm_banner14_current.png',
                     'realm_banner9_compact.png', 'realm_skin_selector3.png',
                     'realm_skin_selector5.png'):
            with self.subTest(name=name):
                self.assertIsNone(detect_realm_entry(load(ROOT/'dev_tools/fixtures'/name),
                                                    ['costume_realm_shen_an']))
        self.assertIsNone(detect_realm_entry(None, ['costume_realm_shen_an']))
        self.assertIsNone(detect_realm_entry(np.zeros((360, 640, 3), np.uint8),
                                            ['costume_realm_shen_an']))


if __name__ == '__main__':
    unittest.main()
