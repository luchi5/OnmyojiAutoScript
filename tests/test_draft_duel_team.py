"""Invitation boundaries and real 01/09 screenshot replay, no device IO."""
from pathlib import Path
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from tasks.DraftDuel.team import TeamAction, TeamView, TeamWorker, TeamVision, execute_action


class TeamWorkerTests(unittest.TestCase):
    def test_long_nickname_requires_complete_wide_read(self):
        vision = TeamVision(Mock())
        vision.match = Mock(side_effect=lambda image, name, *args: (1, 1) if name == 'title' else None)
        vision.text = Mock(side_effect=['队长', '长昵称截断', '完整的长昵称'])
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertEqual(vision.read(image, '完整的长昵称', '队长').kind, 'team_ready')
        vision.text = Mock(side_effect=['队长', '长昵称截断', '另一位长昵称'])
        self.assertNotEqual(vision.read(image, '完整的长昵称', '队长').kind, 'team_ready')

    def worker(self, role):
        self.now = 100
        return TeamWorker(role, clock=lambda: self.now)

    def test_member_never_navigates_or_opens_friend_lists(self):
        w = self.worker('member')
        for view in (TeamView('lobby', (1013, 356)), TeamView('friend_list', (501, 362), True),
                     TeamView('other_invitation', (137, 254)), TeamView()):
            self.assertEqual(w.plan(view).kind, 'wait')
        self.assertEqual(w.plan(TeamView('invitation', (137, 254))).kind, 'accept')
        self.now += 3
        self.assertEqual(w.plan(TeamView('invitation', (137, 254))).kind, 'wait')
        self.assertEqual(w.plan(TeamView('team_ready')).kind, 'done')

    def test_leader_reobserves_after_scroll_and_before_invite(self):
        w = self.worker('leader')
        self.assertEqual(w.plan(TeamView('lobby', (1013, 356))).kind, 'open_list')
        self.now += 2
        self.assertEqual(w.plan(TeamView('friend_list', tab='好友')).kind, 'tab')
        self.now += 2
        self.assertEqual(w.plan(TeamView('friend_list', tab='跨区')).kind, 'scroll')
        self.now += 2
        a = w.plan(TeamView('friend_list', (501, 362), tab='跨区'))
        self.assertEqual((a.kind, a.point), ('select', (501, 362)))
        self.now += 2
        self.assertEqual(w.plan(TeamView('friend_list', tab='跨区')).kind, 'wait')
        self.now += 2
        self.assertEqual(w.plan(TeamView('friend_list', (501, 290), True, '跨区')).kind, 'invite')
        self.now += 2
        self.assertEqual(w.plan(TeamView('lobby', (1013, 356))).kind, 'wait')

    def test_selection_is_sent_once_and_failure_stops(self):
        w = self.worker('leader')
        w.tab_requested = True
        view = TeamView('friend_list', (501, 362), tab='跨区')
        self.assertEqual(w.plan(view).kind, 'select')
        self.now += 2
        self.assertEqual(w.plan(view).kind, 'wait')
        self.now += 5
        self.assertEqual(w.plan(view).kind, 'selection_unconfirmed')

    def test_leader_does_not_accept_invitations_and_scan_is_bounded(self):
        w = self.worker('leader')
        self.assertEqual(w.plan(TeamView('invitation', (137, 254))).kind, 'wait')
        w.tab_requested = True
        for _ in range(12):
            self.assertEqual(w.plan(TeamView('friend_list', tab='跨区')).kind, 'scroll')
            self.now += 2
        self.assertEqual(w.plan(TeamView('friend_list', tab='跨区')).kind, 'friend_not_found')

    def test_io_has_only_team_taps_and_scroll(self):
        io = Mock()
        execute_action(io, TeamAction('wait'))
        execute_action(io, TeamAction('done'))
        io.tap_point.assert_not_called()
        execute_action(io, TeamAction('scroll'))
        io.swipe.assert_called_once_with(850, 468, 850, 235, 450)


class TeamScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tasks.DraftDuel.local_draft import TimedOCR
        cls.ocr = TimedOCR()
        cls.vision = TeamVision(cls.ocr)
        cls.root = Path(__file__).parent/'fixtures/draft_duel/team'

    @classmethod
    def tearDownClass(cls):
        cls.ocr.close()

    def read(self, filename, teammate='千山长月', own='痴不痴路'):
        return self.vision.read(cv2.imread(str(self.root/filename)), teammate, own)

    def test_exact_name_and_selection_from_scrolled_real_list(self):
        self.assertEqual(self.read('friend_list.png').target, None)
        v = self.read('cross_scrolled.png')
        self.assertEqual(v.tab, '跨区')
        self.assertIsNotNone(v.target)
        self.assertFalse(v.selected)
        self.assertTrue(self.read('selected.png').selected)
        self.assertTrue(self.read('selected_right.png').selected)
        self.assertIsNone(self.read('cross_scrolled.png', '不存在的昵称').target)

    def test_only_correct_leader_and_mode_can_be_accepted(self):
        self.assertEqual(self.read('invitation.png', '痴不痴路', '千山长月').kind, 'invitation')
        self.assertEqual(self.read('invitation.png', '其他队长', '千山长月').kind, 'other_invitation')
        im = cv2.imread(str(self.root/'invitation.png'))
        im[256:287, 225:355] = 0
        self.assertEqual(self.vision.read(im, '痴不痴路').kind, 'other_invitation')

    def test_membership_requires_both_positive_names(self):
        self.assertEqual(self.read('formed.png').kind, 'team_ready')
        self.assertNotEqual(self.read('formed.png', '其他队员').kind, 'team_ready')
        self.assertNotEqual(self.read('solo.png').kind, 'team_ready')

    def test_courtyard_is_passive_for_member(self):
        self.assertEqual(self.read('courtyard09.png').kind, 'unknown')


if __name__ == '__main__':
    unittest.main()
