# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import random
import re
from cached_property import cached_property
from datetime import datetime, timedelta
from module.atom.click import RuleClick

from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.atom.image import RuleImage
from module.base.utils import point2str
from module.logger import logger
from module.exception import TaskEnd, GameStuckError

from tasks.KekkaiUtilize.script_task import ScriptTask as KU
from tasks.KekkaiUtilize.utils import CardClass
from tasks.KekkaiActivation.assets import KekkaiActivationAssets
from tasks.KekkaiActivation.utils import parse_rule
from tasks.KekkaiActivation.config import ActivationConfig
from tasks.Utils.config_enum import ShikigamiClass
from tasks.GameUi.page import page_main, page_guild
from tasks.KekkaiActivation.config import CardType
from tasks.KekkaiActivation.activation_button import ActivationButton

""" 结界挂卡 """
class ScriptTask(KU, KekkaiActivationAssets):

    @cached_property
    def I_A_ACTIVATE_YELLOW(self):
        return ActivationButton()

    def run(self):
        con = self.config.kekkai_activation.activation_config
        self.goto_page(page_guild)

        # 在寮的主界面 检查是否有收取体力或者是收取寮资金
        # self.check_guild_ap_or_assets()

        # 进入寮结界
        self.goto_realm()

        if con.exchange_before:
            if getattr(con, 'auto_fill', False):
                self.check_max_lv(con.shikigami_class, auto_fill=True)
            else:
                # KekkaiUtilize may still be cached with its pre-update method signature.
                self.check_max_lv(con.shikigami_class)
        # A card-type bubble means expired; a chest means still active.
        # run_activation checks the bubble before opening the card panel.
        # 开始挂卡
        self.run_activation(con)
        while 1:
            # 关闭到结界界面
            self.screenshot()
            if self.appear(self.I_REALM_SHIN):
                break
            if self.appear(self.I_SHI_GROWN):
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue

        if con.exchange_max:
            if getattr(con, 'auto_fill', False):
                self.check_max_lv(con.shikigami_class, auto_fill=True)
            else:
                self.check_max_lv(con.shikigami_class)
        # self.back_guild()
        self.goto_page(page_main)

        raise TaskEnd('KekkaiActivation')

    def check_max_lv(self, shikigami_class=ShikigamiClass.N, auto_fill=False):
        # One public entry point; preserve manual selection separately.
        if not auto_fill:
            return self._check_max_lv_manual(shikigami_class)
        self.realm_goto_grown()
        fill = RuleImage(roi_front=(1146,505,47,51), roi_back=(1114,478,115,107),
                         threshold=0.8, method='Template matching',
                         file='./tasks/KekkaiUtilize/realm/realm_auto_fill.png')
        done = RuleImage(roi_front=(1146,505,47,51), roi_back=(1114,478,115,107),
                         threshold=0.8, method='Template matching',
                         file='./tasks/KekkaiUtilize/realm/realm_remove_all.png')
        timeout = Timer(20).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(done):
                logger.info('Smart shikigami fill confirmed by remove-all button')
                break
            self.appear_then_click(fill, interval=1.5)
        else:
            raise GameStuckError('Smart shikigami fill was not confirmed within 20 seconds')
        timeout = Timer(15).start()
        while not timeout.reached():
            self.screenshot()
            # Realm plaques remain visible behind the growth overlay. Its
            # controls take priority over background realm recognition.
            if self.appear(done) or self.appear(fill) or self.appear(self.I_UI_BACK_BLUE):
                self.appear_then_click(self.I_UI_BACK_BLUE, interval=1.5)
                continue
            if self.appear(self.I_REALM_SHIN) or self.appear(self.I_SHI_GROWN):
                return
        raise GameStuckError('Cannot return to realm after smart shikigami fill')

    @cached_property
    def dict_card_image(self) -> dict:
        match_targets = {
            CardClass.TAIKO6: self.I_CARDS_KAIKO_6,
            CardClass.TAIKO5: self.I_CARDS_KAIKO_5,
            CardClass.TAIKO4: self.I_CARDS_KAIKO_4,
            CardClass.TAIKO3: self.I_CARDS_KAIKO_3,
            CardClass.FISH6: self.I_CARDS_FISH_6,
            CardClass.FISH5: self.I_CARDS_FISH_5,
            CardClass.FISH4: self.I_CARDS_FISH_4,
            CardClass.FISH3: self.I_CARDS_FISH_3,
            CardClass.MOON6: self.I_CARDS_MOON_6,
            CardClass.MOON5: self.I_CARDS_MOON_5,
            CardClass.MOON4: self.I_CARDS_MOON_4,
            CardClass.MOON3: self.I_CARDS_MOON_3,
            CardClass.MOON2: self.I_CARDS_MOON_2,
            CardClass.MOON1: self.I_CARDS_MOON_1
        }
        return match_targets

    @cached_property
    def dict_image_card(self) -> dict:
        return {v: k for k, v in self.dict_card_image.items()}

    @cached_property
    def order_targets(self) -> ImageGrid:
        rule = self.config.kekkai_activation.activation_config.card_type
        if rule == CardType.TAIKO:
            return ImageGrid([self.I_CARDS_KAIKO_6, self.I_CARDS_KAIKO_5])
        elif rule == CardType.FISH:
            return ImageGrid([self.I_CARDS_FISH_6, self.I_CARDS_FISH_5])
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')

    def run_activation(self, _config: ActivationConfig) -> bool:
        """
        执行挂卡，要求在结界的界面
        顺便把下一次执行也设置了
        :return: 挂卡成功（）返回True，失败(时间没到提前来了)返回False
        退出的时候还是在挂卡界面而不是结界界面
        """
        # Compare the serialized value so an older cached CardType is compatible.
        if _config.card_type != '不筛选（第一张）':
            self._card_reward_limits(_config)
        self.screenshot()
        marker, _ = self.realm_card_marker()
        if marker == 'expired':
            logger.info('Expired card icon visible; collect before opening card panel')
            self.harvest_card()
        elif marker == 'active':
            logger.info('Active card chest visible; open panel only to read remaining time')
        self.goto_cards()
        # 太诡异了 为什么有这么长的动画, 那么长的动画先休息一会
        logger.hr('Start activation')
        expired_retries = 0
        time.sleep(0.5)
        while 1:
            self.screenshot()
            card_status = self.check_card_status()
            card_effect = self.check_card_effect()
            # A yellow activation button is sufficient proof of selection.
            # Background/empty-slot matching must not override that signal.
            if not card_effect and self.appear(self.I_A_ACTIVATE_YELLOW, threshold=0.85):
                card_status = True

            # 不稳定太，等待动画结束
            if not card_status and not card_effect:
                # 黄色的 ”激活“
                if self.appear(self.I_A_ACTIVATE_YELLOW, threshold=0.85):
                    continue
                if self.appear(self.I_A_DEMOUNT):
                    # 现在在动画里面
                    logger.info('Now in the animation')
                    logger.info('Now there is no card')
                    continue
            # 如果这张卡生效着，在使用中
            if card_status and card_effect:
                logger.info('Card is using')
                interval = self.ocr_time()
                if interval == timedelta(0):
                    if expired_retries >= 2:
                        raise GameStuckError('Expired realm card remained after two collection attempts')
                    expired_retries += 1
                    if self.collect_expired_card() is False:
                        # Active chest or no recognized reward: leave the card
                        # intact and retry later instead of restarting the game.
                        self.set_next_run('KekkaiActivation',
                                          target=datetime.now() + timedelta(minutes=5))
                        return False
                    continue
                self.set_next_run("KekkaiActivation", target=interval+datetime.now())
                return False
            # 如果已经选中这张卡了， 那就激活这张卡
            if card_status and not card_effect:
                logger.info('Card is selected but not using')
                while 1:
                    self.screenshot()
                    if self.appear(self.I_A_INVITE, threshold=0.8):
                        logger.info('Card is activated')
                        break
                    if self.appear_then_click(self.I_UI_CONFIRM, interval=0.6):
                        continue
                    if self.appear_then_click(self.I_A_ACTIVATE_YELLOW, interval=1):
                        continue
                interval = self.ocr_time(True)
                if interval == timedelta(0):
                    # Re-enter the bounded expired-card branch on a fresh frame.
                    continue
                self.set_next_run("KekkaiActivation", target=interval + datetime.now())
                return True
            # 如果是什么都没有，那就是可以开始挂卡了
            if not card_status and not card_effect:
                logger.info('Card is not selected also not using')
                self.screening_card(_config.card_type)

    def goto_cards(self):
        """
        寮结界,前往挂卡界面
        :return:
        """
        compact_card = RuleImage(
            roi_front=(820,420,26,60), roi_back=(760,330,190,210),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/compact_realm_card.png')
        # Collecting the card removes its glow and changes the plaque's
        # appearance. Keep both states; do not lower the match threshold.
        compact_empty = RuleImage(
            roi_front=(820,420,26,60), roi_back=(760,330,190,210),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/compact_realm_card_empty.png')
        snow_card = RuleImage(
            roi_front=(821,420,24,66), roi_back=(780,390,120,130),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/snow_realm_card.png')
        # The snow plaque has a further appearance immediately after reward
        # collection. Existing empty/glowing templates score below 0.85 there.
        snow_collected = RuleImage(
            roi_front=(821,420,24,66), roi_back=(780,390,120,130),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/snow_realm_card_collected.png')
        low_card = RuleImage(
            roi_front=(765,492,17,48), roi_back=(730,460,90,110),
            threshold=0.85, method='Template matching',
            file='./tasks/KekkaiActivation/a/low_realm_card_empty.png')
        # Only the three printed characters: exclude moving banners, glow,
        # ornaments and the changing background around the plaque.
        text_cards = [RuleImage(
            roi_front=roi, roi_back=(730,380,150,180), threshold=0.85,
            method='Template matching', file=f'./tasks/KekkaiActivation/a/{name}.png')
            for name, roi in [('card_text_low', (768,500,11,31)),
                              ('card_text_tree', (831,424,11,32))]]
        entry_timer = Timer(45).start()
        next_click = 0
        clicks = 0
        while not entry_timer.reached():
            self.screenshot()

            if self.appear(self.I_A_CHECK_CARD):
                break
            if self.appear(self.I_A_AUTO_INVITE):
                break
            # A click is never considered success: wait for the panel markers.
            if time.monotonic() < next_click or clicks >= 3:
                time.sleep(0.5)
                continue
            for card in text_cards:
                if self.appear(card):
                    self.click(card)
                    clicks += 1
                    next_click = time.monotonic() + 10
                    break
            else:
                card = None
            if card is not None:
                continue
            for candidate, multi in ((low_card, False), (self.I_SHI_CARD, True),
                                     (compact_card, False), (compact_empty, False),
                                     (snow_card, True), (snow_collected, True)):
                matched = self.appear_multi_scale(candidate) if multi else self.appear(candidate)
                if matched:
                    self.click(candidate)
                    clicks += 1
                    next_click = time.monotonic() + 10
                    break
            time.sleep(0.3)
        else:
            raise GameStuckError('Realm card page was not confirmed within 45 seconds')
        logger.info('Enter card page')

    def check_card_status(self, screenshot=False) -> bool:
        """
        判断使用有挂卡在上面了， 判断依据就是如果没看就可以显示背景图
        :param screenshot:
        :return: 如果有卡在上面了返回True，否则返回False
        """
        if screenshot:
            self.screenshot()
        return not self.appear(self.I_A_EMPTY)

    def check_card_effect(self, screenshot=False) -> bool:
        """
        检查这张卡是否生效了, 如果是出现的“邀请”那就是生效了， 如果是“激活”那就是还没生效
        :param screenshot:
        :return: 生效返回True
        """
        if screenshot:
            self.screenshot()
        if self.appear(self.I_A_INVITE, threshold=0.8):
            return True
        elif self.appear(self.I_A_ACTIVATE_YELLOW):
            return False
        logger.info('Unknown card effect')
        while 1:
            self.screenshot()
            if self.appear(self.I_A_INVITE, threshold=0.7):
                return True
            elif self.appear(self.I_A_ACTIVATE_YELLOW):
                return False
            elif self.appear(self.I_A_ACTIVATE_GRAY):
                return False

    def ocr_time(self, screenshot=False) -> timedelta:
        if screenshot:
            self.screenshot()
        zero_reads = 0
        for attempt in range(3):
            if attempt:
                time.sleep(0.5)
                self.screenshot()
            delta = self.O_CARD_ALL_TIME.ocr_duration(self.device.image)
            if isinstance(delta, timedelta):
                if delta > timedelta(0):
                    return delta
                if delta == timedelta(0):
                    zero_reads += 1
        if zero_reads == 3:
            logger.info('Card timer is zero on three frames; collect expired card')
            return timedelta(0)
        raise GameStuckError('Unable to read a stable realm card duration')

    def collect_expired_card(self):
        """Collect from the realm, never unload a potentially active card."""
        timeout = Timer(12).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_A_CHECK_CARD) or self.appear(self.I_A_AUTO_INVITE):
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
                continue
            if self.appear(self.I_REALM_SHIN) or self.appear(self.I_SHI_GROWN):
                break
            self.appear_then_click(self.I_UI_BACK_RED, interval=1)
        else:
            raise GameStuckError('Cannot return to realm to collect expired card')
        collected = self.harvest_card()
        # Unknown reward must reach the caller's five-minute retry instead of
        # failing while reopening a panel hidden behind the reward bubble.
        if collected:
            self.goto_cards()
        return collected

    def screening_card(self, rule: str):
        """
        开始挑选卡
        :return:
        """

        if rule == '不筛选（第一张）':
            self._select_first_card()
            return
        elif rule == CardType.TAIKO:
            card_class = CardClass.TAIKO
            target_class = self.I_A_CARD_KAIKO
        elif rule == CardType.FISH:
            card_class = CardClass.FISH
            target_class = self.I_A_CARD_FISH
        else:
            logger.warning('Unknown card rule')
            self.push_notify(content='Unknown card rule')
            return

        while 1:
            self.screenshot()
            if self.appear(target_class):
                time.sleep(0.3)
                self.screenshot()
                if self.appear(target_class):
                    break
            if self.click(self.C_A_SELECT_CARD_LIST, interval=2.5):
                continue
        logger.info('Appear card class: {}'.format(card_class))
        while 1:
            self.screenshot()
            if not self.appear(target_class):
                break
            if self.appear_then_click(target_class, interval=1):
                continue
        logger.info('Selected card class: {}'.format(card_class))

        # 找最优卡
        while 1:
            self.screenshot()
            target = self.check_card_num()
            if target is None:
                # 未发现卡，处理逻辑
                self._card_not_found()
            if self.appear(self.I_A_EMPTY):
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_A_EMPTY):
                        self.config.kekkai_activation.activation_config.card_not_found_count = 0
                        self.config.save()
                        message = f'✅ 确认挂卡: {rule}'
                        self.save_image(content=message, push_flag=False, wait_time=0)
                        return
                    if self.click(target, interval=1):
                        continue

    def _select_first_card(self, timeout=None):
        """Use the first row of the unfiltered list, without reward OCR."""
        timeout = timeout if timeout is not None else Timer(12).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_A_ACTIVATE_YELLOW, threshold=0.85):
                self.config.kekkai_activation.activation_config.card_not_found_count = 0
                self.config.save()
                self.save_image(content='确认挂卡: 不筛选（第一张）', push_flag=False, wait_time=0)
                return
            if timeout.reached():
                self._card_not_found()
            self.click(self.C_A_SELECT_AUTO, interval=3)

    def _card_reward_limits(self, con=None):
        if con is None:
            con = self.config.kekkai_activation.activation_config
        rule = con.card_type
        if rule == CardType.TAIKO:
            min_card_num = con.min_taiko_num
            max_card_num = getattr(con, 'max_taiko_num', 0)
            check_card = "勾玉"
        elif rule == CardType.FISH:
            min_card_num = con.min_fish_num
            max_card_num = getattr(con, 'max_fish_num', 0)
            check_card = "体力"
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')

        if (type(min_card_num) is not int or type(max_card_num) is not int
                or min_card_num < 0 or max_card_num < 0
                or (max_card_num > 0 and max_card_num < min_card_num)):
            logger.warning(f'挂卡{rule}收益范围无效，跳过本次选卡，180分钟后重查')
            self.set_next_run('KekkaiActivation', target=datetime.now() + timedelta(minutes=180))
            raise TaskEnd('Invalid card reward range')
        return min_card_num, max_card_num, check_card

    def check_card_num(self):
        min_card_num, max_card_num, check_card = self._card_reward_limits()
        upper_label = max_card_num if max_card_num else '不限'
        logger.info(f'挂卡每小时{check_card}收益范围: {min_card_num}～{upper_label}')

        ocr_count = 0
        while 1:
            self.screenshot()
            results = self.O_CHECK_CARD_NUMBER.detect_and_ocr(self.device.image)
            ocr_count += 1
            # 第一步：筛选出包含 "体力或者勾玉" 的结果
            filtered_results = [result for result in results if check_card in result.ocr_text]
            logger.info(f"识别到卡: {[result.ocr_text for result in filtered_results]}")

            # 第二步：提取数字并按数字排序
            numeric_results = []
            for result in filtered_results:
                # 使用正则表达式提取所有数字
                numbers = [int(num) for num in re.findall(r'\d+', result.ocr_text)]
                if numbers:  # 如果提取到数字
                    if numbers[0] < min_card_num or (max_card_num > 0 and numbers[0] > max_card_num):
                        continue
                    numeric_results.append((numbers[0], result))  # 按第一个数字排序

            if numeric_results:
                # 按数字大到小排序
                sorted_results = [result for _, result in sorted(numeric_results, key=lambda x: x[0], reverse=True)]
                max_result = sorted_results[0]  # 获取数字最大的结果对象

                box = max_result.box  # 获取边界框坐标
                x_min = self.O_CHECK_CARD_NUMBER.roi[0] + box[0][0]
                y_min = self.O_CHECK_CARD_NUMBER.roi[1] + box[0][1]
                width = box[1][0] - box[0][0]
                height = box[2][1] - box[1][1]
                roi = int(x_min), int(y_min), int(width), int(height)

                target = RuleClick(roi_front=roi, roi_back=roi, name="tmpclick")
                logger.info(f"选择挂卡: [{max_result.ocr_text}] {roi}")

                return target
            else:
                if ocr_count > 3:
                    logger.error('多次未找到符合条件的结果, 退出')
                    return None
                logger.warning("未找到符合条件的结果, 准备往上滑动")
                duration = 2
                safe_pos_x = random.randint(200, 400)
                safe_pos_y = random.randint(580, 600)
                p1 = (safe_pos_x, safe_pos_y)
                p2 = (safe_pos_x, safe_pos_y - 410)
                logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
                self.device.swipe_adb(p1, p2, duration=duration)
                time.sleep(1)
                continue

    def _card_not_found(self):
        # 获取配置引用
        activation_config = self.config.kekkai_activation.activation_config
        # 多少分钟后重试
        retry_minutes = 180
        retry_count = 3
        # 递增未找到卡的计数器
        activation_config.card_not_found_count += 1

        if activation_config.card_type == '不筛选（第一张）':
            log_msg = '⚠️全部列表第一张卡未能选中，180分钟后重试'
            if activation_config.card_not_found_count >= retry_count:
                activation_config.card_not_found_count = 0
            next_run = datetime.now() + timedelta(minutes=retry_minutes)
        elif activation_config.card_not_found_count >= retry_count:
            # 达到重试上限时的处理
            log_msg = f"⚠️{activation_config.card_type}卡未检出（累计{retry_count}次），{retry_minutes}分钟后重试"
            activation_config.card_not_found_count = 0  # 重置计数器并延长下次执行时间
            next_run = datetime.now() + timedelta(minutes=retry_minutes)
        else:
            # # 未达上限切换卡类型
            new_type = (
                CardType.FISH
                if activation_config.card_type == CardType.TAIKO
                else CardType.TAIKO
            )
            log_msg = f"🔄{activation_config.card_type}卡未检出 → 切换{new_type}"
            activation_config.card_type = new_type
            next_run = datetime.now()

        # 统一记录日志和推送
        self.save_image(content=log_msg, push_flag=True)

        # 保存配置并设置下次执行
        self.config.save()
        if activation_config.card_type == '不筛选（第一张）':
            self.set_next_run("KekkaiActivation", target=next_run, server=False)
        else:
            self.set_next_run("KekkaiActivation", target=next_run)
        raise TaskEnd

    def _check_max_lv_manual(self, shikigami_class: ShikigamiClass = ShikigamiClass.N):
        """
        在结界界面，进入式神育成，检查是否有满级的，如果有就换下一个
        退出的时候还是结界界面
        :return:
        """
        self.realm_goto_grown()
        if self.appear(self.I_RS_LEVEL_MAX):
            # 存在满级的式神
            logger.info('Exist max level shikigami and replace it')
            self.unset_shikigami_max_lv()
            self.switch_shikigami_class(shikigami_class)
            self.set_shikigami(shikigami_order=7, stop_image=self.I_RS_NO_ADD)
        else:
            logger.info('No max level shikigami')
        if self.detect_no_shikigami():
            logger.warning('There are no any shikigami grow room')
            self.switch_shikigami_class(shikigami_class)
            self.set_shikigami(shikigami_order=7, stop_image=self.I_RS_NO_ADD)

        # 回到结界界面
        while 1:
            self.screenshot()

            if self.appear(self.I_REALM_SHIN) and self.appear_multi_scale(self.I_SHI_GROWN):
                self.screenshot()
                if not self.appear(self.I_REALM_SHIN):
                    continue
                break
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=2.5):
                continue

    @cached_property
    def realm_card_markers(self):
        """Local rules for bubbles above normal and compact/snow card plaques."""
        def local(rule):
            return RuleImage(
                roi_front=tuple(rule.roi_front), roi_back=(720, 120, 300, 400),
                threshold=0.8, method=rule.method, file=rule.file)

        # Despite its historical name, HARVEST_EXP is the active-card chest,
        # not an expired card reward. It must never be a collection target.
        chest = local(self.I_A_HARVEST_EXP)
        # Running workers may still hold the asset class from before this update.
        umbrella = getattr(self, 'I_A_HARVEST_UMBRELLA', None)
        if umbrella is None:
            umbrella = RuleImage(
                roi_front=(810, 328, 50, 45), roi_back=(780, 120, 240, 285),
                threshold=0.8, method='Template matching',
                file='./tasks/KekkaiActivation/a/a_a_harvest_umbrella.png')
        cards = [
            umbrella,
            RuleImage(roi_front=(874,164,51,48), roi_back=(780,120,240,285),
                      threshold=0.8, method='Template matching',
                      file='./tasks/KekkaiActivation/a/a_a_harvest_moon_1.png'),
            self.I_A_HARVEST_FISH4,  # 斗鱼4/5区别不大 斗鱼的如果一直没有领的话
            self.I_A_HARVEST_KAIKO_4,  # 太鼓4
            self.I_A_HARVEST_KAIKO_3,   # 太鼓3
            self.I_A_HARVEST_KAIKO_6,  # 太鼓6
            self.I_A_HARVEST_FISH_6,  # 斗鱼6
            self.I_A_HARVEST_MOON_3,  # 太阴3
            self.I_A_HARVEST_FISH_3,  # 斗鱼三
        ]
        return chest, [local(card) for card in cards]

    def realm_card_marker(self):
        """Exclude chests, then detect a reward bubble without naming its card."""
        if self.appear(self.I_A_CHECK_CARD) or self.appear(self.I_A_AUTO_INVITE):
            return 'unknown', None
        chest, cards = self.realm_card_markers
        # Prioritize the chest even if a card template also matches weakly.
        if self.appear_multi_scale(chest):
            return 'active', None
        from tasks.KekkaiActivation.reward_bubble import find_reward_bubble
        rect = find_reward_bubble(self.device.image)
        if rect is not None:
            return 'expired', RuleClick(rect, rect, name='realm_card_reward_bubble')
        # Retain known-art matching for frames with an obscured or clipped rim.
        for card in cards:
            if self.appear_multi_scale(card):
                return 'expired', card
        return 'unknown', None

    def harvest_card(self):
        """Collect non-chest reward bubbles; the card panel verifies removal."""
        timeout = Timer(8).start()
        clicked = False
        while not timeout.reached():
            self.screenshot()
            if self.ui_reward_appear_click():
                continue
            marker, card = self.realm_card_marker()
            if marker == 'active':
                logger.info('Active card chest visible; leave it untouched')
                return False
            if marker == 'expired':
                self.click(card, interval=1)
                clicked = True
                time.sleep(0.5)
            else:
                time.sleep(0.3)
        logger.info('Finished realm card collection pass' if clicked
                    else 'No expired realm reward recognized; no collection attempted')
        return clicked



if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    import cv2

    c = Config('oas1')
    d = Device(c)

    t = ScriptTask(c, d)
    t.run()
    # t.run_activation(t.config.kekkai_activation.activation_config)
