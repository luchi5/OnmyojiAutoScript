"""Stop result clicks after leaving Chess and navigate back from known pages.

Loaded separately by script_task so running workers can use this guard on the
next invocation even when their original GameUi/result modules are cached.
"""

import time

from module.logger import logger
from tasks.Chess.runtime.image import ChessImage as RuleImage
from tasks.GameUi.page import page_chess


class _ChessResultRediscovered(Exception):
    """Hand the current settlement frame back to the caller of outside recovery."""


class ChessLobbyRecoveryMixin:
    # Rank progress has no return button and can resemble the courtyard skin.
    I_CHESS_RANK_PROGRESS = RuleImage(
        roi_front=(370, 455, 77, 35), roi_back=(360, 445, 100, 55),
        threshold=0.9, method='Template matching',
        file='./tasks/Chess/res/rank_progress.png',
    )
    # The new season starts at /100 rather than /200. Identify the paired
    # progress-track ornaments instead of depending on a changing score.
    I_CHESS_RANK_TRACK_LEFT = RuleImage(
        roi_front=(126, 423, 20, 25), roi_back=(120, 418, 35, 40),
        threshold=0.9, method='Template matching',
        file='./tasks/Chess/res/rank_track_cap.png',
    )
    I_CHESS_RANK_TRACK_RIGHT = RuleImage(
        roi_front=(640, 423, 20, 25), roi_back=(634, 418, 35, 40),
        threshold=0.9, method='Template matching',
        file='./tasks/Chess/res/rank_track_cap_right.png',
    )

    def _chess_rank_progress_visible(self) -> bool:
        return (self.appear(self.I_CHESS_RANK_PROGRESS)
                or (self.appear(self.I_CHESS_RANK_TRACK_LEFT)
                    and self.appear(self.I_CHESS_RANK_TRACK_RIGHT)))

    def _chess_settlement_visible(self) -> bool:
        return (self._chess_rank_progress_visible()
                or self.chess_result_continue_visible()
                or self._chess_reward_visible()
                or self.appear(self.I_CHESS_EXIT_TO_LOBBY_2))

    def _chess_outside_page_visible(self) -> bool:
        # These are positive global page markers, not guesses based on missing
        # battle icons. Unknown result/loading frames keep their existing flow.
        return (not self._chess_settlement_visible()
                and (self.appear(self.I_CHECK_MAIN) or self.appear(self.I_CHECK_COLLECTION)))

    def _recover_chess_outside_page(self) -> bool:
        if getattr(self, '_chess_outside_recovering', False):
            return False
        if not self._chess_outside_page_visible():
            return False
        self._chess_share_advancing = False
        # Rank animations can briefly match a courtyard costume before their
        # progress-track ornaments appear. Require three fresh, consistent frames.
        marker = self.I_CHECK_MAIN if self.appear(self.I_CHECK_MAIN) else self.I_CHECK_COLLECTION
        for _ in range(2):
            time.sleep(0.35)
            self.screenshot()
            if not self._chess_outside_page_visible() or not self.appear(marker):
                return True  # Re-evaluate the new frame through the result loop.
        logger.warning(
            'Chess flow reached courtyard/collection; stop result clicks '
            'and navigate back to Chess lobby'
        )
        self._chess_outside_recovering = True
        try:
            self.goto_page(page_chess)
            if self._chess_outside_page_visible():
                from module.exception import GameStuckError
                raise GameStuckError('Chess lobby recovery did not leave the outside page')
            return True
        except _ChessResultRediscovered:
            logger.info('Chess settlement rediscovered during outside recovery; resume result flow')
            return True
        finally:
            self._chess_outside_recovering = False

    def _refresh_current_page(self, destination, skip_first_screenshot):
        if not getattr(self, '_chess_outside_recovering', False):
            return super()._refresh_current_page(destination, skip_first_screenshot)
        if not skip_first_screenshot:
            self.screenshot()
        if self._chess_settlement_visible():
            raise _ChessResultRediscovered()
        current = super()._refresh_current_page(destination, skip_first_screenshot=True)
        # The navigator may take more screenshots while confirming a candidate.
        # Never execute a courtyard transition on a newly visible settlement.
        if self._chess_settlement_visible():
            raise _ChessResultRediscovered()
        return current

    def chess_lobby_visible(self) -> bool:
        return not self._chess_outside_page_visible() and super().chess_lobby_visible()

    def chess_result_page_visible(self) -> bool:
        return (self._chess_rank_progress_visible()
                or (not self._chess_outside_page_visible() and super().chess_result_page_visible()))

    def _click_chess_result_continue(self) -> None:
        if self._chess_outside_page_visible():
            self._chess_share_advancing = False
            return
        return super()._click_chess_result_continue()

    def _advance_chess_result_stage(self) -> bool:
        if self._chess_rank_progress_visible():
            self._click_chess_result_continue()
            return True
        if self._recover_chess_outside_page():
            return True
        return super()._advance_chess_result_stage()
