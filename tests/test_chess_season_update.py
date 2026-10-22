"""Exercise the xy season port without ADB, OCR services or game input."""

from copy import copy
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.atom.ocr import RuleOcr
from module.device.device import Device
from module.exception import GameStuckError, GameTooManyClickError
from tasks.Chess.assets import ChessAssets
from tasks.Chess.config import ChessConfig
from tasks.Chess.runtime.frame_cards import ROOT, read_image
from tasks.Chess.runtime.result_navigation import ChessResultNavigationMixin
from tasks.base_task import BaseTask
from tests import test_chess_port as port


class ChessSeasonUpdateTests(unittest.TestCase):
    def make_task(self):
        task = port.ChessPortTests().make_task()
        task.device.click = Mock(side_effect=AssertionError('Unexpected game input'))
        return task

    @staticmethod
    def paste(image, front, x, y):
        image[y:y + front.shape[0], x:x + front.shape[1]] = front

    def fixture(self, kind, name, star=1, shift=0):
        frame = {'card': ChessAssets.F_HAND_CARD, 'store': ChessAssets.F_STORE_CARD,
                 'emblem': ChessAssets.F_FETTER_BADGE}[kind]
        index = 3 - star if kind == 'card' else 0
        badge = read_image(Path(frame.files[index]))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        bx, by, _, _ = frame.badge_references[index]
        self.paste(image, badge, bx + shift, by)
        task = self.make_task()
        entry = (task.frame_emblem_library['entries'][name] if kind == 'emblem'
                 else task.frame_card_library['cards'][kind][name])
        portrait = read_image(ROOT / 'fetter/portraits' / entry['file'] if kind == 'emblem'
                              else ROOT / entry['portrait'])
        rx, ry, _, _ = frame.roi_ranges[-1 if kind == 'emblem' else -2]
        self.paste(image, portrait, rx + shift, ry)
        task.device.image = image
        return task

    def test_removed_lineups_migrate_and_supported_choices_stay_selected(self):
        for value in ('荒川', 'arakawa', '流火', '冥府', '大江山', '狐妖'):
            self.assertEqual(ChessConfig(lineup_bond=value).lineup_bond.value, '七角山')
        for value in ('七角山', '海国', '冥火', '平安京'):
            self.assertEqual(ChessConfig(lineup_bond=value).lineup_bond.value, value)

    def test_frame_recognizes_hand_stars_and_store_portraits_without_ocr(self):
        for kind, name, star, shift in (
            ('card', 'hakurou', 1, -65), ('card', 'omoikane', 2, 0),
            ('card', 'taira_no_masakado', 3, 100), ('store', 'kaoru', 1, -185),
        ):
            with self.subTest(kind=kind, name=name, star=star):
                task = self.fixture(kind, name, star, shift)
                with patch.object(RuleOcr, 'ocr', side_effect=AssertionError('Unexpected OCR')):
                    cards = task._frame_card_scan(kind)
                identified = [card for card in cards if card['name'] == name]
                self.assertEqual(len(identified), 1)
                self.assertGreater(identified[0]['score'], .99)
                if kind == 'card':
                    self.assertEqual(identified[0]['star'], star)
                task.device.click.assert_not_called()

    def test_emblem_uses_grayscale_badge_and_rebased_ranges(self):
        task = self.make_task()
        name = next(iter(task.frame_emblem_library['entries']))
        task = self.fixture('emblem', name, shift=90)
        with patch.object(RuleOcr, 'ocr', side_effect=AssertionError('Unexpected OCR')):
            cards = task._frame_card_scan('emblem')
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['name'], task.frame_emblem_library['entries'][name]['name'])
        self.assertEqual(cards[0]['type'], 'emblem')

    def test_frame_cache_invalidates_on_new_image_without_xy_frame_id(self):
        task = self.fixture('card', 'hakurou')
        self.assertTrue(task._frame_card_scan('card'))
        task.device.image = np.zeros_like(task.device.image)
        self.assertEqual(task._frame_card_scan('card'), [])

    def test_frame_reads_do_not_click_but_explicit_click_does(self):
        task = self.fixture('card', 'hakurou')
        frame = copy(ChessAssets.F_HAND_CARD)
        frame.defer_ocr = True
        task.click = Mock()
        self.assertTrue(task.runframe(frame))
        task.click.assert_not_called()
        self.assertTrue(task.runframe(frame, None, None, None, None, True))
        task.click.assert_called_once()

    def field_task(self, visible=True):
        task = SimpleNamespace(CHESS_EXIT_TIMEOUT=60, CHESS_EXIT_SCREENSHOT_INTERVAL=.35)
        task.device = SimpleNamespace(click_record_remove=Mock(), stuck_record_clear=Mock())
        task.appear = Mock(return_value=visible)
        task.click = Mock()
        task.screenshot = Mock()
        return task

    def test_six_realms_selects_one_field_and_stops_when_page_disappears(self):
        task = self.field_task()
        task.click.side_effect = lambda _: setattr(task.appear, 'return_value', False)
        module = 'tasks.Chess.runtime.result_navigation'
        with patch(module + '.random.choice', return_value=ChessAssets.C_SIXREALMS_2), \
                patch(module + '.random.randint', return_value=2), patch(module + '.time.sleep'):
            self.assertTrue(ChessResultNavigationMixin.select_chess_sixrealms_if_visible(task))
        task.click.assert_called_once_with(ChessAssets.C_SIXREALMS_2)

    def test_six_realms_timeout_never_exceeds_two_clicks(self):
        task = self.field_task()
        module = 'tasks.Chess.runtime.result_navigation'
        with patch(module + '.random.randint', return_value=2), patch(module + '.time.sleep'), \
                patch(module + '.time.monotonic', side_effect=[0, 0, 1, 2, 61]):
            with self.assertRaises(GameStuckError):
                ChessResultNavigationMixin.select_chess_sixrealms_if_visible(task)
        self.assertEqual(task.click.call_count, 2)
        self.assertIs(task.click.call_args_list[0].args[0], task.click.call_args_list[1].args[0])

    def test_six_realms_does_not_click_other_pages(self):
        task = self.field_task(False)
        self.assertFalse(ChessResultNavigationMixin.select_chess_sixrealms_if_visible(task))
        task.click.assert_not_called()

    def test_red_cross_is_handled_by_chess_burst(self):
        task = self.make_task()
        task.appear_then_click = Mock(return_value=True)
        with patch.object(BaseTask, '_burst', return_value=False):
            self.assertTrue(task._burst())
        task.appear_then_click.assert_called_once_with(ChessAssets.I_CLOSE_RED, interval=.8)

    def purchase_task(self, frames):
        task = self.make_task()
        task.screenshot = Mock()
        task.click = Mock(return_value=True)
        task.appear = Mock(return_value=False)
        task._recognize_shop_slot = Mock(side_effect=frames)
        task._is_purchase_allowed = Mock(return_value=True)
        task._can_afford_shop_shikigami = Mock(return_value=True)
        task._hand_card_detections = Mock(return_value=[])
        task._free_one_hand_slot_for_purchase = Mock(
            side_effect=AssertionError('Unexpected hand sale'))
        return task

    def test_buy_white_wolf_uses_local_adapter_and_confirms_two_missing_frames(self):
        card = {'source': 'frame', 'score': .996}
        task = self.purchase_task([card, card, None, None])
        self.assertTrue(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        task.click.assert_called_once_with(ChessAssets.C_SHIKIGAMI_3, interval=.3)
        self.assertEqual(task.screenshot.call_count, 3)

    def test_disappearance_before_click_is_not_counted_as_purchase(self):
        task = self.purchase_task([{'source': 'frame', 'score': .996}, None, None, None])
        self.assertFalse(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        task.click.assert_not_called()

    def test_purchase_stops_before_click_when_preparation_ends(self):
        card = {'source': 'frame', 'score': .996}
        task = self.purchase_task([card])
        task._is_purchase_allowed.side_effect = [True, False, False]
        self.assertFalse(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        task.click.assert_not_called()

    def test_one_missing_frame_does_not_confirm_purchase(self):
        card = {'source': 'frame', 'score': .996}
        task = self.purchase_task([card, card, None, card, None, None])
        self.assertTrue(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        self.assertEqual(task.click.call_count, 2)
        self.assertEqual(task.screenshot.call_count, 5)

    def test_purchase_click_loop_times_out_without_success(self):
        task = self.purchase_task([])
        with patch('time.monotonic', side_effect=[0, 0, 1, 3]):
            result = task._chess_click_until_disappear(
                ChessAssets.C_SHIKIGAMI_3, click_target=ChessAssets.C_SHIKIGAMI_3,
                visible=lambda: True, guard=lambda: True, timeout=2, confirm_frames=2)
        self.assertFalse(result)
        self.assertEqual(task.click.call_count, 2)

    def test_full_hand_with_no_cleanup_progress_uses_existing_lineup_sale_before_buying(self):
        card = {'source': 'frame', 'score': .98}
        task = self.purchase_task([card, card, card, card, None, None])
        task._hand_card_detections.side_effect = [[{}] * 10, [{}] * 10, [{}] * 9]
        task._free_one_hand_slot_for_purchase = Mock(side_effect=[
            {'equipped': [], 'sold_non_lineup': []}, {'type': 'lineup'}])
        self.assertTrue(task._buy_shop_slot(4, ChessAssets.C_SHIKIGAMI_4, 'kaoru'))
        self.assertEqual([call.kwargs for call in task._free_one_hand_slot_for_purchase.call_args_list],
                         [{'sell_lineup': False}, {'sell_lineup': True}])
        task.click.assert_called_once_with(ChessAssets.C_SHIKIGAMI_4, interval=.3)

    def test_still_full_after_both_recovery_steps_never_clicks_purchase(self):
        card = {'source': 'frame', 'score': .98}
        task = self.purchase_task([card, card, card])
        task._hand_card_detections.return_value = [{}] * 10
        task._free_one_hand_slot_for_purchase = Mock(return_value={})
        self.assertFalse(task._buy_shop_slot(4, ChessAssets.C_SHIKIGAMI_4, 'kaoru'))
        self.assertEqual(task._free_one_hand_slot_for_purchase.call_count, 2)
        task.click.assert_not_called()

    def test_full_hand_recovery_failure_does_not_retry_purchase(self):
        card = {'source': 'frame', 'score': .98}
        task = self.purchase_task([card])
        task._hand_card_detections.return_value = [{}] * 10
        task._free_one_hand_slot_for_purchase = Mock(return_value=None)
        self.assertFalse(task._buy_shop_slot(4, ChessAssets.C_SHIKIGAMI_4, 'kaoru'))
        task.click.assert_not_called()

    def test_target_disappearing_during_hand_cleanup_is_not_a_purchase(self):
        task = self.purchase_task([{'source': 'frame', 'score': .98}, None])
        task._hand_card_detections.return_value = [{}] * 10
        task._free_one_hand_slot_for_purchase = Mock(return_value={})
        self.assertFalse(task._buy_shop_slot(4, ChessAssets.C_SHIKIGAMI_4, 'kaoru'))
        task.click.assert_not_called()

    def test_blocked_purchase_polling_stops_sending_input_after_two_clicks(self):
        task = self.purchase_task([])
        with patch('time.monotonic', side_effect=[0, 0, .1, .2, .3, 3]):
            result = task._chess_click_until_disappear(
                ChessAssets.C_SHIKIGAMI_4, click_target=ChessAssets.C_SHIKIGAMI_4,
                visible=lambda: True, guard=lambda: True, timeout=2,
                confirm_frames=2, max_clicks=2)
        self.assertFalse(result)
        self.assertEqual(task.click.call_count, 2)
        self.assertEqual(task.screenshot.call_count, 4)

    def test_saved_incident_detects_full_hand_and_blocks_purchase(self):
        import cv2
        incident = Path('log/error/1792554535855/2026-10-21_11-48-55-851197.png')
        if not incident.exists():
            self.skipTest('Local incident screenshot is not part of the public repository')
        task = self.make_task()
        task.device.image = cv2.cvtColor(
            cv2.imdecode(np.fromfile(incident, dtype=np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.assertEqual(len(task._hand_card_detections()), task.HAND_CAPACITY)
        task.screenshot = Mock()
        task.click = Mock(side_effect=AssertionError('Must not buy into full hand'))
        task._is_purchase_allowed = Mock(return_value=True)
        task._can_afford_shop_shikigami = Mock(return_value=True)
        task._free_one_hand_slot_for_purchase = Mock(return_value=None)
        self.assertFalse(task._buy_shop_slot(4, ChessAssets.C_SHIKIGAMI_4, 'kaoru'))
        task.click.assert_not_called()

    def test_saved_mixed_hand_counts_seven_shikigami_and_three_souls(self):
        import cv2
        path = Path('log/error/1792563004710/2026-10-21_14-10-04-700571.png')
        if not path.exists():
            self.skipTest('Local incident screenshot is not part of the public repository')
        task = self.make_task()
        task.device.image = cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR),
                                         cv2.COLOR_BGR2RGB)
        self.assertEqual(len(task._frame_card_scan('card')), 7)
        self.assertEqual(len(task._soul_hand_cards()), 3)
        self.assertEqual(len(task._hand_card_detections()), 10)
        task.screenshot = Mock()
        task.click = Mock(side_effect=AssertionError('Must not buy into mixed full hand'))
        task._is_purchase_allowed = Mock(return_value=True)
        task._can_afford_shop_shikigami = Mock(return_value=True)
        task._free_one_hand_slot_for_purchase = Mock(return_value=None)
        self.assertFalse(task._buy_shop_slot(5, ChessAssets.C_SHIKIGAMI_5, 'hakurou'))
        task.click.assert_not_called()

    def test_confirmed_purchase_only_removes_its_own_click_history(self):
        card = {'source': 'frame', 'score': .99}
        task = self.purchase_task([card, card, None, None])
        task.device.click_record_remove = Mock()
        self.assertTrue(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        task.device.click_record_remove.assert_called_once_with(ChessAssets.C_SHIKIGAMI_3)

    def test_blocked_purchase_does_not_clear_click_history(self):
        task = self.purchase_task([{'source': 'frame', 'score': .99}])
        task.device.click_record_remove = Mock()
        task._hand_card_detections.return_value = [{}] * 10
        task._free_one_hand_slot_for_purchase = Mock(return_value=None)
        self.assertFalse(task._buy_shop_slot(3, ChessAssets.C_SHIKIGAMI_3, 'hakurou'))
        task.device.click_record_remove.assert_not_called()

    def sale_history_task(self, confirmed):
        task = self.make_task()
        device = task.device
        device.click_record = deque(['C_USE_SOUL'], maxlen=15)
        device.click_record_clear = device.click_record.clear
        device.click_record_remove = Mock(
            side_effect=lambda name: Device.click_record_remove(device, name))

        def record_sale(_):
            Device.click_record_add(device, task.HAND_CARD_SALE_CONTROL_NAME)
            Device.click_record_check(device)

        task.sell_hand_card = Mock(side_effect=record_sale)
        task._wait_hand_card_removed = Mock(return_value=confirmed)
        return task

    def test_successful_sales_across_rounds_do_not_trip_real_click_guard(self):
        task = self.sale_history_task(True)
        for number in range(20):
            self.assertTrue(task._sell_hand_card_confirmed(
                (300 + number, 620), 1, lambda: 0))
        self.assertEqual(task.sell_hand_card.call_count, 20)
        self.assertEqual(list(task.device.click_record), ['C_USE_SOUL'])
        self.assertEqual(task.device.click_record_remove.call_count, 20)
        task.device.click_record_remove.assert_called_with(
            'CHESS_SELL_UNKNOWN_HAND_CARD')
        task.device.click.assert_not_called()

    def test_failed_sales_still_trip_real_click_guard_on_tenth_attempt(self):
        task = self.sale_history_task(False)
        for _ in range(9):
            self.assertFalse(task._sell_hand_card_confirmed(
                (300, 620), 1, lambda: 1))
        task.device.click_record_remove.assert_not_called()
        self.assertEqual(task.device.click_record.count(
            'CHESS_SELL_UNKNOWN_HAND_CARD'), 9)
        with self.assertRaises(GameTooManyClickError):
            task._sell_hand_card_confirmed((300, 620), 1, lambda: 1)
        self.assertEqual(task._wait_hand_card_removed.call_count, 9)
        task.device.click_record_remove.assert_not_called()

    def test_non_sale_hand_removal_does_not_clear_sale_history(self):
        task = self.make_task()
        task.device.click_record_remove = Mock()
        task._wait_chess_action_state = Mock(return_value=True)
        self.assertTrue(task._wait_hand_card_removed(1, lambda: 0))
        task.device.click_record_remove.assert_not_called()

    def test_soul_cache_invalidates_on_image_and_frame_id_change(self):
        task = self.make_task()
        rule = Mock()
        rule.image = np.zeros((10, 10, 3), dtype=np.uint8)
        task.__dict__['soul_hand_rules'] = [('yinmoluo', rule)]
        with patch('tasks.Chess.runtime.hand_operations.cv2.matchTemplate',
                   return_value=np.zeros((1, 1), dtype=np.float32)) as matcher:
            task._soul_hand_cards()
            calls = matcher.call_count
            self.assertGreater(calls, 0)
            task._soul_hand_cards()
            self.assertEqual(matcher.call_count, calls)
            task.device.image = task.device.image.copy()
            task._soul_hand_cards()
            self.assertGreater(matcher.call_count, calls)
            calls = matcher.call_count
            task.device.image_frame_id = 2
            task._soul_hand_cards()
            self.assertGreater(matcher.call_count, calls)


if __name__ == '__main__':
    unittest.main()
