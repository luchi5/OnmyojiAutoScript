"""Offline regressions for a detail page arriving after entry retries expire."""
from types import SimpleNamespace
import unittest

from dev_tools.test_bondling_battle_entry import extract_methods
from dev_tools.test_area_boss_normal_level import Harness as DifficultyHarness, AreaBossFloor, SOURCE


FILTER = {'filter': True}
ENTRY = {'filter_entry': True}
DETAIL = {'detail': True, 'fire': True}


class Harness(DifficultyHarness):
    def __init__(self, frames):
        super().__init__(frames, floor=AreaBossFloor.DEFAULT)
        self.clock.step = 3.1  # Allow the actual three-second entry click interval.
        self.I_FILTER = 'filter_entry'
        self.I_FIRE = 'fire'
        self.closed = []
        methods = extract_methods(SOURCE, 'ScriptTask', ('open_filter', 'open_boss_detail'), {
            'RuleImage': object, 'logger': self.logger,
            'time': SimpleNamespace(sleep=self.sleep),
        })
        for name, method in methods.items():
            setattr(self, name, method.__get__(self))

    def appear(self, rule):
        if rule == self.I_AB_CLOSE_RED:
            return self.frame.get('detail', False)
        if rule in (self.I_FILTER, self.I_FIRE, self.I_AB_FILTER_OPENED):
            return self.frame.get(rule, False)
        return super().appear(rule)

    def appear_then_click(self, rule, interval=None):
        return self.appear(rule) and self.click(rule, interval=interval)

    def ui_click_until_disappear(self, rule, **kwargs):
        self.closed.append(rule)


class DetailRecoveryTests(unittest.TestCase):
    def test_normal_filter_opening_still_confirms_filter(self):
        h = Harness([ENTRY, FILTER])
        self.assertTrue(h.open_filter(allow_detail=True))
        self.assertEqual([click[0] for click in h.clicks], ['filter_entry'])
        self.assertEqual(h.closed, [])

    def test_delayed_detail_after_three_clicks_continues_same_boss(self):
        h = Harness([FILTER] * 3 + [{}] + [DETAIL] * 2)
        seen = set()
        self.assertTrue(h.boss_fight('boss', seen_names=seen))
        self.assertEqual([click[0] for click in h.clicks], ['boss'] * 3)
        self.assertEqual(h.battles, 1)
        self.assertEqual(h.closed, ['close'])  # Only close after handling the boss.
        self.assertEqual(seen, {'offline-boss'})
        self.assertEqual(h.evidence_records, [('offline-boss', 'battle_success', None, None)])

    def test_real_entry_failure_on_filter_list_does_not_start_battle(self):
        h = Harness([FILTER])
        self.assertFalse(h.boss_fight('boss', seen_names=set()))
        self.assertEqual([click[0] for click in h.clicks], ['boss'] * 3)
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.evidence_records, [])

    def test_detail_visible_before_filter_request_is_reused(self):
        h = Harness([DETAIL])
        self.assertTrue(h.boss_fight('boss'))
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.battles, 1)

    def test_recovered_detail_preserves_duplicate_boss_check(self):
        h = Harness([FILTER] * 3 + [{}] + [DETAIL] * 2)
        self.assertIsNone(h.boss_fight('boss', seen_names={'offline-boss'}))
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.evidence_records, [])
        self.assertEqual(h.closed, ['close'])

    def test_list_callers_receive_filter_instead_of_detail(self):
        h = Harness([DETAIL, DETAIL, ENTRY, FILTER])
        self.assertTrue(h.open_filter())
        self.assertEqual(h.closed, ['close'])
        self.assertEqual([click[0] for click in h.clicks], ['filter_entry'])

    def test_missing_filter_alone_does_not_mean_detail(self):
        for frame in ({}, {'detail': True}, {'fire': True}):
            with self.subTest(frame=frame):
                h = Harness([frame, ENTRY, FILTER])
                self.assertTrue(h.open_filter(allow_detail=True))
                self.assertEqual(h.closed, [])
                self.assertEqual(h.battles, 0)

    def test_transient_detail_must_be_reconfirmed(self):
        h = Harness([DETAIL, ENTRY, ENTRY, FILTER])
        self.assertTrue(h.open_filter(allow_detail=True))
        self.assertEqual(h.closed, [])
        self.assertEqual(h.battles, 0)


if __name__ == '__main__':
    unittest.main()
