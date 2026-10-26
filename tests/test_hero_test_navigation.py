"""Dynamic HeroTest modes must be reachable within each task's page session."""
from types import SimpleNamespace
import unittest

from tasks.GameUi import page as pages
from tasks.GameUi.registry import PageRegistry
from tasks.GameUi.session import NavigatorSession
from tasks.HeroTest.config import Layer
from tasks.HeroTest.script_task import ScriptTask


class HeroTestNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PageRegistry.load_all_pages()

    def task(self, layer):
        # No Config or Device: this only builds the actual navigation graph.
        task = ScriptTask.__new__(ScriptTask)
        task.conf = SimpleNamespace(herotest=SimpleNamespace(layer=layer))
        task.navigator = NavigatorSession(task_category='hero_test')
        task.navigator.bootstrap(PageRegistry.all())
        task.init_pages()
        return task

    def test_each_mode_is_reachable_from_main_and_returns_to_session_home(self):
        for layer, action_name in (
                (Layer.YANWU, 'I_GBB'), (Layer.MIJING, 'I_BCMJ'),
                (Layer.CHUANCHENG, 'I_ENTER_CCSL'), (Layer.MENGXU, 'I_ENTER_MXMJ')):
            with self.subTest(layer=layer):
                task = self.task(layer)
                home = task.navigator.pages['page_hero_test']
                main = task.navigator.pages['page_main']
                path = task._build_path(main, task.page_hero_mode)
                self.assertIsNotNone(path)
                self.assertEqual([edge.destination.key for edge in path],
                                 ['page_exploration', 'page_hero_test', 'page_hero_mode'])
                self.assertIs(path[-1].action, getattr(task, action_name))
                back = task._build_path(task.page_hero_mode, home)
                self.assertEqual(len(back), 1)
                self.assertIs(back[0].destination, home)
                self.assertIs(back[0].action, task.I_BACK_YOLLOW)

    def test_initialization_keeps_global_pages_and_other_task_sessions_unchanged(self):
        global_edges = list(pages.page_hero_test.transitions)
        first = self.task(Layer.CHUANCHENG)
        first_home = first.navigator.pages['page_hero_test']
        first_edges = list(first_home.transitions)
        second = self.task(Layer.MIJING)
        second_home = second.navigator.pages['page_hero_test']
        self.assertEqual(pages.page_hero_test.transitions, global_edges)
        self.assertEqual(first_home.transitions, first_edges)
        self.assertIsNot(first_home, second_home)
        self.assertIsNot(first.page_hero_mode, second.page_hero_mode)
        self.assertIs(first._build_path(first_home, first.page_hero_mode)[0].action,
                      first.I_ENTER_CCSL)
        self.assertIs(second._build_path(second_home, second.page_hero_mode)[0].action,
                      second.I_BCMJ)


if __name__ == '__main__':
    unittest.main()
