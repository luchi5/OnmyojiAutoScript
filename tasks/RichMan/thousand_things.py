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
            logger.info('Borrow-only newcomer/returning page: lending and Thousand Things unavailable')
            self._tt_leave_borrow()
            return
        self.goto_page(page_travel)

        self.screenshot()
        if beginner_travel(self.device.image):
            logger.info('Newcomer/returning travel area: skip lending and unavailable Thousand Things shop')
            return
        if con.earn_money:
            logger.info('Start Earn Money')
            self.tt_earn_money()
            self.screenshot()
            if beginner_travel(self.device.image):
                return

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
        self.tt_get_reward(self.I_TT_BUY_CONFIRM)
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
        self.tt_get_reward(self.I_TT_BUY_CONFIRM)
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
        self.O_TT_NUMBER.keyword = '2'
        click_count = 0
        while 1:
            self.screenshot()
            if self.ocr_appear(self.O_TT_NUMBER):
                break
            appear_max = self.appear(self.I_TT_BUY_UP)
            if click_count >= 4:
                logger.warning('Buy ap failed')
                break
            if self.appear_then_click(self.I_TT_BUY_UP, interval=0.5):
                click_count += 1
                continue
            if not appear_max and self.ocr_appear_click(self.O_TT_AP, interval=2.3):
                continue
        logger.info('Buy ap')
        self.tt_get_reward(self.I_TT_BUY_CONFIRM)
        logger.info('Buy ap success')
        time.sleep(0.5)
        return True

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
        from time import sleep
        click_count = 0
        while 1:
            self.screenshot()

            if click_count >= 6:
                logger.warning('Get reward failed')
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_TT_BUY_UP):
                        break
                    if self.appear_then_click(self.I_UI_BACK_RED, interval=3):
                        continue
                break

            if self.ui_reward_appear_click():
                sleep(0.5)
                while 1:
                    self.screenshot()
                    # 等待动画结束
                    if not self.appear(self.I_UI_REWARD, threshold=0.6):
                        logger.info('Get reward success')
                        break

                    # 一直点击
                    if self.ui_reward_appear_click():
                        continue
                break

            if self.appear_then_click(image_button, interval=2):
                click_count += 1
                continue
        return True

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




