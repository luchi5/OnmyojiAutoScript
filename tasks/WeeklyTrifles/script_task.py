# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

from module.exception import TaskEnd
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import (
    page_main,
    page_collection,
    page_area_boss,
    page_secret_zones,
    page_summon,
    page_guild,
    random_click,
)
from tasks.WeeklyTrifles.assets import WeeklyTriflesAssets


class ScriptTask(GameUi, WeeklyTriflesAssets):

    def run(self):
        con = self.config.weekly_trifles.trifles
        if con.share_collect:
            self._share_collect()
        if con.share_area_boss:
            self._share_area_boss()
        if con.share_secret:
            self._share_secret()
        if con.save_touch_fish:
            self._save_touch_fish()
        if con.broken_amulet:
            self._broken_amulet(con.broken_amulet)

        self.set_next_run(task='WeeklyTrifles', success=True, finish=True)
        raise TaskEnd('WeeklyTrifles')

    def _open_share_panel(self, wechat):
        """Give each share entry three clicks, including time for its last response."""
        timer = Timer(16).start()
        attempts = 0
        for _ in range(64):
            self.screenshot()
            if self.appear(self.I_WT_QR_CODE):
                return True
            if timer.reached():
                break
            if attempts < 3 and self.appear_then_click(wechat, interval=4):
                attempts += 1
            sleep(0.3)
        logger.warning('Share did not open after at most three attempts; skip this share')
        return False

    def click_share(self, wechat) -> bool:
        """
        点击分享
        :param wechat:
        :return:
        """
        # 点击分享
        # self.ui_click(wechat, self.I_WT_QR_CODE)
        if not self._open_share_panel(wechat):
            return False
        logger.info('Click share')
        get_timer = Timer(7)
        get_timer.start()
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click():
                logger.info('Get reward')
                return True
            if self.appear_then_click(
                self.I_WT_QR_CODE, self.C_WT_WECHAT, interval=4.8
            ):
                continue
            if get_timer.reached():
                logger.warning('Share timeout. The reward may have been obtained')
                return False

    def _open_touch_fish(self):
        """Search only the guild sidebar, with bounded scrolling and entry checks."""
        for _ in range(3):
            self.screenshot()
            if self.appear(self.I_CHECK_TOUCH_FISH):
                return True
            if not self.appear(self.I_CHECK_GUILD):
                return False
            if self.appear(self.I_WT_FOLD_WINDOW):
                break
            self.appear_then_click(self.I_WT_OPEN_FOLD_WINDOW, interval=1)
            sleep(1)
        else:
            logger.warning('Guild sidebar did not expand; stop touch fish attempt')
            return False

        # Four upward swipes cover additional temporary-event rows. Never swipe
        # outside the sidebar or keep navigating back through the courtyard.
        for attempt in range(5):
            self.screenshot()
            if self.appear(self.I_CHECK_TOUCH_FISH):
                return True
            if not self.appear(self.I_CHECK_GUILD) or not self.appear(self.I_WT_FOLD_WINDOW):
                return False
            if self.appear_then_click(self.I_GUILD_GOTO_TF, interval=1):
                for _ in range(5):
                    sleep(1)
                    self.screenshot()
                    if self.appear(self.I_CHECK_TOUCH_FISH):
                        return True
                logger.warning('Touch fish entry clicked but destination unconfirmed')
                return False
            if attempt < 4:
                self.device.swipe(p1=(150, 450), p2=(150, 210),
                                  duration=(0.4, 0.5), control_name='TOUCH_FISH_SIDEBAR_SCROLL')
                sleep(0.8)
        logger.warning('Touch fish entry not found after four sidebar scrolls')
        return False

    def _save_touch_fish(self):
        """
        惠比寿的摸鱼行动
        :return:
        """
        logger.hr('Touch fish start')
        self.goto_page(page_guild)
        if not self._open_touch_fish():
            self.set_next_run(task='WeeklyTrifles', success=False)
            raise TaskEnd('Touch fish entry unavailable; stop this attempt')
        # 保存次数，后续做了也会邮件返还福运御守
        self.screenshot()
        if self.appear(self.I_WT_SAVE_ALL):
            cu_tickts, _, _ = self.O_WT_LUCKY_TICKETS.ocr(self.device.image)
            logger.info(f"当前的福运御守数量：{cu_tickts}")
            cost_tickts = self.O_WT_SAVE_COST.ocr(self.device.image)
            logger.info(f"全部存储需要花费的福运御守数量：{cost_tickts}")
            not_save_flag = True
            get_timer = Timer(7)
            get_timer.start()
            while 1:
                sleep(0.5)
                self.screenshot()
                if self.appear(self.I_WT_LAST_SAVE):
                    self.click(
                        random_click(ltrb=(True, False, False, False)), interval=1.5
                    )
                    continue
                if self.appear_then_click(self.I_WT_HAPPY_GET):
                    continue
                if cu_tickts >= cost_tickts and not_save_flag:
                    self.appear_then_click(self.I_WT_SAVE_ALL)
                    not_save_flag = False
                    continue
                if self.appear_then_click(self.I_WT_TF_CONFIRM):
                    continue
                if self.appear(self.I_WT_TF_SAVE_SUCCESS) and not self.appear(
                    self.I_WT_SAVE_ALL
                ):
                    logger.info('Touch fish save success')
                    break
                if cu_tickts < cost_tickts and not_save_flag:
                    logger.warning('Touch fish tickts not enough, exit!')
                    break
                if get_timer.reached():
                    logger.warning('Touch fish timeout, exit!')
                    break
        logger.hr('Touch fish finished')
        self.goto_page(page_main)

    def _share_collect(self):
        """
        图鉴分享
        :return:
        """
        logger.hr('Share collect')
        self.goto_page(page_collection)
        # 一路进去
        timer = Timer(20).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_WT_LOGO):
                break
            if self.appear_then_click(self.I_WT_SHIKIAGMI, interval=1):
                continue
            if self.appear_then_click(self.I_WT_SHARE, interval=1):
                continue
        else:
            logger.warning('Collection share entry unavailable; skip')
            self._leave_collect_share()
            return False
        # 点击分享
        if not self._open_share_panel(self.I_WT_COLLECT_WECHAT):
            self._leave_collect_share()
            return False
        logger.info('Click share')
        get_timer = Timer(3)
        get_timer.start()
        while 1:
            self.screenshot()

            if self.ui_reward_appear_click():
                logger.info('Get reward')
                break

            if self.appear_then_click(
                self.I_WT_QR_CODE, self.C_WT_WECHAT, interval=2.8
            ):
                continue
            if get_timer.reached():
                logger.warning('Share timeout. The reward may have been obtained')
                break
        self._leave_collect_share()

    def _leave_collect_share(self):
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_WT_SHIKIAGMI):
                return
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
        raise TaskEnd('Unable to leave collection sharing safely')

    def _share_area_boss(self):
        """
        地鬼分享
        :return:
        """

        def back_boss():
            while 1:
                self.screenshot()
                if self.appear(self.I_WT_DAY_BATTLE) or self.appear(
                    self.I_CHECK_EXPLORATION
                ):
                    break
                if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    continue
                if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                    continue
                if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                    continue
            logger.info('Back to boss')

        logger.hr('Share area boss')
        self.goto_page(page_area_boss)

        # 一路进去
        obtained = False
        while 1:
            self.screenshot()
            if self.appear(self.I_WT_AB_WECHAT):
                break
            if self.appear(self.I_WT_NO_DAY):
                obtained = True
                break
            if self.click(self.C_WT_AB_CLICK, interval=2.5):
                continue
            if self.appear_then_click(self.I_WT_DAY_BATTLE, interval=2):
                continue
            if self.appear_then_click(self.I_WT_SHARE_AB, interval=1):
                continue
        # 再次检查一次这周有没有领取
        time.sleep(1)
        self.screenshot()
        if not self.appear(self.I_WT_AB_JADE):
            logger.warning('This week has been obtained')
            obtained = True
        if not obtained:
            # 点击分享
            self.click_share(self.I_WT_AB_WECHAT)
            obtained = True
        if obtained:
            back_boss()

    def _leave_secret_share(self):
        for _ in range(12):
            self.screenshot()
            if self.appear(self.I_CHECK_SECRET_ZONES) or self.appear(self.I_CHECK_MAIN):
                return
            for button in (self.I_UI_BACK_RED, self.I_UI_BACK_BLUE, self.I_UI_BACK_YELLOW):
                if self.appear_then_click(button, interval=1):
                    break
            sleep(0.4)
        raise TaskEnd('Unable to leave unavailable secret sharing safely')

    def _share_secret(self):
        """
        秘闻分享
        :return:
        """
        logger.hr('Share secret')
        self.goto_page(page_secret_zones)
        # 一路进去
        valid = False
        timeout = Timer(20).start()
        for _ in range(60):
            self.screenshot()
            if self.appear(self.I_WT_SE_WECHAT):
                self.wait_until_stable(self.I_WT_SE_WECHAT, skip_first_screenshot=True)
                break
            if timeout.reached():
                logger.warning('秘闻分享入口未开放或未响应，跳过本次分享')
                self._leave_secret_share()
                return False
            if self.appear_then_click(self.I_WT_ENTER_SE, interval=4):
                continue
            if self.appear_then_click(self.I_WT_SE_SHARE, interval=5):
                valid = True
                continue
            if self.appear(self.I_WT_SE_RANK) and (not valid):
                # 如果出现排名但是没有出现分享，那就是还没打，退出
                self.wait_until_stable(self.I_WT_SE_SHARE, skip_first_screenshot=True)
                if self.appear(self.I_WT_SE_SHARE):
                    continue
                logger.warning('This week has not been obtained')
                self.ui_click(self.I_UI_BACK_BLUE, self.I_UI_BACK_YELLOW)
                self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_MAIN)
                return
            sleep(0.3)
        else:
            self._leave_secret_share()
            return False
        logger.info('Enter secret')
        # 判断是否已经领取
        self.screenshot()
        obtained = False
        if not self.appear(self.I_WT_SE_JADE):
            obtained = True
            logger.warning('This week has been obtained')
        # 点击分享
        if not obtained:
            self.click_share(self.I_WT_SE_WECHAT)
        # 返回
        self.ui_click(self.I_UI_BACK_BLUE, self.I_UI_BACK_YELLOW)
        self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_MAIN)

    def _wait_broken_amulet_result(self, before):
        """Only reset this round's click guard after verified consumption."""
        timeout = Timer(5).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_BM_AGAIN):
                after = self.O_BA_AMOUNT_1.ocr(self.device.image)
                if (isinstance(after, int) and not isinstance(after, bool)
                        and after >= 0 and before - after in (10, 50)):
                    for button in ('SAFE_RANDOM_CLICK', self.I_BM_ENTER, self.I_BM_AGAIN):
                        self.device.click_record_remove(button)
                    return before - after
            self.click(random_click(), interval=0.8)
        return None

    def _broken_amulet(self, dest_num: int):
        """

        :param dest_num:
        :return:
        """

        def exit_amulet():
            self.wait_until_appear(self.I_BM_CONFIRM, wait_time=3)
            while 1:
                self.screenshot()
                if not self.appear(self.I_BM_CONFIRM):
                    break
                else:
                    self.appear_then_click(self.I_BM_CONFIRM, interval=1)
            logger.info('Exit broken amulet')

        logger.hr('Broken amulet')
        self.goto_page(page_summon)
        self.screenshot()
        real_num = self.O_BA_AMOUNT_1.ocr(self.device.image)
        if not isinstance(real_num, int) or isinstance(real_num, bool) or real_num <= 0:
            logger.warning('No broken amulet')
            return
        logger.info(f'Broken amulet: {real_num}')
        count = 0
        self.wait_until_appear(self.I_BM_ENTER)
        logger.info('Enter broken amulet')
        while count < dest_num:
            self.screenshot()
            real_num = self.O_BA_AMOUNT_1.ocr(self.device.image)
            if not isinstance(real_num, int) or isinstance(real_num, bool) or real_num <= 0:
                exit_amulet()
                return
            if self.appear_then_click(
                self.I_BM_ENTER, interval=1
            ) or self.appear_then_click(self.I_BM_AGAIN, interval=1):
                sleep(0.4)  # 等待动画开始
                consumed = self._wait_broken_amulet_result(real_num)
                if consumed is None:
                    logger.warning(
                        f'Wait for again timeout:Count[{count}], Remain[{real_num}]'
                    )
                    exit_amulet()
                    self.set_next_run(task='WeeklyTrifles', success=False)
                    raise TaskEnd('Broken amulet result unconfirmed')
            else:
                # 既没有出现召唤破碎符咒也没有出现再次召唤, 则退出(一般不会出现这种情况, 只是为了异常处理)
                exit_amulet()
                return
            x_10, _, _, _ = self.O_BA_TIMES.ocr(self.device.image, '10次')
            x_50, _, _, _ = self.O_BA_TIMES.ocr(self.device.image, '50次')
            self.I_BMT_CHECK.match(self.device.image)
            x_check, y_check, width_check, height_check = self.I_BMT_CHECK.roi_front
            selected_10 = min(abs(x_10 - x_check), abs(x_50 - x_check)) == abs(
                x_10 - x_check
            )
            logger.info(f'Current selected {"10" if selected_10 else "50"} amulet')
            count += consumed
            logger.info(f'Broken amulet:Count[{count}], Remain[{real_num}]')
            # 一次50票不超过限制且当前选择的是10票则切换50票
            if count + 50 < dest_num and selected_10:
                logger.hr('Switch to 50 amulet')
                self.device.click(
                    x=x_50 - width_check // 2,
                    y=y_check + height_check // 2,
                    control_name='Click_50',
                )
                self.device.click_record_clear()
            # 一次50票会超过限制且当前选择的是50票则切换10票
            if count + 50 >= dest_num and not selected_10:
                logger.hr('Switch to 10 amulet')
                self.device.click(
                    x=x_10 - width_check // 2,
                    y=y_check + height_check // 2,
                    control_name='Click_10',
                )
                self.device.click_record_clear()
        # 正常结束且还有票, 则执行一次退出
        exit_amulet()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
    # t._share_collect()
    t._share_area_boss()
    # t.click_share(t.I_WT_SE_WECHAT)
