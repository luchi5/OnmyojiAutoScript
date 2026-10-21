# This Python file uses the following encoding: utf-8
import time

from module.logger import logger
from tasks.Chess.assets import ChessAssets
from tasks.Component.Buy.buy import Buy
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_entertainment
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.config import ItachiCoinShop as ItachiCoinShopConfig
from tasks.RichMan.page import page_itachi_shop


class ItachiCoinShop(Buy, GameUi, RichManAssets):
    """购买鼬乐园商店中自动前移的三个鼬乐礼盒。"""

    ENTERTAINMENT_OVERLAY_TIMEOUT = 8.0
    ENTERTAINMENT_STABLE_TIME = 1.0
    GIFT_DIALOG_TIMEOUT = 3.0
    PURCHASE_RESULT_TIMEOUT = 8.0
    PURCHASE_RETURN_GRACE = 3.0
    REWARD_DISMISS_TIMEOUT = 5.0
    MAX_GIFT_PURCHASES = 3

    def execute_itachi_coin_shop(self, con: ItachiCoinShopConfig) -> None:
        if con is None or not con.itachi_coin_buy_jade:
            logger.info('Itachi coin purchase is disabled')
            return

        logger.hr('Start Itachi coin shop', 1)
        self.goto_page(page_entertainment)
        if not self._wait_entertainment_overlay_closed():
            logger.warning('Itachi Park overlay did not close; skip coin purchase')
            return

        self.goto_page(page_itachi_shop)
        purchased = 0
        for _ in range(self.MAX_GIFT_PURCHASES):
            if not self._open_front_gift_once():
                # 已购买商品不会再打开购买弹窗。这里只点击一次，避免在同一
                # 商品位置进行高频试探。
                logger.info('未出现礼盒购买框，停止本次购买；可能已购完或界面未响应')
                break
            if not self._buy_opened_gift():
                break
            purchased += 1

        logger.info(f'Itachi coin gifts purchased: {purchased}')
        self.goto_page(page_entertainment)

    def _wait_entertainment_overlay_closed(self) -> bool:
        """处理鼬乐园附属页，跳过消失并稳定后才允许识别商店。"""

        deadline = time.monotonic() + self.ENTERTAINMENT_OVERLAY_TIMEOUT
        stable_since = None
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(ChessAssets.I_SKIP):
                self.appear_then_click(ChessAssets.I_SKIP, interval=0.5)
                stable_since = None
                continue

            if stable_since is None:
                stable_since = time.monotonic()
                continue
            if time.monotonic() - stable_since >= self.ENTERTAINMENT_STABLE_TIME:
                return True

        return False

    def _open_front_gift_once(self) -> bool:
        """只点击一次前排商品，并等待至多三秒确认购买框。"""

        self.screenshot()
        self.click(self.C_ITACHI_GIFT)
        deadline = time.monotonic() + self.GIFT_DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_BUY_PLUS):
                return True
        return False

    def _read_itachi_coin(self) -> tuple[int, int] | None:
        result = self.O_ITACHI_COIN.ocr(self.device.image)
        if isinstance(result, tuple) and len(result) >= 3:
            current, _, total = result[:3]
            if total > 0:
                logger.info(f'Itachi coin: {current}/{total}')
                return int(current), int(total)
        logger.warning(f'Itachi coin OCR failed: {result}')
        return None

    def _read_buy_cost(self) -> int | None:
        result = self.O_ITACHI_BUY_COST.ocr(self.device.image)
        if isinstance(result, int) and result > 0:
            logger.info(f'Itachi gift cost: {result}')
            return result
        logger.warning(f'Itachi gift cost OCR failed: {result}')
        return None

    def _buy_opened_gift(self) -> bool:
        """确认一次购买，并区分奖励成功与直接退回商店的失败状态。"""

        self.screenshot()
        before_coin = self._read_itachi_coin()
        cost = self._read_buy_cost()
        if before_coin is None or cost is None or before_coin[0] < cost:
            logger.warning("Itachi gift balance/cost unverified or insufficient; skip purchase")
            return False
        self.click(self.C_BUY_MORE)

        deadline = time.monotonic() + self.PURCHASE_RESULT_TIMEOUT
        dialog_absent_since = None
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_UI_REWARD, threshold=0.6):
                reward_closed = self._dismiss_purchase_reward()
                if reward_closed:
                    self._verify_coin_cost(before_coin, cost)
                return reward_closed

            if self.appear(self.I_BUY_PLUS):
                dialog_absent_since = None
                continue

            if dialog_absent_since is None:
                dialog_absent_since = time.monotonic()
                continue
            if time.monotonic() - dialog_absent_since >= self.PURCHASE_RETURN_GRACE:
                logger.warning('Itachi gift purchase failed or coin is insufficient')
                return False

        logger.warning('Itachi gift purchase result timeout')
        return False

    def _dismiss_purchase_reward(self) -> bool:
        deadline = time.monotonic() + self.REWARD_DISMISS_TIMEOUT
        while time.monotonic() < deadline:
            self.screenshot()
            if not self.appear(self.I_UI_REWARD, threshold=0.6):
                return True
            self.ui_reward_appear_click()
        logger.warning('Itachi gift reward did not close')
        return False

    def _verify_coin_cost(
        self,
        before_coin: tuple[int, int] | None,
        cost: int | None,
    ) -> None:
        self.screenshot()
        after_coin = self._read_itachi_coin()
        if before_coin is None or after_coin is None or cost is None:
            return
        consumed = before_coin[0] - after_coin[0]
        if consumed == cost:
            logger.info(f'Itachi gift purchase confirmed: consumed={consumed}')
        else:
            logger.warning(
                'Itachi gift coin change mismatch: '
                f'before={before_coin[0]}, after={after_coin[0]}, '
                f'cost={cost}'
            )
