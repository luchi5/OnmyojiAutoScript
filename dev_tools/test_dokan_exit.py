"""Replay Dokan exit transitions without importing runtime/device modules."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from dev_tools.test_demon_preset_transition import extract, Logger

ROOT = Path(__file__).resolve().parents[1]


def run(frames, navigation_error=None):
    clock = SimpleNamespace(now=0)
    h = SimpleNamespace(clicks=[], routes=[], captures=0, current=set())
    for name in ('I_CHECK_MAIN', 'I_CHECK_TOWN', 'I_RYOU_DOKAN_TOPPA_RANK',
                 'C_DOKAN_TOPPA_RANK_CLOSE_AREA', 'I_RYOU_DOKAN_QUIT_BATTLE_ENSURE',
                 'I_RYOU_DOKAN_DOKAN_QUIT', 'I_UI_BACK_BLUE', 'I_BACK_Y'):
        setattr(h, name, name)
    def screenshot():
        h.current = set(frames[min(h.captures,len(frames)-1)])
        h.captures += 1
        clock.now += 1
    def navigate(page):
        h.routes.append(page)
        if navigation_error:
            raise navigation_error
    h.screenshot = screenshot
    h.appear = lambda marker: marker in h.current
    h.click = lambda marker, **kw: h.clicks.append(marker)
    h.goto_page = navigate
    method = extract(ROOT/'tasks/Dokan/script_task.py', 'ScriptTask', ('goto_main',),
                     {'time':SimpleNamespace(monotonic=lambda:clock.now), 'logger':Logger(),
                      'page_main':'main', 'GameStuckError':RuntimeError})['goto_main']
    try:
        method(h)
    except RuntimeError as error:
        h.error = error
    return h


class ExitTests(unittest.TestCase):
    def test_town_uses_normal_navigation(self):
        h = run([['I_BACK_Y'], ['I_CHECK_TOWN']])
        self.assertEqual(h.routes, ['main'])
        self.assertEqual(h.captures, 2)

    def test_rank_and_confirmation_take_priority_over_town(self):
        h = run([['I_CHECK_TOWN','I_RYOU_DOKAN_TOPPA_RANK'],
                 ['I_CHECK_TOWN','I_RYOU_DOKAN_QUIT_BATTLE_ENSURE'],['I_CHECK_TOWN']])
        self.assertEqual(h.clicks, ['C_DOKAN_TOPPA_RANK_CLOSE_AREA','I_RYOU_DOKAN_QUIT_BATTLE_ENSURE'])
        self.assertEqual(h.routes,['main'])

    def test_existing_courtyard_does_not_navigate_again(self):
        h = run([['I_CHECK_MAIN']])
        self.assertEqual(h.routes,[])

    def test_unknown_page_still_times_out(self):
        h = run([[]])
        self.assertIn('60 seconds',str(h.error))

    def test_failed_navigation_is_not_reported_as_success(self):
        h = run([['I_CHECK_TOWN']], RuntimeError('navigation failed'))
        self.assertEqual(str(h.error),'navigation failed')


if __name__ == '__main__':
    unittest.main()
