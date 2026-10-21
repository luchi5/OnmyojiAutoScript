# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re

from module.logger import logger
from module.base.timer import Timer
from module.exception import GameStuckError
from module.atom.image import RuleImage
from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr

from tasks.GameUi.page import page_main, page_guild
from tasks.RichMan.mall.navbar import MallNavbar
from tasks.Component.Buy.buy import Buy
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.config import Bondlings as BondlingsConfig


class Bondlings(Buy, MallNavbar):

    def execute_bondlings(self, con: BondlingsConfig = None):
        if not con:
            con = self.config.rich_man.bondlings
        if not con.enable:
            logger.info('Bondlings is disable')
            return
        if not self._enter_bondlings():
            return

        # 购买石头
        self._bondlings_base(buy_button=self.I_BL_BUY_STONE, remain_number=self.O_BL_RES_STONE, check_class=2,
                             buy_number=con.bondling_stone, buy_max=10, buy_money=30)
        # 购买御魂
        self._bondlings_base(buy_button=self.I_BL_BUY_SOULS, remain_number=self.O_BL_RES_SOULS, check_class=1,
                             buy_number=con.random_soul, buy_max=25, buy_money=20)
        # 购买高级盘
        self._bondlings_base(buy_button=self.I_BL_BUY_HIGH, remain_number=self.O_BL_RES_HIGH, check_class=3,
                             buy_number=con.high_bondling_discs, buy_max=10, buy_money=50)
        # 购买中级盘
        self._bondlings_base(buy_button=self.I_BL_BUY_MEDIUM, remain_number=self.O_BL_RES_MEDIUM, check_class=4,
                             buy_number=con.medium_bondling_discs, buy_max=25, buy_money=20)

    def _bondlings_base(self, buy_button: RuleImage, remain_number: RuleOcr, check_class: int,
                       buy_number: int, buy_max: int, buy_money: int):
        """

        :param buy_button: 对应的兑换按钮
        :param remain_number: 对应的检查剩余数量的ocr
        :param check_money: 对应的检查剩余金钱的第一个
        :param buy_number: 购买多少
        :param buy_max: 这一个种类一次性最多购买多少
        :param buy_money: 每一个购买的钱数
        :return:
        """
        logger.hr(buy_button.name, 3)
        if buy_number == 0:
            logger.info('Buy number is 0')
            return
        self.screenshot()

        # Check funds before doing any product/stock OCR.
        cu, remainder, total = self.O_BL_CHECK_MONEY.ocr(self.device.image)
        if total <= 0 or min(cu, remainder) < 0 or cu + remainder != total:
            logger.warning('Contract shop balance unreadable; skip product')
            return
        if cu < buy_money:
            logger.info(f'Contract shop balance {cu} below unit price {buy_money}; skip product')
            return
        located = self._locate_bondling_product(check_class)
        if located is None:
            return
        buy_button, remaining = located
        buy_number = min(buy_number, remaining, cu // buy_money)
        if buy_number <= 0:
            return
        # 购买
        logger.info(f'Buy number is {buy_number}')
        if buy_number >= buy_max:
            buy_cycles_number = buy_number // buy_max
            buy_res_number = buy_number % buy_max
        else:
            buy_cycles_number = None
            buy_res_number = buy_number

        if buy_cycles_number:
            for i in range(buy_cycles_number):
                self._buy_bondling_checked(buy_button, buy_max, check_class)
                time.sleep(0.5)
        if buy_res_number:
            self._buy_bondling_checked(buy_button, buy_res_number, check_class)
            time.sleep(0.5)

    def _locate_bondling_product(self, check_class):
        for attempt in range(3):
            self.screenshot()
            try:
                return self._locate_bondling_product_once(check_class)
            except GameStuckError as exc:
                logger.warning(f'Contract shop product/stock unreadable ({attempt + 1}/3): {exc}')
                if attempt < 2:
                    time.sleep(0.3)
        logger.warning('Contract shop stock remains unknown; skip this product without purchase')
        return None

    def _locate_bondling_product_once(self, check_class):
        names = {1: {'随机御魂'}, 2: {'鸣契石'},
                 3: {'万象式盘', '高级契灵盘'}, 4: {'千咒式盘', '中级契灵盘'}}
        roi = (60,400,1110,70)
        reader = RuleOcr(roi=roi, area=roi, mode='Full', method='Default',
                         keyword='', name='bondling_shop_names')
        found = [r for r in reader.detect_and_ocr(self.device.image)
                 if ''.join(r.ocr_text.split()) in names[check_class]]
        if len(found) != 1:
            raise GameStuckError('Contract shop product location not confirmed')
        center = int((found[0].box[0][0] + found[0].box[1][0]) / 2 + roi[0])
        stock_roi = (max(0, center-140),175,280,155)
        for shade in ('dark','red'):
            sold = RuleImage(roi_front=(0,0,40,140), roi_back=stock_roi,
                             threshold=0.85, method='Template matching',
                             file='./tasks/RichMan/mall/bondlings/sold_'+shade+'.png')
            if self.appear(sold):
                return None
        stock = RuleOcr(roi=stock_roi, area=stock_roi, mode='Full', method='Default',
                        keyword='', name='bondling_product_stock')
        results = stock.detect_and_ocr(self.device.image)
        if '售罄' in ''.join(r.ocr_text for r in results):
            logger.info('Contract shop product sold out')
            return None
        # Anchor the small number crop to the vertical limit label, not the
        # product artwork (which can contain a question mark or stray digits).
        labels = [r for r in results if '每周限购' in ''.join(r.ocr_text.split())]
        if len(labels) != 1:
            raise GameStuckError('Contract shop limit label not confirmed')
        xs = [point[0] for point in labels[0].box]
        left, right = int(min(xs)) + stock_roi[0], int(max(xs)) + stock_roi[0]
        if not 8 <= right - left <= 45:
            raise GameStuckError('Contract shop limit label geometry invalid')
        digit_roi = (max(0, left-3), 280, right-left+6, 40)
        number_reader = RuleOcr(roi=digit_roi, area=digit_roi, mode='Single',
                                method='Default', keyword='', name='bondling_stock_digits')
        raw = str(number_reader.ocr(self.device.image)).strip()
        if not re.fullmatch(r'[0-9]{1,3}', raw):
            raise GameStuckError('Contract shop stock not confirmed')
        remaining = int(raw)
        if remaining > {1: 100, 2: 10, 3: 20, 4: 50}[check_class]:
            raise GameStuckError('Contract shop stock exceeds weekly limit')
        if remaining == 0:
            logger.info('Contract shop product has zero remaining stock')
            return None
        button_roi = (center+45,475,70,35)
        return RuleClick(roi_front=button_roi,roi_back=button_roi,
                         name='verified_bondling_product'), remaining

    def _verify_bondling_item(self, check_class):
        # Verify the dialog's item name, not identical exchange buttons on cards.
        names = {1: {'随机御魂'}, 2: {'鸣契石'},
                 3: {'万象式盘', '高级契灵盘'}, 4: {'千咒式盘', '中级契灵盘'}}
        reader = RuleOcr(roi=(390,130,500,220), area=(390,130,500,220),
                         mode='Full', method='Default', keyword='', name='bondling_purchase_item')
        results = reader.detect_and_ocr(self.device.image)
        texts = {''.join(r.ocr_text.split()) for r in results}
        if not (names[check_class] & texts):
            raise GameStuckError('Contract shop item not confirmed; purchase blocked: {}'.format(sorted(texts)))

    def _buy_bondling_checked(self, button, number, check_class):
        timer = Timer(10).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_BUY_PLUS):
                break
            if isinstance(button, RuleClick):
                self.click(button, interval=1)
            else:
                self.appear_then_click(button, interval=1)
        else:
            raise GameStuckError('Contract shop purchase dialog not found')
        self._verify_bondling_item(check_class)
        # Single mode preserves empty OCR; never interpret it as a quantity.
        reader = RuleOcr(roi=self.O_BUY_NUMBER.roi, area=self.O_BUY_NUMBER.roi,
                         mode='Single', method='Default', keyword='', name='bondling_purchase_number')
        timer = Timer(45).start()
        confirmed = 0
        maximum_attempted = False
        previous = None
        previous_control = None
        while not timer.reached():
            self.screenshot()
            self._verify_bondling_item(check_class)
            text = str(reader.ocr(self.device.image)).strip()
            if not text.isascii() or not text.isdigit() or int(text) <= 0:
                raise GameStuckError('Contract shop quantity unreadable; purchase blocked')
            current = int(text)
            if previous is not None and previous_control is not None:
                progressed = ((previous_control == self.I_BUY_ADD and current > previous)
                              or (previous_control == self.I_BUY_SUB and current < previous))
                if progressed:
                    self.device.click_record_remove(previous_control)
                    previous_control = None
            if current == number:
                confirmed += 1
                if confirmed >= 2:
                    break
            else:
                confirmed = 0
                if current < number and not maximum_attempted:
                    maximum_attempted = True
                    self.appear_then_click(self.I_BUY_PLUS, interval=0.8)
                    previous_control = None
                else:
                    control = self.I_BUY_ADD if current < number else self.I_BUY_SUB
                    if self.appear_then_click(control, interval=0.8):
                        previous_control = control
                        previous = current
            time.sleep(0.4)
        else:
            raise GameStuckError('Contract shop requested quantity not confirmed')
        # Item and quantity were both checked on this same frame. Submit once.
        self.click(self.C_BUY_MORE)
        timer = Timer(12).start()
        while not timer.reached():
            self.screenshot()
            if self.ui_reward_appear_click():
                logger.info('Verified contract shop purchase completed: class={}, count={}'.format(check_class, number))
                return True
        raise GameStuckError('Contract shop purchase result unknown; do not submit again')





if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = Bondlings(c, d)

    t.execute_bondlings()


