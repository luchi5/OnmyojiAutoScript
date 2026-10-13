"""Offline replay of restricted presets; no game, account or OCR imports."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from dev_tools.test_demon_preset_transition import Harness, Marker, extract, frame, menu, Logger
from dev_tools.test_area_boss_battle_prepare import Harness as PreparationHarness, prepare, battle

ROOT = Path(__file__).resolve().parents[1]


def harness(frames, rejected=False):
    h = Harness(frames, demon=False)
    h.I_DE_LOCATION = Marker('I_DE_LOCATION')
    h.O_AB_PRESET_REJECTED = Marker('rejected')
    h.C_AB_DISMISS_PRESET = Marker('dismiss')
    h._area_boss_battle_started = lambda: h.appear('I_BATTLE_INFO')
    h.ocr_appear = lambda target: rejected
    h._defer_battle_preparation = lambda: (_ for _ in ()).throw(RuntimeError('deferred'))
    h.screenshot_limit = 300
    def capture():
        h.frame = h.frames[min(h.screenshots, len(h.frames) - 1)]
        h.device.image = h.frame
        h.screenshots += 1
        h.clock.now += 0.5
        if h.screenshots > h.screenshot_limit:
            raise AssertionError('unbounded preset loop')
    h.screenshot = capture
    # A rejected confirmation leaves the menu visible, as in the incident.
    h.click = lambda marker, **kwargs: h.clicks.append(str(marker)) or True
    methods = extract(ROOT/'tasks/AreaBoss/script_task.py', 'ScriptTask',
                      ('_preset_selection_fallback',), {
                          'logger': Logger(),
                          'time': SimpleNamespace(monotonic=lambda: h.clock.now, sleep=h.sleep),
                      })
    h._preset_selection_fallback = methods['_preset_selection_fallback'].__get__(h)
    return h


class PresetRejectionTests(unittest.TestCase):
    def test_rejected_preset_closes_after_one_confirmation(self):
        frames = [menu() for _ in range(6)] + [frame('I_PREPARE_HIGHLIGHT')]
        h = harness(frames, rejected=True)
        h.switch_preset_team(True)
        self.assertEqual(h.clicks.count('I_PRESET_ENSURE'), 1)
        self.assertEqual(h.clicks.count('dismiss'), 1)
        self.assertTrue(h.is_in_prepare(False))

    def test_missed_toast_has_bounded_confirmation_and_close(self):
        h = harness([menu() for _ in range(9)] + [frame('I_PREPARE_HIGHLIGHT')])
        h.switch_preset_team(True)
        self.assertEqual(h.clicks.count('I_PRESET_ENSURE'), 3)
        self.assertLessEqual(h.clicks.count('dismiss'), 3)
        self.assertTrue(h.is_in_prepare(False))

    def test_failed_panel_close_defers_without_prepare_click(self):
        h = harness([menu()], rejected=True)
        with self.assertRaisesRegex(RuntimeError, 'deferred'):
            h.switch_preset_team(True)
        self.assertLessEqual(h.clicks.count('dismiss'), 3)
        self.assertNotIn('I_PREPARE_HIGHLIGHT', h.clicks)

    def test_successful_preset_does_not_use_fallback(self):
        h = harness([menu(), menu(), menu(), frame('I_PREPARE_HIGHLIGHT')])
        h.switch_preset_team(True)
        self.assertNotIn('dismiss', h.clicks)

    def test_preparation_continues_with_original_team_after_fallback(self):
        h = PreparationHarness([prepare(), prepare(), battle(), battle()])
        fallback = harness([menu(), frame('I_PREPARE_HIGHLIGHT')], rejected=True)
        h.switch_preset_team = lambda *args: fallback._preset_selection_fallback()
        self.assertTrue(h.battle_before(None, h.battle_config))
        self.assertTrue(h.prepare_clicks())
        self.assertEqual(h.deferred, 0)


if __name__ == '__main__':
    unittest.main()
