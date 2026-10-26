# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

from module.logger import logger
from module.exception import TaskEnd
from tasks.RichMan.borrow_selection import borrow_cards, empty_borrow_slots, beginner_travel, borrow_only_page

from tasks.GameUi.page import page_main, page_travel
from tasks.GameUi.game_ui import GameUi
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.config import ThousandThings as ConfigThousandThings

class ThousandThings(GameUi, RichManAssets):

    def execute_tt(self, con: ConfigThousandThings) -> None:
        """

        :param con:
        :return:
        """
        logger.hr('Start Thousand Things')
        if not con.enable:
            logger.info('Thousand Things is disabled')
            return
        self.screenshot()
        if borrow_only_page(self.device.image):
            logger.info('借用页面：先退出，再检查千物宝库')
            self._tt_leave_borrow()
        self.goto_page(page_travel)

        self.screenshot()
        is_beginner = beginner_travel(self.device.image)
        logger.info('珍旅居：明确识别为新手，跳过式神借出，继续检查宝库' if is_beginner
                    else '珍旅居：未识别到明确新手标志，按普通账号处理')
        if con.earn_money and not is_beginner:
            logger.info('Start Earn Money')
            self.tt_earn_money()

        while 1:
            self.screenshot()
            if self.appear(self.I_TT_CHECK):
                break
            if self.appear_then_click(self.I_TT_ENTER, interval=1):
                continue
        logger.info('Enter Thousand Things')
        self.screenshot()
        if not self.appear(self.I_TT_TICKET_BULE) and not self.appear(self.I_TT_BLACK) and not self.appear(self.I_TT_AP):
            time.sleep(1)
        if con.mystery_amulet:
            self.tt_buy_mystery_amulet()
        if con.black_daruma_fragment:
            self.tt_buy_black_daruma_scrap()
        if con.ap:
            self.tt_buy_ap()
        while 1:
            self.screenshot()
            if self.appear(self.I_TT_ENTER):
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
        logger.info('Exit Thousand Things')


    def tt_buy_mystery_amulet(self) -> bool:
        """

        :return: 成功购买返回True，找不到或者是钱不够返回False
        """
        self.screenshot()
        if not self.appear(self.I_TT_TICKET_BULE):
            logger.info('No mystery amulet')
            return False
        if not self.tt_check_money(2000):
            return False
        while 1:
            self.screenshot()
            if self.appear(self.I_TT_BUY_UP):
                break
            if self.ocr_appear_click(self.O_TT_BLUE_TICKET, interval=1):
                continue
        logger.info('Buy mystery amulet')
        if not self.tt_get_reward(self.I_TT_BUY_CONFIRM):
            return False
        logger.info('Buy mystery amulet success')
        time.sleep(0.5)
        return True

    def tt_buy_black_daruma_scrap(self):
        self.screenshot()
        if not self.appear(self.I_TT_BLACK):
            logger.info('No black daruma scrap')
            return False
        if not self.tt_check_money(350):
            return False
        while 1:
            self.screenshot()
            if self.appear(self.I_TT_BUY_UP):
                break
            if self.ocr_appear_click(self.O_TT_BLACK, interval=1):
                continue
        logger.info('Buy black daruma scrap')
        if not self.tt_get_reward(self.I_TT_BUY_CONFIRM):
            return False
        logger.info('Buy black daruma scrap success')
        time.sleep(0.5)
        return True

    def tt_buy_ap(self):
        self.screenshot()
        if not self.appear(self.I_TT_AP):
            logger.info('No ap')
            return False
        if not self.tt_check_money(600):
            return False
        maximum_clicked = False
        verified_twos = 0
        for _ in range(30):
            self.screenshot()
            # Never read a number from the shop or an item-description overlay.
            if not self._tt_buy_dialog_open():
                verified_twos = 0
                self.ocr_appear_click(self.O_TT_AP, interval=1)
                time.sleep(0.3)
                continue
            quantity = self._tt_read_purchase_quantity()
            verified_twos = verified_twos + 1 if quantity == 2 else 0
            if verified_twos >= 2:
                logger.info('Buy ap: quantity 2 verified on two screenshots')
                if not self.tt_get_reward(self.I_TT_BUY_CONFIRM):
                    return False
                logger.info('Buy ap success')
                time.sleep(0.5)
                return True
            if quantity == 1 and not maximum_clicked:
                self.click(self.I_TT_BUY_UP)
                maximum_clicked = True
            time.sleep(0.3)
        logger.warning('Buy ap failed: quantity 2 was not verified; skip this purchase')
        self._tt_close_buy_dialog()
        return False

    def _tt_buy_dialog_open(self):
        return self.appear(self.I_TT_BUY_CONFIRM) and self.appear(self.I_TT_BUY_UP)

    def _tt_read_purchase_quantity(self):
        from copy import copy
        # The dialog moves vertically with its description. Anchor to its max
        # button instead of the old fixed ROI, which cut off the number.
        x, y, _, _ = self.I_TT_BUY_UP.roi_front
        reader = copy(self.O_TT_NUMBER)
        reader.mode = reader.mode.__class__.SINGLE
        reader.roi = [x - 191, y + 7, 80, 42]
        reader.area = list(reader.roi)
        text = reader.ocr(self.device.image)
        # Single mode preserves empty/unknown text; Digit silently turns it to 0.
        if not isinstance(text, str) or text.strip() not in ('1', '2'):
            return None
        return int(text.strip())

    def _tt_close_buy_dialog(self):
        for _ in range(6):
            self.screenshot()
            if not (self.appear(self.I_TT_BUY_CONFIRM) or self.appear(self.I_TT_BUY_UP)):
                return True
            self.appear_then_click(self.I_UI_BACK_RED, interval=0.5)
            time.sleep(0.3)
        logger.warning('Purchase dialog did not close; continue recovery through the shop exit')
        return False

    def tt_check_money(self, mix: int) -> bool:
        self.screenshot()
        current = self.O_S_TOTAL.ocr(self.device.image)
        if not isinstance(current, int):
            logger.warning('OCR current money failed')
            return False
        if current >= mix:
            logger.info('Money is enough')
            return True
        logger.info('Money is not enough')
        return False

    def tt_get_reward(self, image_button):
        deadline = time.monotonic() + 18
        click_count = 0
        reward_seen = False
        for _ in range(45):
            self.screenshot()
            if time.monotonic() >= deadline:
                break
            if self.ui_reward_appear_click():
                reward_seen = True
                time.sleep(0.5)
                continue
            if reward_seen and not self.appear(self.I_UI_REWARD, threshold=0.6):
                logger.info('Get reward success')
                return True
            if not reward_seen and click_count < 3 and self._tt_buy_dialog_open():
                if self.appear_then_click(image_button, interval=2):
                    click_count += 1
            time.sleep(0.3)
        logger.warning('Purchase reward was not confirmed; stop waiting for this item')
        self._tt_close_buy_dialog()
        return False

    def _tt_prepare_borrow(self):
        pending = None
        previous_empty = 3
        unchanged = 0
        added = False
        replacing = False
        replace_clicked = False
        confirm_clicked = False
        for _ in range(40):
            self.screenshot()
            if replacing and self.appear(self.I_UI_CONFIRM):
                if not confirm_clicked:
                    self.click(self.I_UI_CONFIRM)
                    confirm_clicked = True
                time.sleep(0.5)
                continue
            if self.appear(self.I_TT_SHIKIGAMI_REPLACE):
                replacing = True
                if not replace_clicked:
                    self.click(self.I_TT_SHIKIGAMI_REPLACE)
                    replace_clicked = True
                time.sleep(0.5)
                continue
            if not self.appear(self.I_TT_CONFIRM):
                time.sleep(0.5)
                continue
            cards = borrow_cards(self.device.image)
            selected = sum(card[2] for card in cards)
            empty = empty_borrow_slots(self.device.image)
            logger.info(f'借出式神识别：可见{len(cards)}张，已选{selected}张，空位{empty}')
            if empty is None:
                logger.warning('借出空位识别不可用，结束本次配置')
                return False
            if replacing:
                # A replacement must return to the roster before confirming it.
                return replace_clicked and bool(cards)
            if pending is not None:
                if empty < previous_empty:
                    added = True
                    pending = None
                    unchanged = 0
                else:
                    unchanged += 1
                    if unchanged >= 8:
                        logger.warning('Borrow selection did not change; stop clicking this card')
                        return False
                    time.sleep(0.5)
                    continue
            if added and empty == 0:
                return True
            candidates = [(x, y) for x, y, chosen in cards if not chosen]
            if not candidates:
                return empty == 0 and selected > 0
            pending = candidates[0]
            previous_empty = empty
            self.device.click(*pending, control_name='TT_BORROW_UNSELECTED_CARD')
            time.sleep(0.5)
        logger.warning('Borrow selection timed out; stop this attempt')
        return False

    def _tt_leave_borrow(self):
        for _ in range(10):
            self.screenshot()
            if self.appear(self.I_TT_ENTER):
                return
            if borrow_only_page(self.device.image):
                self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1)
            else:
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
            time.sleep(0.5)
        raise TaskEnd('Unable to leave borrowing screen safely')

    def tt_earn_money(self):

        # 进入界面
        for _ in range(20):
            self.screenshot()
            if borrow_only_page(self.device.image):
                self._tt_leave_borrow()
                return False
            if self.appear(self.I_TT_CONFIGURE):
                break
            self.appear_then_click(self.I_TT_BORROW, interval=1)
            time.sleep(0.5)
        else:
            logger.warning('Lending configuration unavailable; skip this attempt')
            self._tt_leave_borrow()
            return False
        self.ui_click(self.I_TT_CONFIGURE, self.I_TT_CONFIRM)
        # Empty slots accept cards directly; a full roster opens replacement UI.
        if not self._tt_prepare_borrow():
            logger.warning('Skip lending setup; continue purchases after leaving the roster')
            self._tt_leave_borrow()
            return False
        self.ui_click(self.I_TT_CONFIRM, self.I_TT_BORROW_CONFIRM)

        # 获取奖励
        self.ui_get_reward(self.I_TT_BORROW_CONFIRM)

        # 退出界面
        self.ui_click(self.I_UI_BACK_RED, self.I_TT_ENTER)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ThousandThings(c, d)

    t.execute_tt(t.config.rich_man.thousand_things)




