"""Offline pagination and visual-evidence tests; no backend/OCR/device imports."""
from copy import copy, deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

import cv2
import numpy as np

from dev_tools.test_bondling_battle_entry import extract_methods, FakeLogger
from dev_tools.test_area_boss_normal_level import Harness as DifficultyHarness, AreaBossFloor, TaskEnd
from dev_tools.test_area_boss_floor_selection import FloorHarness


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/AreaBoss/script_task.py'
METHODS = extract_methods(SOURCE, 'ScriptTask', (
    '_visible_challenge_buttons', '_select_challenge_button', '_boss_list_frame',
    '_same_boss_list_frame', '_fight_filter_bosses', '_selected_star_visual_match'),
    dict(np=np, cv2=cv2, copy=copy, RuleImage=object, logger=FakeLogger(),
         time=SimpleNamespace(sleep=lambda seconds: None)))
RULE_METHODS = extract_methods(ROOT / 'module/atom/image.py', 'RuleImage',
    ('corp', 'match', 'match_all_any', 'match_saturation'),
    dict(np=np, cv2=cv2, logger=FakeLogger()))
RUN = extract_methods(SOURCE, 'ScriptTask', ('run',),
    dict(logger=FakeLogger(), TaskEnd=TaskEnd, page_area_boss='area',
         page_shikigami_records='records'))['run']


class PixelRule:
    is_template_match = True
    debug_mode = False
    threshold = .8

    def __init__(self, pixels, roi):
        self.image = pixels
        self.roi_back = tuple(roi)
        self.roi_front = list(roi)
        self.name = 'offline-button'


for method_name, method in RULE_METHODS.items():
    setattr(PixelRule, method_name, method)


def read_template(name):
    return cv2.cvtColor(cv2.imread(str(ROOT / 'tasks/AreaBoss/res' / name)), cv2.COLOR_BGR2RGB)


class PixelHarness:
    def __init__(self):
        self.device = SimpleNamespace(image=np.full((720, 1280, 3), 90, np.uint8))
        for index, y in enumerate((235, 390, 545), 1):
            image = read_template(f'res_battle_{index}.png')
            h, w = image.shape[:2]
            setattr(self, f'I_BATTLE_{index}', PixelRule(image, (1083, y, w, h)))

    def appear(self, rule):
        return rule.match(self.device.image)

    def place(self, index, y, enabled=True):
        rule = getattr(self, f'I_BATTLE_{index}')
        image = rule.image
        if not enabled:
            image = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
        h, w = image.shape[:2]
        self.device.image[y:y + h, 1083:1083 + w] = image


for method_name in ('_visible_challenge_buttons', '_select_challenge_button',
                    '_boss_list_frame', '_selected_star_visual_match'):
    setattr(PixelHarness, method_name, METHODS[method_name])


class ListHarness:
    I_AB_FILTER_OPENED = 'filter'
    S_AB_FILTER_UP = 'scroll-up'

    def __init__(self, pages, *, fail=(), repeat=False, reset_after_battle=False):
        self.pages, self.page = pages, 0
        self.fail, self.repeat = set(fail), repeat
        self.reset_after_battle = reset_after_battle
        self.opened = True
        self.swipes, self.battles, self.attempts = [], [], []
        self._area_boss_seen_names = set()

    def screenshot(self):
        pass

    def appear(self, rule):
        return self.opened

    def open_filter(self):
        self.opened = True

    def _boss_list_frame(self):
        return np.full((20, 20), self.page * 20, np.uint8)

    def _visible_challenge_buttons(self):
        return [SimpleNamespace(name=name, roi_front=[1083, 210 + index * 155, 65, 54])
                for index, name in enumerate(self.pages[self.page]) if name is not None]

    def _select_challenge_button(self, battle):
        return battle

    def boss_fight(self, target, seen_names=None):
        name = target.name
        self.attempts.append(name)
        self.opened = False
        if self.reset_after_battle:
            self.page = 0
        if name in seen_names:
            return None
        seen_names.add(name)
        self.battles.append(name)
        return name not in self.fail

    def swipe(self, rule, interval):
        self.swipes.append((rule, interval))
        self.page = ((self.page + 1) % len(self.pages) if self.repeat
                     else min(self.page + 1, len(self.pages) - 1))

    _fight_filter_bosses = METHODS['_fight_filter_bosses']
    _same_boss_list_frame = staticmethod(METHODS['_same_boss_list_frame'].__func__)


class PopularListTests(unittest.TestCase):
    def test_first_three_black_next_page_available(self):
        h = ListHarness([[None] * 3, ['next-boss', None, None]])
        self.assertEqual(h._fight_filter_bosses(1, scroll=True), [True])
        self.assertEqual(h.battles, ['next-boss'])
        self.assertEqual(len(h.swipes), 1)

    def test_remaining_one_two_three_respects_daily_quota(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                h = ListHarness([[None] * 3, ['first', 'second', 'third']])
                self.assertEqual(h._fight_filter_bosses(count, scroll=True), [True] * count)
                self.assertEqual(len(h.battles), count)
                self.assertEqual(len(h.swipes), 1)

    def test_all_black_stops_on_repeated_bottom(self):
        h = ListHarness([[None] * 3, [None] * 3])
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [])
        self.assertEqual(len(h.swipes), 2)
        self.assertEqual(h.battles, [])

    def test_looping_pages_stop_without_unbounded_swipes(self):
        h = ListHarness([[None] * 3, [None] * 3], repeat=True)
        self.assertEqual(h._fight_filter_bosses(2, scroll=True), [])
        self.assertEqual(len(h.swipes), 2)

    def test_many_distinct_unavailable_pages_have_hard_limit(self):
        h = ListHarness([[None] * 3 for _ in range(30)])
        self.assertEqual(h._fight_filter_bosses(1, scroll=True), [])
        self.assertEqual(len(h.swipes), 13)

    def test_reopen_reset_to_first_page_still_reaches_three_distinct_pages(self):
        h = ListHarness([['first'], ['second'], ['third']], reset_after_battle=True)
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(h.attempts, ['first', 'second', 'third'])
        self.assertLessEqual(len(h.swipes), 4)

    def test_reset_midpage_discards_stale_coordinates_and_resumes_remaining_rows(self):
        h = ListHarness([[None] * 3, ['first', 'second', 'third']], reset_after_battle=True)
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(len(h.swipes), 3)

    def test_reopening_duplicate_on_overlapping_page_does_not_hide_later_boss(self):
        h = ListHarness([['first'], ['second'], ['second', 'third']], reset_after_battle=True)
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(h.attempts, ['first', 'second', 'second', 'third'])

    def test_overlapping_pages_and_same_names_count_once(self):
        h = ListHarness([['first', None, None], ['first', 'second', 'second'],
                         ['second', 'third', None]])
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [True, True, True])
        self.assertEqual(h.battles, ['first', 'second', 'third'])

    def test_reward_boss_is_not_counted_twice(self):
        h = ListHarness([['reward', 'second', None]])
        h._area_boss_seen_names.add('reward')
        self.assertEqual(h._fight_filter_bosses(1, scroll=True), [True])
        self.assertEqual(h.battles, ['second'])

    def test_no_remaining_quota_does_not_open_or_scroll(self):
        h = ListHarness([['first']])
        self.assertEqual(h._fight_filter_bosses(0, scroll=True), [])
        self.assertEqual(h.attempts, [])
        self.assertEqual(h.swipes, [])

    def test_failure_preserves_existing_attempt_budget(self):
        h = ListHarness([['first', 'second', 'third']], fail=('first',))
        self.assertEqual(h._fight_filter_bosses(2, scroll=True), [False, True])
        self.assertEqual(h.battles, ['first', 'second'])

    def test_collection_keeps_single_page_policy(self):
        h = ListHarness([[None] * 3, ['first']])
        self.assertEqual(h._fight_filter_bosses(1, scroll=False), [])
        self.assertEqual(h.swipes, [])

    def test_favorites_refresh_changed_rank_badge_and_finish_remaining_red_rows(self):
        h = ChangingListHarness([['first', 'second', 'third']])
        self.assertEqual(h._fight_filter_bosses(3, scroll=False), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(h.swipes, [])

    def test_favorites_refresh_shifted_rows_when_completed_button_disappears(self):
        h = ChangingListHarness([['first', 'second', 'third']], remove_completed=True)
        self.assertEqual(h._fight_filter_bosses(3, scroll=False), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(h.attempts, ['first', 'second', 'third'])
        self.assertEqual(h.swipes, [])

    def test_popular_refreshes_changed_badge_before_scrolling_past_remaining_rows(self):
        h = ChangingListHarness([['first', 'second', 'third'], ['unneeded']])
        self.assertEqual(h._fight_filter_bosses(3, scroll=True), [True] * 3)
        self.assertEqual(h.battles, ['first', 'second', 'third'])
        self.assertEqual(h.swipes, [])

    def test_favorites_unstable_frames_have_bounded_refresh_without_scrolling(self):
        h = ChangingListHarness([['first', 'second', 'third']])
        frames = []

        def unstable_frame():
            frames.append(len(frames))
            return np.full((20, 20), len(frames) * 20, np.int16)

        h._boss_list_frame = unstable_frame
        self.assertEqual(h._fight_filter_bosses(3, scroll=False), [])
        self.assertEqual(len(frames), 26)
        self.assertEqual(h.attempts, [])
        self.assertEqual(h.swipes, [])

    def test_stale_enabled_target_is_rechecked(self):
        h = ListHarness([['first', 'second']])
        h._select_challenge_button = lambda target: None if target.name == 'first' else target
        self.assertEqual(h._fight_filter_bosses(1, scroll=True), [True])
        self.assertEqual(h.battles, ['second'])

    def test_detail_name_dedupe_prevents_battle_and_count(self):
        h = DifficultyHarness([{}], floor=AreaBossFloor.DEFAULT)
        self.assertIsNone(h.boss_fight(object(), seen_names={'offline-boss'}))
        self.assertEqual(h.battles, 0)
        self.assertEqual(h.evidence_records, [])

    def test_detail_name_is_added_before_attempt(self):
        h = DifficultyHarness([{}], floor=AreaBossFloor.DEFAULT)
        names = set()
        self.assertTrue(h.boss_fight(object(), seen_names=names))
        self.assertEqual(names, {'offline-boss'})
        self.assertIsNone(h.boss_fight(object(), seen_names=names))
        self.assertEqual(h.battles, 1)

    def test_unreadable_name_without_pixels_is_skipped(self):
        h = DifficultyHarness([{}], floor=AreaBossFloor.DEFAULT)
        h.O_AB_BOSS_NAME.detect_text = lambda image: ''
        self.assertIsNone(h.boss_fight(object(), seen_names=set()))
        self.assertEqual(h.battles, 0)


class ChangingListHarness(ListHarness):
    def __init__(self, pages, *, remove_completed=False):
        super().__init__(pages)
        self.revision, self.remove_completed = 0, remove_completed

    def _boss_list_frame(self):
        return np.full((20, 20), self.page * 80 + self.revision * 20, np.uint8)

    def boss_fight(self, target, seen_names=None):
        result = super().boss_fight(target, seen_names=seen_names)
        if result is not None:
            self.revision += 1
            if self.remove_completed:
                self.pages[self.page].remove(target.name)
        return result


class RunHarness(ListHarness):
    run = RUN

    def __init__(self, pages, *, quota=3, reward=False, collect=False):
        super().__init__(pages)
        self.quota, self.reward, self.schedule, self.returned = quota, reward, [], False
        self.config = SimpleNamespace(area_boss=SimpleNamespace(
            general_battle=SimpleNamespace(lock_team_enable=True),
            boss=SimpleNamespace(boss_reward=reward, use_collect=collect),
            switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False)))

    def goto_page(self, page):
        pass

    def fight_reward_boss(self):
        self._area_boss_seen_names.add('reward')
        return self.reward

    def switch_to_collect(self):
        pass

    def switch_to_famous(self):
        pass

    def _get_dynamic_boss_count(self):
        return self.quota

    def go_back(self):
        self.returned = True

    def set_next_run(self, **kwargs):
        self.schedule.append(kwargs)


class RunIntegrationTests(unittest.TestCase):
    def test_reward_is_subtracted_and_shared_identity_is_skipped(self):
        h = RunHarness([['reward', 'second', 'third']], reward=True)
        with self.assertRaises(TaskEnd):
            h.run()
        self.assertEqual(h.battles, ['second', 'third'])
        self.assertEqual(h.schedule, [{'task': 'AreaBoss', 'success': True, 'finish': False}])

    def test_unavailable_list_keeps_existing_normal_schedule(self):
        h = RunHarness([[None] * 3])
        with self.assertRaises(TaskEnd):
            h.run()
        self.assertEqual(h.battles, [])
        self.assertTrue(h.returned)
        self.assertEqual(h.schedule, [{'task': 'AreaBoss', 'success': True, 'finish': False}])

    def test_reward_fills_one_boss_quota_without_extra_battle(self):
        h = RunHarness([['reward', 'second', 'third']], quota=1, reward=True)
        with self.assertRaises(TaskEnd):
            h.run()
        self.assertEqual(h.battles, [])
        self.assertEqual(h.swipes, [])

    def test_different_daily_quotas_do_not_change_configuration(self):
        for count in (1, 2, 3):
            h = RunHarness([[None] * 3, ['first', 'second', 'third']], quota=count)
            before = deepcopy(h.config.area_boss.boss)
            with self.assertRaises(TaskEnd):
                h.run()
            self.assertEqual(len(h.battles), count)
            self.assertEqual(h.config.area_boss.boss, before)


class VisualEvidenceTests(unittest.TestCase):
    def test_scrolled_buttons_use_real_positions_and_colour_gate(self):
        h = PixelHarness()
        h.place(1, 217, enabled=False)
        h.place(2, 384, enabled=False)
        h.place(3, 604)
        before = deepcopy([getattr(h, f'I_BATTLE_{i}').roi_front for i in (1, 2, 3)])
        targets = h._visible_challenge_buttons()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].roi_front[1], 604)
        self.assertIsNotNone(h._select_challenge_button(targets[0]))
        self.assertEqual(before, [getattr(h, f'I_BATTLE_{i}').roi_front for i in (1, 2, 3)])

    def test_overlapping_template_matches_are_deduplicated(self):
        h = PixelHarness()
        for i, y in enumerate((216, 379, 602), 1):
            h.place(i, y)
        self.assertEqual([target.roi_front[1] for target in h._visible_challenge_buttons()],
                         [216, 379, 602])

    def test_all_black_is_never_enabled(self):
        h = PixelHarness()
        for i, y in enumerate((216, 379, 602), 1):
            h.place(i, y, enabled=False)
        self.assertEqual(h._visible_challenge_buttons(), [])

    def test_changed_contribution_frame_is_rechecked(self):
        h = PixelHarness()
        h.place(1, 216)
        target = h._visible_challenge_buttons()[0]
        h.place(1, 216, enabled=False)
        self.assertIsNone(h._select_challenge_button(target))

    def test_map_animation_does_not_hide_list_bottom(self):
        h = PixelHarness()
        first = h._boss_list_frame()
        h.device.image[:, :900] = 255
        second = h._boss_list_frame()
        self.assertTrue(ListHarness._same_boss_list_frame(first, second))
        h.place(1, 216)
        self.assertFalse(ListHarness._same_boss_list_frame(first, h._boss_list_frame()))

    def test_star_lettering_accepts_changed_background_without_ocr(self):
        for name in ('one', 'ten'):
            with self.subTest(name=name):
                h = PixelHarness()
                template = read_template(f'ab_ji_floor_{name}.png')
                pixels = template.copy()
                pixels[np.min(pixels, axis=2) <= 170] = (130, 70, 65)
                h.device.image[120:145, 385:432] = pixels
                self.assertTrue(h._selected_star_visual_match(SimpleNamespace(image=template)))

    def test_star_numeral_mismatch_is_not_hidden_by_shared_star_character(self):
        h = PixelHarness()
        h.device.image[120:145, 385:432] = read_template('ab_ji_floor_ten.png')
        self.assertFalse(h._selected_star_visual_match(SimpleNamespace(image=read_template('ab_ji_floor_one.png'))))

    def test_saved_real_menu_and_header_font_match_without_matching_other_numerals(self):
        fixture = json.loads((ROOT / 'dev_tools/fixtures/area_boss_star_masks.json').read_text())

        def pixels(item):
            mask = np.array([[value == '1' for value in row] for row in item['rows']])
            return np.repeat(np.where(mask, 255, 90).astype(np.uint8)[:, :, None], 3, axis=2)

        h = PixelHarness()
        h.device.image[116:152, 378:445] = pixels(fixture['header'])
        self.assertTrue(h._selected_star_visual_match(SimpleNamespace(image=pixels(fixture['menu']))))
        for name in ('one', 'ten'):
            self.assertFalse(h._selected_star_visual_match(SimpleNamespace(image=read_template(f'ab_ji_floor_{name}.png'))))

    def test_blank_and_normal_level_are_not_extreme_star_evidence(self):
        h = PixelHarness()
        target = SimpleNamespace(image=read_template('ab_ji_floor_one.png'))
        self.assertFalse(h._selected_star_visual_match(target))
        cv2.putText(h.device.image, '1', (390, 141), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        self.assertFalse(h._selected_star_visual_match(target))

    def test_existing_ten_needs_no_selection(self):
        h = FloorHarness(target='ten', current='ten')
        self.assertTrue(h.run())
        self.assertEqual(h.clicks, [])
        self.assertEqual(h.swipes, [])


if __name__ == '__main__':
    unittest.main()
