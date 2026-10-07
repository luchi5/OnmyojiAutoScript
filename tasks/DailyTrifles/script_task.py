# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import copy
import json
import os
from pathlib import Path
from time import sleep
from datetime import time, datetime, timedelta

from exceptiongroup import catch
from tasks.DailyTrifles.page import page_store_gift_room
from winerror import NOERROR

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_summon, page_guild, page_mall, page_friends
from tasks.DailyTrifles.config import DailyTriflesConfig
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.Component.Summon.summon import Summon

from module.logger import logger
from module.exception import TaskEnd, RequestHumanTakeover
from module.base.timer import Timer
from module.atom.image import RuleImage
from filelock import FileLock
from tasks.DailyTrifles.config import SummonType
import re

class SushiPurchaseSkipped(Exception):
    """Skip only the purchase; retain ambiguous claims and keep scheduling."""


class ScriptTask(GameUi, Summon, DailyTriflesAssets):

    def run(self):
        con = self.config.daily_trifles.trifles_config
        # 每日召唤
        if con.one_summon:
            self.run_one_summon()
        if con.guild_wish:
            pass
        # 友情点
        if con.friend_love:
            self.run_friend_love()
        # 吉闻
        if con.luck_msg:
            self.run_luck_msg()
        # 商店签到 or 购买寿司
        if con.store_sign or con.buy_sushi_count > 0:
            self.run_store()
        self.set_next_run('DailyTrifles', success=True, finish=False)
        raise TaskEnd('DailyTrifles')

    def run_one_summon(self):
        self.goto_page(page_summon)
        config=self.config.daily_trifles.trifles_config
        if config.summon_type == SummonType.default:
            self.summon_one(draw_mystery_pattern=config.draw_mystery_pattern)
            self.check_time()
        elif config.summon_type == SummonType.recall:
            self.summon_recall()
        self.back_summon_main()

    def check_time(self):
        config = self.config.daily_trifles.trifles_config
        now = datetime.now()
        next_run = now + self.config.daily_trifles.scheduler.success_interval
        # 检查是否跨月（next_run的月份与当前月份不同）
        if next_run.month != now.month:
            # 跨月重置神秘图案触发状态
            if not config.draw_mystery_pattern:
                config.draw_mystery_pattern = True
                logger.info(
                    f"reset draw_mystery_pattern to True, next_run: {next_run}")
        else:
            # 如果还是在同一月份，则没必要再绘制神秘图案
            config.draw_mystery_pattern = False
        self.config.save()

    def summon_recall(self):
        """
        确保在召唤界面,每日召唤一次
        召唤结束后回到 召唤主界面
        :return:
        """
        list = [self.O_SELECT_SM2, self.O_SELECT_SM3, self.O_SELECT_SM4]
        count = 0
        while True:
            count += 1

            for i in range(len(list)):
                sleep(1)
                self.goto_page(page_main)
                self.goto_page(page_summon)
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
                x, y = list[i].coord()
                self.device.click(x, y)
                sleep(1)
                self.screenshot()
                if self.appear(self.I_RECALL_TICKET):
                    break
                logger.info("Select preset group RECALL")

            self.screenshot()
            if self.appear(self.I_RECALL_TICKET):
                break
            if count >= 3:
                self.config.notifier.push(title='今忆召唤抽卡失败', content='每日任务,今忆召唤抽卡失败!!!')
                return

        logger.info('Summon one RECALL')
        self.wait_until_appear(self.I_RECALL_TICKET)
        while True:
            ticket_info = self.O_RECALL_TICKET_AREA.ocr(self.device.image)
            # 处理 None 和空字符串
            if ticket_info is None or ticket_info == '':
                ticket_info = 0
            else:
                # 使用正则表达式提取字符串中的数字
                match = re.search(r'\d+', ticket_info)
                if match:
                    ticket_info = int(match.group())
                else:
                    logger.warning(f'Invalid ticket_info value: {ticket_info}, expected a numeric string')
                    ticket_info = 0  # 将无效值设置为默认值 0
            if ticket_info <= 0:
                logger.warning('There is no any one RECALL ticket')
                return
            # 某些情况下滑动异常
            self.S_RANDOM_SWIPE_1.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_2.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_3.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_4.name = 'S_RANDOM_SWIPE'
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_ONE_TICKET):
                    break
                if self.appear_then_click(self.I_RECALL_TICKET, interval=1):
                    continue

            # 画一张票
            sleep(1)
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_SM_CONFIRM, interval=0.6):
                    self.ui_click_until_disappear(self.I_RECALL_SM_CONFIRM)
                    break
                if self.appear(self.I_SM_CONFIRM_2, interval=0.6):
                    self.ui_click_until_disappear(self.I_SM_CONFIRM_2)
                    break
                if self.appear(self.I_RECALL_ONE_TICKET, interval=1):
                    # 某些时候会点击到 “语言召唤”
                    if self.appear_then_click(self.I_UI_CANCEL, interval=0.8):
                        continue
                    self.summon()
                    continue
            logger.info('Summon one success')

    def run_guild_wish(self):
        pass

    def run_luck_msg(self):
        self.goto_page(page_friends)
        while 1:
            self.screenshot()
            if self.appear(self.I_LUCK_TITLE):
                break
            if self.appear_then_click(self.I_FRIENDSHIP_UP, interval=1):
                continue
            if self.ocr_appear_click(self.O_LUCK_MSG, interval=1):
                continue
        logger.info('Start luck msg')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_CLICK_BLESS, interval=1):
                continue
            if self.appear_then_click(self.I_ONE_CLICK_BLESS, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of luck msg')
                break
            if check_timer.reached():
                logger.warning('There is no any luck msg')
                break

        self.ui_click(self.I_UI_BACK_RED, self.I_CHECK_MAIN)

    def run_friend_love(self):
        self.goto_page(page_friends)
        while 1:
            self.screenshot()
            if self.appear(self.I_L_LOVE):
                break
            if self.appear_then_click(self.I_FRIENDSHIP_UP, interval=1):
                continue
            if self.appear_then_click(self.I_L_FRIENDS, interval=1):
                continue
            if self.appear_then_click(self.I_FRIEND_TAB, interval=3):
                continue
        logger.info('Start friend love')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_L_COLLECT, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of friend love')
                break
            if check_timer.reached():
                logger.warning('There is no any love')
                break

        self.ui_click(self.I_UI_BACK_RED, self.I_CHECK_MAIN)

    def run_store(self):
        self.goto_page(page_mall, confirm_wait=3)

        if self.config.daily_trifles.trifles_config.store_sign:
            self.run_store_sign()
        if self.config.daily_trifles.trifles_config.buy_sushi_count > 0:
            self.run_buy_sushi()

        self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_MALL, interval=2.4, timeout=8)
        self.goto_page(page_main)

    def run_store_sign(self):

        self.goto_page(page_store_gift_room)
        self.screenshot()
        self.appear_then_click(self.I_GIFT_RECOMMEND, interval=1)
        logger.info('Enter store sign')
        sleep(1)  # 等个动画
        self.screenshot()
        if not self.appear(self.I_GIFT_SIGN):
            logger.warning('There is no gift sign')
            return

        if self.ui_get_reward(self.I_GIFT_SIGN, click_interval=2.5):
            logger.info('Get reward of gift sign')

    def _sushi_price_info(self, text):
        if not isinstance(text, str):
            return None
        text = re.sub(r'\s+', '', text)
        if not re.fullmatch(r'[1-9][0-9]{1,2}', text):
            return None
        price = int(text)
        if price < 60 or (price - 60) % 20:
            return None
        return (price - 60) // 20, price

    def _read_sushi_price(self, element):
        x, y, width, height = element.roi_front
        reader = copy.copy(self.O_STORE_SUSHI_PRICE)
        reader.roi = (x + width, y + height - 30, 60, 30)
        reader.score = max(reader.score, 0.85)
        return self._sushi_price_info(reader.detect_text(self.device.image))

    def _read_sushi_quantity(self, confirmation):
        x, y, _, _ = confirmation.roi_front
        reader = copy.copy(self.O_STORE_SUSHI_PRICE)
        reader.roi = (x - 55, y - 100, 100, 55)
        reader.name = 'STORE_SUSHI_QUANTITY'
        reader.score = max(reader.score, 0.85)
        raw = reader.detect_text(self.device.image)
        value = raw.strip() if isinstance(raw, str) else ''
        return int(value) if re.fullmatch(r'[1-9][0-9]*', value) else None

    def _sushi_purchase_state_path(self):
        name = str(self.config.config_name)
        if not name or any(character in name for character in '/\\:\x00'):
            raise SushiPurchaseSkipped('无法安全保存体力购买记录，本次跳过购买')
        return Path(__file__).resolve().parents[2] / 'log' / 'purchase-state' / f'{name}-sushi.json'

    def _sushi_purchase_state(self, action='read', price=None, goal=None, expected=None):
        path = self._sushi_purchase_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Re-read and claim under the same lock: atomic replacement alone
            # would let two workers both submit the same purchase.
            with FileLock(str(path) + '.lock', timeout=0):
                previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
                if previous is not None and (
                    not isinstance(previous, dict) or previous.get('version') != 1
                    or previous.get('account') != str(self.config.config_name)
                    or previous.get('status') not in ('pending', 'confirmed')
                    or not isinstance(previous.get('price'), int) or isinstance(previous.get('price'), bool)
                    or self._sushi_price_info(str(previous.get('price'))) is None
                ):
                    raise SushiPurchaseSkipped('体力购买记录不完整，需要核对记录；本次不提交购买')
                today = datetime.now().date().isoformat()
                if previous:
                    recorded_date = previous.get('date')
                    if not isinstance(recorded_date, str) or len(recorded_date) != 10 or (
                        datetime.strptime(recorded_date, '%Y-%m-%d').date().isoformat() != recorded_date
                        or recorded_date > today
                    ):
                        raise SushiPurchaseSkipped('体力购买记录日期无效，需要核对记录；本次不提交购买')
                if action == 'read':
                    return previous
                if action == 'pending' and previous and previous['status'] == 'pending' and previous['date'] == today:
                    raise SushiPurchaseSkipped('已有待确认的体力购买，本日不重复提交')
                if action == 'pending' and previous and previous['status'] == 'pending' and previous['date'] < today and price != 60:
                    raise SushiPurchaseSkipped('跨日待确认记录仅在稳定识别到首购60勾玉时允许进入新一天购买')
                if action == 'pending' and previous != expected:
                    raise SushiPurchaseSkipped('购买记录已被其他任务更新，本次跳过，下次按最新记录核验')
                if action == 'pending' and previous and previous['date'] == today and price < previous['price']:
                    raise SushiPurchaseSkipped('今日记录和识别价格不一致，本次跳过，避免重复扣费')
                if action == 'confirmed' and (
                    not previous or previous['status'] != 'pending' or price <= previous['price']
                ):
                    raise SushiPurchaseSkipped('体力购买价格变化尚未证实，本次跳过重复购买')
                if action not in ('pending', 'confirmed') or self._sushi_price_info(str(price)) is None:
                    raise SushiPurchaseSkipped('体力购买记录参数无效，本次跳过购买')
                now = datetime.now().isoformat(timespec='seconds')
                record = {'version': 1, 'account': str(self.config.config_name),
                          'status': action, 'price': price, 'goal': goal,
                          'date': now[:10], 'updated_at': now}
                if action == 'pending':
                    record['submitted_at'] = now
                    if previous and previous['status'] == 'pending' and previous['date'] < today:
                        record['previous_attempt'] = {key: previous.get(key) for key in ('date', 'status', 'price', 'submitted_at')}
                else:
                    record['submitted_at'] = previous.get('submitted_at')
                    record['previous_price'] = previous['price']
                    if previous.get('previous_attempt'):
                        record['previous_attempt'] = previous['previous_attempt']
                temporary = path.with_suffix(f'.{os.getpid()}.tmp')
                with temporary.open('w', encoding='utf-8') as output:
                    json.dump(record, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                return record
        except (SushiPurchaseSkipped, RequestHumanTakeover):
            raise
        except Exception as error:
            raise SushiPurchaseSkipped('无法锁定或保存体力购买记录，本次跳过，避免重复扣费') from error

    def _close_sushi_confirmation(self, confirmation):
        close = copy.copy(self.C_UI_REWARD)
        close.roi_front = (910, 230, 180, 230)  # Outside the purchase card and its paid button.
        close.name = 'SUSHI_CONFIRMATION_CLOSE'
        timer = Timer(5).start()
        while not timer.reached():
            self.screenshot()
            if not self.appear(confirmation):
                return
            self.click(close, interval=1)
        raise SushiPurchaseSkipped('体力购买确认框暂时无法关闭，交给游戏恢复')

    def run_buy_sushi(self):
        try:
            return self._run_buy_sushi()
        except SushiPurchaseSkipped as error:
            logger.warning(f'体力购买本次跳过：{error}；本账号继续其他任务')
            # Dismiss the unsubmitted/uncertain purchase dialog without touching
            # its paid button. A takeover from Device must still stop the account.
            confirmation = copy.copy(self.I_STORE_COST_TYPE_JADE)
            confirmation.roi_front = list(confirmation.roi_front)
            confirmation.roi_back = (500, 480, 300, 170)
            try:
                self._close_sushi_confirmation(confirmation)
            except RequestHumanTakeover:
                raise
            except Exception as cleanup_error:
                logger.warning(f'购买页面暂时无法返回，跳过本次每日杂事并安排游戏恢复：{type(cleanup_error).__name__}')
                # Prevent Restart from immediately scheduling this unresolved
                # purchase again; subsequent tasks can continue after recovery.
                self.set_next_run('DailyTrifles', success=True, finish=False)
                self.config.task_call('Restart')
                raise TaskEnd('DailyTrifles: sushi skipped; recover game')
            return False

    def _run_buy_sushi(self):
        from tasks.RichMan.assets import RichManAssets

        goal = self.config.daily_trifles.trifles_config.buy_sushi_count
        if goal <= 0:
            return
        confirmation = copy.copy(self.I_STORE_COST_TYPE_JADE)
        confirmation.roi_front = list(confirmation.roi_front)
        confirmation.roi_back = (500, 480, 300, 170)
        purchase_title = RuleImage(
            roi_front=(588, 151, 109, 34), roi_back=(480, 100, 320, 160),
            threshold=0.9, method='Template matching',
            file=str(Path(__file__).resolve().parent / 'store' / 'store_sushi_purchase_title.png'),
        )
        item = copy.copy(self.I_SPECIAL_SUSHI)
        item.roi_front = list(item.roi_front)
        previous = self._sushi_purchase_state()
        navigation = Timer(15).start()
        try:
            while not navigation.reached():
                self.screenshot()
                if self.appear(confirmation) or self.appear(RichManAssets.I_SIDE_CHECK_SPECIAL):
                    break
                if self.appear_then_click(RichManAssets.I_MALL_SUNDRY, interval=1):
                    continue
                self.appear_then_click(RichManAssets.I_SIDE_SURE_SPECIAL, interval=1)
            else:
                raise SushiPurchaseSkipped('无法确认购买体力页面，本次跳过购买')
        except (SushiPurchaseSkipped, RequestHumanTakeover):
            raise
        except Exception as error:
            if previous and previous['status'] == 'pending':
                raise SushiPurchaseSkipped('购买结果待确认且页面连接中断，本次跳过') from error
            raise

        overall = Timer(30).start()
        pending_price = None
        pending_timer = None
        stable = None
        stable_frames = 0
        opened = False
        try:
            while not overall.reached():
                self.screenshot()
                if pending_timer and pending_timer.reached():
                    raise SushiPurchaseSkipped('已提交一次购买但结果未确认，保留记录，本日不再次提交')
                # Recognition and click cooldown are separate: never scan the
                # shop item through a reward overlay while its click is cooling.
                if self.appear(self.I_UI_REWARD, threshold=0.6):
                    self.ui_reward_appear_click()
                    continue
                in_confirmation = self.appear(confirmation)
                element = confirmation if in_confirmation else item
                if not in_confirmation and not self.appear(item):
                    stable = None; stable_frames = 0
                    continue
                if in_confirmation:
                    if not self.appear(purchase_title):
                        stable = None; stable_frames = 0
                        continue
                    quantity = self._read_sushi_quantity(confirmation)
                    if quantity is None:
                        stable = None; stable_frames = 0
                        continue
                    if quantity != 1:
                        raise SushiPurchaseSkipped('购买体力数量不是1，本次不提交购买')
                info = self._read_sushi_price(element)
                if info is None:
                    stable = None; stable_frames = 0
                    continue
                count, price = info
                sample = (in_confirmation, price)
                stable_frames = stable_frames + 1 if sample == stable else 1
                stable = sample
                if stable_frames < 2:
                    continue
                if previous and previous['status'] == 'pending' and previous['date'] == datetime.now().date().isoformat():
                    if price <= previous['price']:
                        raise SushiPurchaseSkipped('今天已有未确认购买，保留记录，本日不再次提交；下次按价格核验')
                    previous = self._sushi_purchase_state('confirmed', price, goal)
                    logger.info(f'体力购买已确认：下次价格{price}勾玉，今日已购买{count}次，目标{goal}次')
                if pending_price is not None:
                    if price != pending_price + 20:
                        if price == pending_price:
                            continue
                        raise SushiPurchaseSkipped('购买后的价格变化不符合预期，保留记录，本次跳过')
                    previous = self._sushi_purchase_state('confirmed', price, goal)
                    logger.info(f'体力购买已确认：下次价格{price}勾玉，今日已购买{count}次，目标{goal}次')
                    pending_price = None; pending_timer = None; opened = False
                if count >= goal:
                    if in_confirmation:
                        self._close_sushi_confirmation(confirmation)
                    logger.info(f'今日体力购买目标{goal}次，已购买{count}次，跳过继续购买')
                    return
                if goal == 1 and price != 60:
                    raise SushiPurchaseSkipped('只允许首次60勾玉购买，本次不提交其他价格')
                if in_confirmation:
                    self._sushi_purchase_state('pending', price, goal, expected=previous)
                    pending_price = price
                    pending_timer = Timer(10).start()
                    stable = None; stable_frames = 0
                    self.click(confirmation)  # A paid confirmation is submitted exactly once.
                    logger.info(f'已提交一次体力购买：{price}勾玉，等待价格变化确认；今日目标{goal}次')
                elif not opened:
                    self.click(item)
                    opened = True
                    stable = None; stable_frames = 0
            raise SushiPurchaseSkipped('无法稳定确认体力购买状态，本次跳过，避免重复扣费')
        except (SushiPurchaseSkipped, RequestHumanTakeover):
            raise
        except Exception as error:
            if pending_price is not None or previous and previous['status'] == 'pending':
                raise SushiPurchaseSkipped('提交购买后连接或识别中断，保留记录，本次跳过') from error
            raise


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run_friend_love()
