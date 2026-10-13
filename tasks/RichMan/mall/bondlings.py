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
        self._enter_bondlings()

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

        # Resolve the current column by product name; sold-out products can
        # move to the right, so old exchange-button coordinates are unsafe.
        located = self._locate_bondling_product(check_class)
        if located is None:
            logger.info('Requested contract shop product is sold out')
            return
        buy_button, _remain = located
        if _remain == 0:
            logger.warning('本周契灵御魂已经买光')
            return
        if _remain < buy_number:
            logger.warning(f'Remain number is {_remain}, buy number is {buy_number}')
            buy_number = _remain
        # 检查金钱
        cu, re, total = self.O_BL_CHECK_MONEY.ocr(self.device.image)
        if cu + re != total:
            logger.warning('Check money is error')
            logger.warning(f'cu: {cu}, re: {re}, total: {total}')
            return
        money_enough = cu >= buy_money * buy_number
        if not money_enough:
            logger.warning(f'Money is not enough {cu}')
            # 判断够不够买2个
            if cu < buy_money * 2:
                logger.warning('Money is not enough 2')
                return
            buy_number = cu // buy_money
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
        texts = [r.ocr_text for r in stock.detect_and_ocr(self.device.image)]
        if '售罄' in ''.join(texts):
            return None
        numbers = [int(n) for t in texts for n in re.findall(r'[0-9]+', t)]
        if len(numbers) != 1 or numbers[0] <= 0:
            raise GameStuckError('Contract shop stock not confirmed; purchase blocked')
        button_roi = (center+45,475,70,35)
        return RuleClick(roi_front=button_roi,roi_back=button_roi,
                         name='verified_bondling_product'), numbers[0]

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
        while not timer.reached():
            self.screenshot()
            self._verify_bondling_item(check_class)
            text = str(reader.ocr(self.device.image)).strip()
            if not text.isascii() or not text.isdigit() or int(text) <= 0:
                raise GameStuckError('Contract shop quantity unreadable; purchase blocked')
            current = int(text)
            if current == number:
                confirmed += 1
                if confirmed >= 2:
                    break
            else:
                confirmed = 0
                control = self.I_BUY_ADD if current < number else self.I_BUY_SUB
                self.appear_then_click(control, interval=0.8)
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


