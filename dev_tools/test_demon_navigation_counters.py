from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from tasks.DemonEncounter.assets import DemonEncounterAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.default_pages import page_main, page_demon_encounter_realworld
from tasks.GameUi.navigator import GameUi
from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT.parent / 'migration-records'


def read_frame(path):
    if not path.exists():
        raise unittest.SkipTest('Local incident screenshot is not part of the public repository')
    return cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR),
                        cv2.COLOR_BGR2RGB)


class DemonNavigationCounterTests(unittest.TestCase):
    def test_completed_boss_counter_keeps_full_text_height(self):
        frame = read_frame(RECORDS / 'account15-fox-victory-20261021-1832/live-15.png')
        self.assertEqual(DemonEncounterAssets.O_DE_CHALLENGE_COUNT.ocr(frame), (0, 1, 1))

    def test_four_remaining_discoveries_do_not_read_fourteen(self):
        frame = read_frame(RECORDS / 'account13-login-20261021-1835/live-after.png')
        self.assertEqual(DemonEncounterAssets.O_DE_COUNTER.ocr(frame), (4, 0, 4))
        self.assertEqual(DemonEncounterAssets.O_DE_CHALLENGE_COUNT.ocr(frame), (1, 0, 1))

    def test_realworld_marker_wins_over_false_courtyard_candidate(self):
        files = list((ROOT / 'log/error/1792578893574').glob('*.png'))
        if not files:
            self.skipTest('Local incident screenshot is not part of the public repository')
        frame = read_frame(files[0])
        task = SimpleNamespace(
            navigator=SimpleNamespace(current_page=None),
            maybe_screenshot=Mock(), screenshot=Mock(),
        )
        task.appear = lambda r: r is GameUiAssets.I_CHECK_MAIN or r.match(frame)
        task.match_page_once = lambda p: p.recognizer.evaluate(task)
        self.assertTrue(task.match_page_once(page_demon_encounter_realworld))
        self.assertIs(GameUi._detect_pages(task, [page_main, page_demon_encounter_realworld]),
                      page_demon_encounter_realworld)

    def test_preset_reenters_records_after_delayed_back_navigation(self):
        method = extract_methods(ROOT / 'tasks/Component/SwitchSoul/switch_soul.py',
            'SwitchSoul', ('click_preset',), {'GameUiAssets': GameUiAssets,
                                           'logger': FakeLogger()})['click_preset']
        frames = iter([{GameUiAssets.I_CHECK_MAIN}, {'preset'}, {'switch1'}])
        task = SimpleNamespace(
            I_SOU_SWITCH_1='switch1', I_SOU_SWITCH_2='switch2', I_SOU_SWITCH_3='switch3',
            I_SOU_SWITCH_4='switch4', I_SOU_TEAM_PRESENT='team', I_SOUL_PRESET='preset',
            goto_page=Mock(), click=Mock(),
        )
        task.screenshot = lambda: setattr(task, 'visible', next(frames))
        task.appear = lambda r: r in task.visible
        method(task)
        task.goto_page.assert_called_once()
        task.click.assert_called_once_with('preset', interval=3)


if __name__ == '__main__':
    unittest.main()
