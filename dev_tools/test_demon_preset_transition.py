"""Exercise the actual preset and battle methods without importing the game.

Only synthetic frames are used here; private error screenshots stay outside
the source tree. No device, account config, backend or notification is opened.
"""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(os.environ.get('OAS_REGRESSION_ROOT', Path(__file__).resolve().parents[1]))
GENERAL = ROOT / 'tasks/Component/GeneralBattle/general_battle.py'
DEMON = ROOT / 'tasks/DemonEncounter/script_task.py'


class Marker(str):
    def __new__(cls, name, x=0):
        value = super().__new__(cls, name)
        value.roi_back = (x, 0, 5, 5)
        return value


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Timer:
    def __init__(self, clock, limit):
        self.clock, self.limit, self.started = clock, limit, clock.now

    def start(self):
        self.started = self.clock.now
        return self

    def reached(self):
        return self.clock.now - self.started >= self.limit


def extract(path, class_name, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    source = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in source.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == set(names)
    namespace = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 str(path), 'exec'), namespace)
    return {name: namespace[name] for name in names}


def frame(*visible, **extra):
    return {'visible': set(visible), **extra}


def prepare(*extra, **kwargs):
    return frame('I_PRESET', 'I_PREPARE_HIGHLIGHT', *extra, **kwargs)


def menu(**extra):
    return frame('I_PRESET_ENSURE', **extra)


def battle():
    return frame('I_BATTLE_INFO')


class Harness:
    def __init__(self, frames, demon=True, source=GENERAL):
        self.frames, self.frame = frames, frames[0]
        self.clock = SimpleNamespace(now=0)
        self.screenshots = 0
        self.clicks, self.ocr_calls, self.buff_calls, self.green_calls = [], [], [], []
        self.rewards, self.wait_calls, self.current_count = [], 0, 0
        self.device = SimpleNamespace(image=self.frame,
                                      stuck_record_add=lambda value: None,
                                      click_record_clear=lambda: None)
        names = ('I_PRESET', 'I_PRESET_WIT_NUMBER', 'I_PRESET_ENSURE',
                 'I_PRESENT_LESS_THAN_5', 'O_PRESET', 'O_PRESET_FULL',
                 'I_PREPARE_HIGHLIGHT', 'I_PREPARE_DARK', 'I_BUFF', 'I_BATTLE_INFO',
                 'I_WIN', 'I_DE_WIN', 'I_FALSE', 'I_REWARD', 'I_REWARD_GOLD',
                 'I_DISABLE_7DAYS_DIFF_SOUL', 'I_CONFIRM_CLOSE_DIFF_SOUL')
        for name in names:
            setattr(self, name, Marker(name))
        for group in range(1, 8):
            setattr(self, f'C_PRESET_GROUP_{group}', Marker(f'group{group}', group))
        for team in range(1, 6):
            setattr(self, f'C_PRESET_TEAM_{team}', Marker(f'team{team}', team + 10))
        self.conf = SimpleNamespace(lock_team_enable=False, preset_enable=True,
                                    preset_group=2, preset_team=3,
                                    green_enable=True, green_mark='left1',
                                    random_click_swipt_enable=False)
        namespace = {
            'logger': Logger(), 'GeneralBattleConfig': type(self.conf), 'BuffClass': object,
            'Timer': lambda limit: Timer(self.clock, limit),
            'time': SimpleNamespace(sleep=self.sleep), 'sleep': self.sleep,
            'random': SimpleNamespace(uniform=lambda low, high: low),
            'get_color': lambda image, roi: image.get('colors', {}).get(roi[0], (0, 0, 0)),
            'color_similar': lambda first, second: first == second,
        }
        methods = ('switch_preset_team', 'battle_before', 'run_general_battle',
                   'is_in_prepare', 'is_in_real_battle')
        for name, method in extract(source, 'GeneralBattle', methods, namespace).items():
            setattr(self, name, method.__get__(self))
        if demon:
            methods = ('_preset_selection_interrupted', 'battle_wait')
            for name, method in extract(DEMON, 'ScriptTask', methods, namespace).items():
                setattr(self, name, method.__get__(self))

    def screenshot(self):
        self.frame = self.frames[min(self.screenshots, len(self.frames) - 1)]
        self.device.image = self.frame
        self.screenshots += 1
        self.clock.now += 0.05
        if self.screenshots > 50:
            raise AssertionError('Preset loop did not hand off to the active battle')

    def sleep(self, seconds):
        self.clock.now += seconds

    def appear(self, marker, **kwargs):
        return marker in self.frame['visible']

    def click(self, marker, **kwargs):
        self.clicks.append(str(marker))
        if marker == 'I_PRESET_ENSURE':
            self.frame['visible'].discard('I_PRESET_ENSURE')
        return True

    def appear_then_click(self, marker, **kwargs):
        return self.appear(marker) and self.click(marker, **kwargs)

    def ocr_appear(self, marker, **kwargs):
        self.ocr_calls.append(str(marker))
        return False

    def wait_until_appear(self, marker, **kwargs):
        return self.appear(marker)

    def check_and_open_buff(self, buff):
        self.buff_calls.append(buff)

    def is_in_battle(self, is_screenshot=True):
        return self.is_in_real_battle(is_screenshot)

    def green_mark(self, *args):
        self.green_calls.append(args)

    def ui_click_until_disappear(self, marker):
        self.rewards.append(str(marker))
        self.frame['visible'].discard(marker)


class PresetTransitionTests(unittest.TestCase):
    def test_automatic_start_while_opening_menu_reaches_actual_reward_handler(self):
        h = Harness([prepare(), prepare(), battle(), frame('I_DE_WIN'), frame('I_REWARD')])
        self.assertTrue(h.run_general_battle(h.conf, buff=object()))
        self.assertEqual(h.clicks, ['I_PRESET'])
        self.assertEqual(h.ocr_calls, [])
        self.assertEqual(h.buff_calls, [])
        self.assertEqual(h.rewards, ['I_DE_WIN', 'I_REWARD'])
        self.assertEqual(h.current_count, 1)
        self.assertEqual(h.green_calls, [(True, 'left1')])

    def test_automatic_start_at_each_selection_stage_avoids_stale_clicks(self):
        for stage, frames, before in (
                ('opening', [battle()], []),
                ('group', [menu(), battle()], []),
                ('team', [menu(), menu(), battle()], []),
                ('confirm', [menu(), menu(), menu(), battle()], ['team3'])):
            with self.subTest(stage=stage):
                h = Harness(frames)
                self.assertIsNone(h.switch_preset_team(True, 2, 3))
                self.assertEqual(h.clicks, before)

    def test_result_screen_also_hands_off_without_preset_or_ocr_clicks(self):
        for marker in ('I_WIN', 'I_DE_WIN', 'I_FALSE', 'I_REWARD'):
            with self.subTest(marker=marker):
                h = Harness([frame(marker)])
                self.assertIsNone(h.switch_preset_team(True))
                self.assertEqual(h.clicks, [])
                self.assertEqual(h.ocr_calls, [])

    def test_gold_only_result_is_not_accepted_by_demon_reward_handler(self):
        h = Harness([frame('I_REWARD_GOLD')])
        self.assertFalse(h._preset_selection_interrupted())

    def test_failed_battle_after_preset_interruption_returns_existing_failure_result(self):
        h = Harness([prepare(), prepare(), battle(), frame('I_FALSE')])
        self.assertFalse(h.run_general_battle(h.conf))
        self.assertEqual(h.rewards, ['I_FALSE'])
        self.assertEqual(h.clicks, ['I_PRESET'])

    def test_prepare_and_open_menu_take_precedence_over_battle_info(self):
        for visible in (prepare('I_BATTLE_INFO'), frame('I_PRESET_ENSURE', 'I_BATTLE_INFO'),
                        frame('I_PRESENT_LESS_THAN_5', 'I_BATTLE_INFO')):
            with self.subTest(visible=visible):
                h = Harness([visible])
                self.assertFalse(h._preset_selection_interrupted())

    def test_normal_preset_still_selects_group_team_and_confirms(self):
        frames = [prepare(), menu(), menu(colors={2: (224.9, 208.3, 187.4)}), menu(),
                  menu(colors={13: (216.8, 185.0, 146.8)}), menu(), menu(), frame()]
        h = Harness(frames)
        self.assertIsNone(h.switch_preset_team(True, 2, 3))
        self.assertEqual(h.clicks, ['I_PRESET', 'group2', 'team3', 'team3', 'I_PRESET_ENSURE'])

    def test_normal_preparation_keeps_preset_prepare_and_reward_flow(self):
        h = Harness([prepare(), prepare(), menu(), menu(), menu(), menu(),
                     prepare(), battle(), frame('I_REWARD')])
        self.assertTrue(h.run_general_battle(h.conf))
        self.assertEqual(h.clicks, ['I_PRESET', 'team3', 'I_PRESET_ENSURE', 'I_PREPARE_HIGHLIGHT'])
        self.assertEqual(h.buff_calls, [None])
        self.assertEqual(h.rewards, ['I_REWARD'])

    def test_other_task_without_optional_hook_keeps_original_selection_behavior(self):
        h = Harness([menu(), menu(), menu(), menu(), frame()], demon=False)
        self.assertIsNone(h.switch_preset_team(True, 2, 3))
        self.assertEqual(h.clicks, ['team3', 'I_PRESET_ENSURE'])

    def test_disabled_preset_preserves_none_return_and_does_not_capture(self):
        h = Harness([battle()])
        self.assertIsNone(h.switch_preset_team(False))
        self.assertEqual(h.screenshots, 0)


if __name__ == '__main__':
    unittest.main()
