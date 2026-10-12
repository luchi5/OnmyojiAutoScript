"""Stop result clicks after leaving Chess and navigate back from known pages.

Loaded separately by script_task so running workers can use this guard on the
next invocation even when their original GameUi/result modules are cached.
"""

from module.logger import logger
from tasks.GameUi.page import page_chess


class ChessLobbyRecoveryMixin:
    def _chess_outside_page_visible(self) -> bool:
        # These are positive global page markers, not guesses based on missing
        # battle icons. Unknown result/loading frames keep their existing flow.
        return self.appear(self.I_CHECK_MAIN) or self.appear(self.I_CHECK_COLLECTION)

    def _recover_chess_outside_page(self) -> bool:
        if not self._chess_outside_page_visible():
            return False
        self._chess_share_advancing = False
        logger.warning(
            'Chess flow reached courtyard/collection; stop result clicks '
            'and navigate back to Chess lobby'
        )
        self.goto_page(page_chess)
        return True

    def chess_lobby_visible(self) -> bool:
        return not self._chess_outside_page_visible() and super().chess_lobby_visible()

    def chess_result_page_visible(self) -> bool:
        return not self._chess_outside_page_visible() and super().chess_result_page_visible()

    def _click_chess_result_continue(self) -> None:
        if self._chess_outside_page_visible():
            self._chess_share_advancing = False
            return
        return super()._click_chess_result_continue()

    def _advance_chess_result_stage(self) -> bool:
        if self._recover_chess_outside_page():
            return True
        return super()._advance_chess_result_stage()
