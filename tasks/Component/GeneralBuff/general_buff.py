# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import unicodedata
from copy import deepcopy

import cv2
import numpy as np

from tasks.Component.GeneralBuff.assets import GeneralBuffAssets
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from tasks.base_task import BaseTask
from module.logger import logger


class GeneralBuff(BaseTask, GeneralBuffAssets):

    def open_buff(self):
        """
        打开buff的总界面
        :return:
        """
        logger.info('Open buff')
        while 1:
            self.screenshot()
            if self.appear(self.I_CLOUD):
                break
            if self.appear_then_click(self.I_BUFF_1, interval=2):
                continue

        self._locate_buff_top()

    def _locate_buff_top(self):
        """Return to the awakening row in one direction, with bounded retries."""
        for attempt in range(4):
            self.screenshot()
            if not self.appear(self.I_CLOUD):
                return False
            if self.appear(self.I_AWAKE):
                time.sleep(0.5)
                return True
            if attempt < 3:
                self.swipe(self.S_BUFF_UP, interval=0)
                time.sleep(1)
        logger.warning('未找到觉醒加成定位基准，本次跳过金币加成操作')
        return False

    def close_buff(self):
        """
        关闭buff的总界面, 但是要确保buff界面已经打开了
        :return:
        """
        logger.info('Close buff')
        while 1:
            self.screenshot()
            if not self.appear(self.I_CLOUD):
                break
            if self.appear_then_click(self.I_BUFF_1, interval=2):
                continue

    def get_area(self, buff: RuleOcr) -> tuple:
        """
        获取要点击的开关buff的区域
        :param cls:
        :param image:
        :param buff:
        :return:  如果没有就返回None
        """
        # 防止邀请框挡住BUFF框架
        self.reject_invite()
        self.screenshot()
        area = buff.ocr(self.device.image)
        if area == tuple([432.0, 143.0, 325.0, 21.0]):
            logger.info(f'No {buff.name} buff')
            return None

        # 开始的x坐标就是文字的右边
        start_x = area[0] + area[2] + 10  # 10是文字和开关之间的间隔
        start_y = area[1] - 10
        width = 80  # 开关的宽度 80够了
        height = area[3] + 20
        return int(start_x), int(start_y), int(width), int(height)

    def set_switch_area(self, area):
        """
        设置开关的区域
        :param area:
        :return:
        """
        self.I_OPEN_YELLOW.roi_back = list(area)  # 动态设置roi
        self.I_CLOSE_RED.roi_back = list(area)

    @staticmethod
    def _gold_row_area(buff, results):
        # Compare complete individual rows, allowing only harmless formatting.
        def normalize(text):
            return ''.join(unicodedata.normalize('NFKC', text).split()).rstrip('。.')
        matches = [r for r in results if normalize(r.ocr_text) == normalize(buff.keyword)]
        if len(matches) != 1:
            return None
        box = matches[0].box
        x = float(box[:, 0].min()) + buff.roi[0]
        y = float(box[:, 1].min()) + buff.roi[1]
        w = float(box[:, 0].max() - box[:, 0].min())
        h = float(box[:, 1].max() - box[:, 1].min())
        # A clipped or multiline OCR box is not safe for locating its switch.
        if not (150 <= w <= buff.roi[2] and 10 <= h <= 32
                and buff.roi[1] + 3 <= y
                and y + h <= buff.roi[1] + buff.roi[3] - 3):
            return None
        left = int(x + w + 10)
        if not 700 <= left <= 790:
            return None
        return left, int(y - 8), 80, int(h + 16)

    def _set_gold_buff(self, buff, is_open):
        # Each snapshot must prove both row identity and switch state.
        opened, closed = deepcopy(self.I_OPEN_YELLOW), deepcopy(self.I_CLOSE_RED)
        clicks = stable = misses = 0
        last_y = None
        deadline = time.monotonic() + 40
        next_click = 0
        self.reject_invite()
        if not self._locate_buff_top():
            return False
        for _ in range(32):
            if time.monotonic() >= deadline:
                break
            self.screenshot()
            if not self.appear(self.I_CLOUD):
                logger.warning(f'{buff.name}: 加成面板已关闭，停止操作')
                return False
            rows = buff.detect_and_ocr(self.device.image)
            area = self._gold_row_area(buff, rows)
            if area is None:
                stable = 0
                misses += 1
                # Two fresh frames allow rendering to settle; absent rows are
                # skipped without searching elsewhere or touching other buffs.
                if misses >= 2:
                    logger.info(f'{buff.name}: 当前未识别到金币加成，跳过')
                    return False
                time.sleep(0.3)
                continue
            misses = 0
            opened.roi_back = closed.roi_back = area
            on, off = self.appear(opened), self.appear(closed)
            if on == off:
                stable = 0
                time.sleep(0.3)
                continue
            if on == is_open:
                stable = stable + 1 if last_y is not None and abs(area[1] - last_y) <= 3 else 1
                last_y = area[1]
                if stable >= 2:
                    logger.info(f'{buff.name}: 已核验金币加成{"开启" if is_open else "关闭"}')
                    return True
            else:
                stable = 0
                last_y = None
                if time.monotonic() >= next_click:
                    if clicks >= 3:
                        break
                    # Match and click use this same frame and this row only.
                    self.click(closed if is_open else opened)
                    clicks += 1
                    next_click = time.monotonic() + 2
            time.sleep(0.35)
        logger.warning(f'{buff.name}: 金币加成{"开启" if is_open else "关闭"}未确认；查找/操作已达上限')
        return False

    def gold_50(self, is_open: bool = True) -> bool:
        return self._set_gold_buff(self.O_GOLD_50, is_open)

    def gold_100(self, is_open: bool = True) -> bool:
        return self._set_gold_buff(self.O_GOLD_100, is_open)

    def exp_50(self, is_open: bool = True):
        """
        经验50buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} exp 50 buff')
        while 1:
            self.screenshot()
            area = self.get_area(self.O_EXP_50)
            if not area:
                logger.warning('No exp 50 buff')
                continue
            self.set_switch_area(area)

            if not self.appear(self.I_OPEN_YELLOW) and not self.appear(self.I_CLOSE_RED):
                self.device.swipe(p2=(530, 240), p1=(580, 320))
                time.sleep(1)
            else:
                break

        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open exp 50 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close exp 50 buff failed')

    def exp_100(self, is_open: bool = True):
        """
        经验100buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} exp 100 buff')
        while 1:
            self.screenshot()
            area = self.get_area(self.O_EXP_100)
            if not area:
                logger.warning('No exp 100 buff')
                continue
            self.set_switch_area(area)

            if not self.appear(self.I_OPEN_YELLOW) and not self.appear(self.I_CLOSE_RED):
                self.device.swipe(p2=(530, 240), p1=(580, 320))
                time.sleep(1)
            else:
                break

        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open exp 100 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close exp 100 buff failed')

    def get_area_image(self, target: RuleImage) -> list:
        """
        获取觉醒加成或者是御魂加成所要点击的区域
        因为实在的图片比ocr快
        :param image:
        :param target:
        :return:
        """
        self.reject_invite()
        self.screenshot()

        if not target.match(self.device.image):
            logger.warning(f'No {target.name} buff')
            return None
            # logger.info(f'front area: {target.roi_front}')
            # logger.info(f'front center: {target.front_center()}')
        start_x = int(target.front_center()[0] + 364)
        start_y = int(target.roi_front[1])
        width = 80
        height = int(target.roi_front[3])
        return [start_x, start_y, width, height]

    def awake(self, is_open: bool = True):
        """
        觉醒buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} awake buff')
        self.screenshot()
        area = self.get_area_image(self.I_AWAKE)
        if not area:
            logger.warning('No awake buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open awake buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close awake buff failed')

    def soul(self, is_open: bool = True):
        """
        御魂buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} soul buff')
        self.screenshot()
        area = self.get_area_image(self.I_SOUL)
        if not area:
            logger.warning('No soul buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open soul buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close soul buff failed')

    def reject_invite(self):
        from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
        while 1:
            self.screenshot()
            if not (self.appear(gia.I_I_REJECT_1) or self.appear(gia.I_I_REJECT_2) or self.appear(gia.I_I_REJECT_3)):
                break
            if self.appear(gia.I_I_REJECT_3):
                self.click(gia.I_I_REJECT_3, 6)
                continue
            if self.appear(gia.I_I_REJECT_2):
                self.click(gia.I_I_REJECT_2, 6)
                continue
            if self.appear(gia.I_I_REJECT_1):
                self.click(gia.I_I_REJECT_1, 6)
                continue


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = GeneralBuff(c, d)

    t.open_buff()
    # t.screenshot()
    #
    t.awake(is_open=True)
    t.soul(is_open=True)
    t.gold_50(is_open=False)
    t.gold_100(is_open=False)
    t.exp_50(is_open=True)
    t.exp_100(is_open=True)
