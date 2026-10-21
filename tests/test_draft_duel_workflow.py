"""Task integration boundaries: admission, configured leader and draft completion."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import ast
from collections import deque
from types import SimpleNamespace
import numpy as np
import cv2

from tasks.DraftDuel.local_draft import LocalDraft, Frame, TimedOCR, DraftVision
from tasks.DraftDuel.ratings import Ratings
from tasks.DraftDuel.strategy import Candidate
from tasks.DraftDuel.session import DraftDuelSession, SessionError, SessionUnavailable
from tasks.DraftDuel.team import TeamVision
from tasks.DraftDuel.workflow import read_offer, OwnDraftIO, save_frame


class WorkflowTests(unittest.TestCase):
    def test_explicit_leader_controls_session_even_with_reversed_names(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root)/'runtime.sqlite'
            captain = DraftDuelSession(db, 'reversed', 'zz-leader', 'aa-member', leader_id='zz-leader')
            member = DraftDuelSession(db, 'reversed', 'aa-member', 'zz-leader', leader_id='zz-leader')
            captain.acquire_lease(); member.acquire_lease()
            with self.assertRaises(SessionUnavailable): member.open_session()
            captain.open_session(); member.open_session()
            self.assertTrue(captain.is_leader)
            self.assertFalse(member.is_leader)
            with self.assertRaises(SessionError): member.set_workflow('starting')
            captain.set_workflow('starting', {'costs': {'zz-leader': 300000}})
            self.assertEqual(member.workflow()['phase'], 'starting')
            with self.assertRaises(SessionError):
                DraftDuelSession(db, 'reversed', 'aa-member', 'zz-leader', leader_id='aa-member')

    def test_guard_prevents_own_draft_click_after_peer_stops(self):
        io, guard = Mock(), Mock(side_effect=SessionError('peer stopped'))
        with self.assertRaises(SessionError): OwnDraftIO(io, guard).tap(0)
        io.tap_point.assert_not_called()

    def test_draft_click_does_not_reuse_invitation_button_guard_name(self):
        io, guard = Mock(), Mock()
        OwnDraftIO(io, guard).tap(1)
        io.tap_point.assert_called_once_with(773, 660, control_name='DRAFT_DUEL_PICK_1')

    def test_real_click_guard_accepts_valid_pipeline_and_still_blocks_repeats(self):
        source = Path(__file__).parents[1]/'module/device/device.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Device')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'click_record_check')
        namespace = {'logger': Mock(), 'GameTooManyClickError': RuntimeError}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), 'exec'), namespace)
        record = deque(maxlen=15)
        device = SimpleNamespace(click_record=record, click_record_clear=record.clear)
        check = lambda: namespace['click_record_check'](device)
        for name in ['DRAFT_DUEL_TEAM']*6+['SWIPE']*2+['DRAFT_DUEL_START']+['DRAFT_DUEL_PICK_1']*5:
            record.append(name); check()
        record.clear()
        with self.assertRaises(RuntimeError):
            for _ in range(10): record.append('DRAFT_DUEL_PICK_1'); check()

    def test_evidence_written_to_chinese_account_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)/'01-自己'; folder.mkdir()
            image = np.full((10, 10, 3), 123, np.uint8)
            path = folder/'frame.png'; save_frame(path, image)
            restored = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            self.assertTrue(np.array_equal(image, restored))

    def test_complete_requires_ten_confirmed_slots_not_ten_sent_clicks(self):
        runner = LocalDraft({'one': Mock(), 'two': Mock()})
        try:
            cards = tuple(Candidate(f'{a}:r{p}:s0', a, '椒图')
                          for a in runner.devices for p in range(1, 6))
            runner.attempted = {(a, p) for a in runner.devices for p in range(1, 6)}
            self.assertFalse(runner.complete)
            runner.confirmed = cards[:-1]
            self.assertFalse(runner.complete)
            runner.confirmed = cards
            self.assertTrue(runner.complete)
            runner.awaiting['two'] = (None, cards[-1])
            self.assertFalse(runner.complete)
        finally: runner.close()

    def test_fifth_pick_requires_positive_return_lobby_and_portrait_change(self):
        image = np.full((720, 1280, 3), 200, np.uint8)
        devices = {'one': Mock(), 'two': Mock()}
        devices['one'].capture.return_value = (image, 101)
        runner = LocalDraft(devices)
        card = Candidate('one:r5:s0', 'one', '椒图')
        before = Frame('one', 100, 5, 30, (card,), '5:椒图', np.zeros((35, 210, 3)))
        vision = Mock(); vision.ocr.return_value = ('unrecognized', .1)
        vision.read.return_value = None
        runner.local.vision = vision
        try:
            runner.awaiting['one'] = (before, card)
            vision.finished_lobby.return_value = False
            self.assertIsNone(runner.observe('one'))
            vision.finished_lobby.return_value = True
            self.assertEqual(runner.observe('one'), ('confirmed', card))
            runner.awaiting['one'] = (replace(before, round_no=4), card)
            self.assertIsNone(runner.observe('one'))
        finally: runner.close()


class AdmissionImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ocr = TimedOCR(); cls.vision = TeamVision(cls.ocr)
        cls.root = Path(__file__).parent/'fixtures/draft_duel/team'

    @classmethod
    def tearDownClass(cls): cls.ocr.close()

    def test_positive_team_and_actual_price_required(self):
        image = cv2.imread(str(self.root/'formed_paid.png'))
        offer = read_offer(self.vision, image, '千山长月', '痴不痴路')
        self.assertEqual(offer.cost, 300000)
        self.assertIsNotNone(offer.start)
        wrong = read_offer(self.vision, image, '其他队友', '痴不痴路')
        self.assertIsNone(wrong.cost)
        image[682:720, 1130:1280] = 0
        self.assertIsNone(read_offer(self.vision, image, '千山长月', '痴不痴路').cost)

    def test_free_is_positive_text_evidence(self):
        image = cv2.imread(str(self.root/'formed.png'))
        self.assertEqual(read_offer(self.vision, image, '千山长月', '痴不痴路').cost, 0)

    def test_discount_reads_actual_ten_wan_and_not_struck_thirty_wan(self):
        image = cv2.imread(str(self.root/'formed_member.png'))
        self.assertEqual(read_offer(self.vision, image, '痴不痴路', '千山长月').cost, 100000)


if __name__ == '__main__': unittest.main()
