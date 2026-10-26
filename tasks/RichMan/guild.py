# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re

from module.logger import logger
from module.atom.image import RuleImage

from tasks.GameUi.page import page_main, page_guild
from tasks.GameUi.game_ui import GameUi
from tasks.Component.Buy.buy import Buy
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.config import GuildStore


class ButtonException(Exception):
    pass


class Guild(Buy, GameUi, RichManAssets):

    def execute_guild(self, con: GuildStore=None):

        if not con.enable:
            return
        logger.hr('Start guild', 1)
        self.screenshot()
        if not self.appear(self.I_GUILD_CLOSE_RED):
            self.goto_page(page_guild)
        while 1:
            self.screenshot()
            if self.appear(self.I_GUILD_CLOSE_RED):
                break
            if self.appear_then_click(self.I_GUILD_SHRINE, interval=0.8):
                continue
            if self.appear_then_click(self.I_GUILD_STORE, interval=1.1):
                continue
        logger.info('Enter guild store success')
        time.sleep(0.5)
        # 对应的图片, 对应的函数
        # 这里的顺序就是，在游戏中出现的顺序
        items = [
            *([(self.I_GUILD_HONOR_GIFT, self._guild_honor_gift)] if getattr(con, "honor_gift", False) else []),
            *([(self.I_GUILD_BLUE, self._guild_mystery_amulet)] if con.mystery_amulet else []),
            *([(self.I_GUILD_SCRAP, self._guild_black_daruma_scrap)] if con.black_daruma_scrap else []),
            *([(self.I_GUILD_SKIN, lambda: self._guild_skin_ticket(con.skin_ticket))] if con.skin_ticket else []),
        ]
        items_all_done = not items
        swipe_no_progress = 0
        while items:
            self.screenshot()
            bought_this_round = False
            for item in items:
                button, func = item
                if not self.appear(button):
                    continue
                self.wait_until_stable(button)
                try:
                    result = func()
                    logger.info(f'功勋商店商品处理：{button.name}，购买结果={result}')
                    # 如果这里是最后一个，就是代表前面的都购买完成了
                    if len(items) == 1:
                        items_all_done = True
                    items.remove(item)
                    bought_this_round = True
                    swipe_no_progress = 0
                except ButtonException as e:
                    logger.warning(f'Button {button.name} click failed')
                break
            if items_all_done:
                logger.info('功勋商店配置商品已逐项处理')
                break
            # 刚购买成功: 先等待界面稳定并重新识别, 视野内还有商品就继续买, 不要急着滑动
            if bought_this_round:
                time.sleep(1)
                continue
            # BaseTask.swipe returns None even when it swipes. Count actual
            # unconditional gestures and always wait for the page to settle.
            if swipe_no_progress >= 8:
                logger.warning(f'功勋商店实际滑动8次仍未识别：{[item[0].name for item in items]}')
                break
            self.swipe(self.S_GUILD_STORE)
            swipe_no_progress += 1
            logger.info(f'功勋商店寻找商品：实际滑动{swipe_no_progress}/8')
            time.sleep(2)



        # 回去
        while 1:
            self.screenshot()
            if self.appear(self.I_GUILD_SHRINE):
                break
            if self.appear_then_click(self.I_GUILD_CLOSE_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue

    def _guild_buy_more(self, button, number):
        """Guild-only exact quantity confirmation; never accept a low-score digit."""
        self.screenshot()
        if not self.appear(button):
            return False
        self.click(button)
        deadline = time.monotonic() + 25
        previous = None
        stable = 0
        while time.monotonic() < deadline:
            time.sleep(0.4)
            self.screenshot()
            if not self.appear(self.I_BUY_PLUS):
                continue
            ocr = self.O_BUY_NUMBER
            crop = ocr.pre_process(ocr.crop(self.device.image, ocr.roi))
            text, score = ocr.model.ocr_single_line(crop)
            value = int(text) if score >= 0.85 and re.fullmatch(r'[1-9][0-9]*', text) else None
            if value is None:
                previous, stable = None, 0
                continue
            stable = stable + 1 if value == previous else 1
            previous = value
            if stable < 2:
                continue
            if value == number:
                logger.info(f'功勋商店购买数量双帧确认：{value}')
                self.click(self.C_BUY_MORE)
                end = time.monotonic() + 10
                while time.monotonic() < end:
                    self.screenshot()
                    if self.ui_reward_appear_click():
                        time.sleep(0.5)
                        return True
                    time.sleep(0.3)
                raise RuntimeError('功勋商店购买提交后未确认奖励，停止以避免重复购买')
            adjust = self.I_BUY_ADD if value < number else self.I_BUY_SUB
            if not self.appear(adjust):
                break
            self.click(adjust)
            previous, stable = None, 0
        self.click(self.C_BUY_CANCEL)
        logger.warning('功勋商店数量未确认，取消本次购买')
        return False

    def _guild_honor_gift(self):
        logger.hr('Guild honor gift', 2)
        self.screenshot()
        if not self.buy_check_money(self.O_GUILD_TOTAL, 210):
            return False
        if self.check_remain(self.I_GUILD_HONOR_GIFT) <= 0:
            logger.info('No guild honor gift available')
            return False
        return self._guild_buy_more(self.I_GUILD_HONOR_GIFT, 1)

    def _guild_mystery_amulet(self):
        # 蓝票
        logger.hr('Guild mystery amulet', 2)
        self.screenshot()
        if not self.buy_check_money(self.O_GUILD_TOTAL, 240):
            return False
        number = self.check_remain(self.I_GUILD_BLUE)
        if number == 0:
            logger.warning('No mystery amulet can buy')
            return False
        return self._guild_buy_more(self.I_GUILD_BLUE, number)

    def _guild_black_daruma_scrap(self):
        # 黑碎
        logger.hr('Guild black daruma scrap', 2)
        self.screenshot()
        if not self.buy_check_money(self.O_GUILD_TOTAL, 200):
            return False
        number = self.check_remain(self.I_GUILD_SCRAP)
        if number == 0:
            logger.warning('No black daruma can buy')
            return False
        self.buy_one(self.I_GUILD_SCRAP, self.I_GUILD_CHECK_SCRAP)
        time.sleep(0.5)
        return True

    def _guild_skin_ticket(self, num: int = 0):
        # 皮肤券
        logger.hr('Guild skin ticket', 2)
        if num == 0:
            logger.warning('No buy skin ticket')
            return False
        self.screenshot()
        if not self.buy_check_money(self.O_GUILD_TOTAL, 50):
            return False
        # 检查功勋商店皮肤券 本周剩余数量
        number = self.check_remain(self.I_GUILD_SKIN)
        if number == 0:
            logger.warning('No skin ticket can buy')
            return False
        # 购买功勋商店皮肤券
        return self._guild_buy_more(self.I_GUILD_SKIN, min(num, number))

    def check_remain(self, image: RuleImage) -> int:
        if not self.appear(image):
            logger.warning(f'Image {image.name} not appear')
            return 0
        self.O_GUILD_REMAIN.roi[0] = image.roi_front[0] - 38
        self.O_GUILD_REMAIN.roi[1] = image.roi_front[1] + 83
        logger.info(f'Image roi {image.roi_front}')
        logger.info(f'Image roi {self.O_GUILD_REMAIN.roi}')
        self.screenshot()
        result = self.O_GUILD_REMAIN.ocr(self.device.image)
        logger.warning(result)
        result = result.replace('？', '2').replace('?', '2').replace(':', '；')
        if re.search(r'[1-9]\d*级寮解锁', ''.join(result.split())):
            logger.info(f'Guild product {image.name} is locked: {result}; skip')
            return 0
        if '本周剩余数量' not in result:
            raise ButtonException(f'Image {image.name} ocr result is empty: {result}')
        try:
            result = re.findall(r'本周剩余数量(\d+)', result)[0]
            result = int(result)
        except:
            result = 0
        logger.info('Remain: %s' % result)
        return int(result)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = Guild(c, d)

    # t._guild_skin_ticket(5)
    t.execute_guild(con=c.rich_man.guild_store)


