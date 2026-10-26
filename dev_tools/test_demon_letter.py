"""Replay delayed rewards and unacknowledged quiz clicks without a device/OCR."""
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from dev_tools.test_bondling_battle_entry import FakeClock, FakeLogger, FakeTimer, extract_methods
from dev_tools.test_delegation_demon_rewards import DEMON, RuleClick


def question(count=2, **kwargs):
    return dict(count=count, visible=True, reward=False, result=False,
                text='阴阳寮宴会料理的品级不包括', options=('上等', '良品', '特选'), **kwargs)


class LetterHarness:
    def __init__(self, frames):
        self.frames = deque(frames)
        self.frame = None
        self.clock = FakeClock(step=0.5)
        self.device = SimpleNamespace(image=None, click_record_clear=Mock())
        self.clicks = []
        self.on_click = lambda marker: None
        for marker in ('I_LETTER_CLOSE', 'C_ANSWER_1', 'C_ANSWER_2', 'C_ANSWER_3'):
            setattr(self, marker, marker)
        self.O_LETTER_COUNT = SimpleNamespace(ocr=lambda f: (f['count'], 3-f['count'], 3))
        self.O_LETTER_QUESTION = SimpleNamespace(detect_text=lambda f: f['text'])
        for i in range(3):
            setattr(self, f'O_LETTER_ANSWER_{i+1}',
                    SimpleNamespace(detect_text=lambda f, i=i: f['options'][i]))
        self.lookup = Mock(return_value=3)
        namespace = {
            'logger': FakeLogger(), 'Answer': lambda: SimpleNamespace(answer_one=self.lookup),
            'read_letter_option': lambda image, rule: rule.detect_text(image),
            'RuleImage': lambda **kwargs: 'result', 'RuleClick': RuleClick,
            'Timer': lambda limit: FakeTimer(self.clock, limit), 'GameStuckError': RuntimeError,
            'time': SimpleNamespace(sleep=lambda s: setattr(self.clock, 'now', self.clock.now+s)),
        }
        self.run = extract_methods(DEMON, 'ScriptTask', ('_mail',), namespace)['_mail'].__get__(self)

    def screenshot(self):
        self.clock.advance()
        if self.frames:
            self.frame = self.frames.popleft()
        self.device.image = self.frame

    def appear(self, marker, **kwargs):
        if marker == 'I_LETTER_CLOSE':
            return self.frame['visible']
        return marker == 'result' and self.frame['result']

    def click(self, marker, **kwargs):
        self.clicks.append((getattr(marker, 'name', marker), self.clock.now, self.frame.copy()))
        self.on_click(marker)
        # Match BaseTask: without interval the input is sent but return is False.
        return bool(kwargs.get('interval'))

    def ui_reward_appear_click(self):
        return self.click('reward', interval=0.4) if self.frame['reward'] else False

    def answers(self):
        return [item for item in self.clicks if item[0].startswith('C_ANSWER')]


class DemonLetterTests(unittest.TestCase):
    def test_delayed_previous_reward_during_ocr_does_not_mark_next_question_answered(self):
        q2, q3 = question(), question(3)
        reward = {**q2, 'reward': True, 'text': '阴阳寮宴会料级不包括获得奖励',
                  'options': ('', 'EOA10000', '')}
        r2, r3 = {**q2, 'result': True}, {**q3, 'result': True}
        closed = {**q3, 'visible': False}
        h = LetterHarness([q2, q2, q2, reward, q2, q2, q2, r2,
                           q3, q3, q3, r3, closed, closed])
        h.run('lantern')
        self.assertEqual([item[2]['count'] for item in h.answers()], [2, 3])
        self.assertTrue(all(not item[2]['reward'] for item in h.answers()))
        self.assertEqual(h.clicks[0][0], 'reward')
        self.assertEqual(h.lookup.call_count, 3)  # Discard the first, stale OCR result.

    def test_reward_contaminated_ocr_waits_for_clear_question(self):
        for changes in ({'text': '题目获得奖励'},):
            with self.subTest(changes=changes):
                q = question(3)
                bad = {**q, **changes}
                h = LetterHarness([q, q, bad, q, q, q, {**q, 'result': True},
                                   {**q, 'visible': False}, {**q, 'visible': False}])
                h.run('lantern')
                self.assertEqual(len(h.answers()), 1)
                h.lookup.assert_called_once()
                self.assertEqual(h.answers()[0][0], 'C_ANSWER_3')

    def test_incomplete_ocr_uses_default_answer_instead_of_waiting_forever(self):
        q = question(3)
        h = LetterHarness([q, q, {**q, 'options': ('', '', '')}, q,
                           {**q, 'result': True}, {**q, 'visible': False},
                           {**q, 'visible': False}])
        h.lookup.return_value = None
        h.run('lantern')
        self.assertEqual([item[0] for item in h.answers()], ['C_ANSWER_1'])
        self.assertEqual(h.clicks[-1][0], 'I_LETTER_CLOSE')

    def test_lost_click_retries_then_closes_only_after_visible_result(self):
        q = question(3)
        h = LetterHarness([q])

        def respond(marker):
            if marker == 'C_ANSWER_3' and len(h.answers()) == 2:
                h.frames.append({**q, 'result': True})
            elif marker == 'I_LETTER_CLOSE':
                h.frames.append({**q, 'visible': False})

        h.on_click = respond
        h.run('lantern')
        self.assertEqual(len(h.answers()), 2)
        self.assertGreaterEqual(h.answers()[1][1]-h.answers()[0][1], 3)
        h.device.click_record_clear.assert_called_once()
        self.assertTrue(h.clicks[-1][2]['result'])

    def test_stuck_question_has_three_attempts_and_a_specific_error(self):
        h = LetterHarness([question()])
        with self.assertRaisesRegex(RuntimeError, 'question 2: no result after 3'):
            h.run('lantern')
        self.assertEqual(len(h.answers()), 3)
        h.device.click_record_clear.assert_called_once()

    def test_next_question_arriving_during_ocr_is_reread_before_click(self):
        q2, q3 = question(), question(3)
        h = LetterHarness([q2, q2, q2, q3, q3, q3, q3, {**q3, 'result': True},
                           {**q3, 'visible': False}, {**q3, 'visible': False}])
        h.run('lantern')
        self.assertEqual([item[2]['count'] for item in h.answers()], [3])
        self.assertEqual(h.lookup.call_count, 2)

    def test_result_arriving_during_ocr_is_not_answered_again(self):
        q = question(3)
        result = {**q, 'result': True}
        h = LetterHarness([q, q, q, result, result,
                           {**q, 'visible': False}, {**q, 'visible': False}])
        h.run('lantern')
        self.assertFalse(h.answers())
        self.assertEqual([item[0] for item in h.clicks], ['I_LETTER_CLOSE'])


if __name__ == '__main__':
    unittest.main()
