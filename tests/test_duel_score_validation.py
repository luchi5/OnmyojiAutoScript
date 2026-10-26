"""Replay score counters against Duel methods without loading accounts/devices."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class TaskEnd(Exception):
    pass


SOURCE = Path(__file__).resolve().parents[1] / 'tasks/Duel/script_task.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
METHODS = [n for n in CLASS.body if isinstance(n, ast.FunctionDef) and n.name in {
    'get_and_update_cur_score', 'can_start_duel', 'check_honor'}]
NAMESPACE = {'datetime': datetime, 'timedelta': timedelta, 'sleep': Mock(),
             'logger': Mock(), 'TaskEnd': TaskEnd}
exec(compile(ast.fix_missing_locations(ast.Module(body=METHODS, type_ignores=[])),
             str(SOURCE), 'exec'), NAMESPACE)


class ScoreTests(unittest.TestCase):
    def harness(self, readings):
        h = SimpleNamespace(current_score=3800, is_celeb=False,
                            I_D_CELEB_STAR='star', I_D_CELEB_HONOR='celeb',
                            maybe_screenshot=Mock(), screenshot=Mock(),
                            appear=Mock(return_value=False), device=SimpleNamespace(image='frame'),
                            O_D_SCORE=SimpleNamespace(ocr=Mock(side_effect=readings)))
        return h, lambda: NAMESPACE['get_and_update_cur_score'](h)

    def test_actual_bad_counter_is_unknown_without_reusing_initial_score(self):
        h, read = self.harness([(8050, -5850, 2200)] * 3)
        self.assertIsNone(read())
        self.assertEqual(h.current_score, 3800)
        self.assertEqual(h.screenshot.call_count, 3)

    def test_valid_counter_needs_two_matching_fresh_frames(self):
        h, read = self.harness([(2050, 150, 2200)] * 2)
        self.assertEqual(read(), 2050)
        self.assertEqual(h.current_score, 2050)
        self.assertEqual(h.screenshot.call_count, 2)

    def test_invalid_then_two_valid_frames_recovers(self):
        _, read = self.harness([(8050, -5850, 2200), (2050, 150, 2200), (2050, 150, 2200)])
        self.assertEqual(read(), 2050)

    def test_changing_counters_remain_unknown(self):
        _, read = self.harness([(2050, 150, 2200), (2150, 50, 2200), (2050, 150, 2200)])
        self.assertIsNone(read())

    def test_empty_negative_or_inconsistent_counters_are_rejected(self):
        for counter in ((0, 0, 0), (-50, 2250, 2200), (2050, 0, 2200), (10050, -7850, 2200)):
            with self.subTest(counter=counter):
                _, read = self.harness([counter] * 3)
                self.assertIsNone(read())

    def test_invalid_frame_breaks_the_confirmation_sequence(self):
        _, read = self.harness([(2050, 150, 2200), (0, 0, 0), (2050, 150, 2200)])
        self.assertIsNone(read())

    def test_legitimate_zero_score_is_accepted(self):
        _, read = self.harness([(0, 1000, 1000)] * 2)
        self.assertEqual(read(), 0)

    def can_start_harness(self, score):
        return SimpleNamespace(start_time=datetime.now(), limit_time=timedelta(minutes=30),
                               get_and_update_cur_score=Mock(return_value=score),
                               set_next_run=Mock(), is_celeb=False,
                               conf=SimpleNamespace(duel_config=SimpleNamespace(target_score=3000,
                                                        honor_full_exit=True),
                                                    duel_celeb_config=SimpleNamespace(celeb_battle=False)),
                               I_BATTLE_WITH_TRAIN='train', I_BATTLE_WITH_TRAIN2='train2',
                               appear=Mock(return_value=False), check_honor=Mock(return_value=False))

    def test_unknown_score_defers_without_matching_or_finishing_the_week(self):
        h = self.can_start_harness(None)
        before = datetime.now()
        with self.assertRaises(TaskEnd):
            NAMESPACE['can_start_duel'](h)
        kwargs = h.set_next_run.call_args.kwargs
        self.assertEqual(kwargs['task'], 'Duel')
        self.assertFalse(kwargs['server'])
        self.assertGreaterEqual(kwargs['target'], before + timedelta(minutes=3))
        self.assertNotIn('finish', kwargs)

    def test_valid_score_below_target_can_match(self):
        h = self.can_start_harness(2050)
        self.assertTrue(NAMESPACE['can_start_duel'](h))
        h.set_next_run.assert_not_called()

    def test_verified_target_score_stops_matching(self):
        h = self.can_start_harness(3000)
        self.assertFalse(NAMESPACE['can_start_duel'](h))

    def honor_harness(self, readings, icon=True):
        return SimpleNamespace(appear=Mock(return_value=icon), screenshot=Mock(),
                               I_DUEL_HONOR=SimpleNamespace(roi_front=[190, 635, 20, 20]),
                               O_D_HONOR=SimpleNamespace(roi=None, ocr=Mock(side_effect=readings)),
                               device=SimpleNamespace(image='frame'))

    def test_honor_counters_confirm_full_and_not_full(self):
        for reading, expected in (((2600, 0, 2600), True), ((870, 1130, 2000), False),
                                  ((0, 2000, 2000), False)):
            with self.subTest(reading=reading):
                h = self.honor_harness([reading] * 2)
                self.assertEqual(NAMESPACE['check_honor'](h), expected)
                self.assertEqual(h.screenshot.call_count, 2)
                self.assertEqual(h.O_D_HONOR.roi, [210, 635, 110, 20])

    def test_invalid_honor_readings_are_unknown(self):
        for reading in ((0, 0, 0), (-1, 2001, 2000), (8050, -6050, 2000), (870, 0, 2000)):
            with self.subTest(reading=reading):
                h = self.honor_harness([reading] * 3)
                self.assertIsNone(NAMESPACE['check_honor'](h))

    def test_missing_honor_icon_is_unknown_and_never_reads_another_currency(self):
        h = self.honor_harness([], icon=False)
        self.assertIsNone(NAMESPACE['check_honor'](h))
        h.O_D_HONOR.ocr.assert_not_called()

    def test_single_false_full_reading_does_not_stop(self):
        h = self.honor_harness([(2000, 0, 2000), (870, 1130, 2000), (870, 1130, 2000)])
        self.assertFalse(NAMESPACE['check_honor'](h))

    def test_invalid_reading_breaks_honor_confirmation(self):
        h = self.honor_harness([(2000, 0, 2000), (0, 0, 0), (2000, 0, 2000)])
        self.assertIsNone(NAMESPACE['check_honor'](h))

    def test_unknown_honor_defers_without_matching_or_finishing_the_week(self):
        h = self.can_start_harness(2050)
        h.check_honor.return_value = None
        with self.assertRaisesRegex(TaskEnd, 'honor not confirmed'):
            NAMESPACE['can_start_duel'](h)
        kwargs = h.set_next_run.call_args.kwargs
        self.assertFalse(kwargs['server'])
        self.assertNotIn('finish', kwargs)
        h.get_and_update_cur_score.assert_not_called()

    def test_confirmed_full_honor_stops_even_if_score_cannot_be_read(self):
        h = self.can_start_harness(None)
        h.check_honor.return_value = True
        self.assertFalse(NAMESPACE['can_start_duel'](h))
        h.get_and_update_cur_score.assert_not_called()
        h.set_next_run.assert_not_called()

    def test_disabled_honor_switch_does_not_require_honor_reading(self):
        h = self.can_start_harness(2050)
        h.conf.duel_config.honor_full_exit = False
        self.assertTrue(NAMESPACE['can_start_duel'](h))
        h.check_honor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
