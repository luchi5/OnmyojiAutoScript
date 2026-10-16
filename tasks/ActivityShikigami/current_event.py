"""2026-10 拾此一瞬：磐长故地活动门票副本。

月度入口、资源位置和结算规则集中在此；古迹演武不使用本流程。
"""

import re
import random

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from tasks.Component.GeneralBattle.battle_wait import battle_wait_strategy, battle_wait_options


def _image(name, front, back=None, threshold=0.85):
    return RuleImage(roi_front=front, roi_back=back or front,
                     method="Template matching", threshold=threshold,
                     file=f"./tasks/ActivityShikigami/event_202610/event_202610_{name}.png")


class CurrentEventAssets:
    I_EVENT_MAIN = _image("main", (151, 20, 132, 34), (141, 10, 152, 54))
    I_EVENT_VERSION = _image("version", (54, 146, 92, 28), (40, 130, 125, 60))
    I_EVENT_LIST_ART = _image("list_art", (1062, 99, 94, 117), (1050, 85, 120, 145))
    I_EVENT_TICKET_ENTRY = _image("ticket_entry", (815, 461, 108, 29), (792, 443, 155, 68), 0.8)
    I_EVENT_MAP = _image("map_title", (591, 3, 138, 38), (577, 0, 166, 57))
    I_EVENT_MAP_BATTLE = _image("map_battle", (74, 152, 120, 28), (62, 142, 144, 48))
    I_EVENT_MAP_BACK = _image("map_back", (18, 12, 51, 48), (5, 2, 77, 70))
    I_TICKET_PAGE = _image("ticket_page", (151, 20, 131, 34), (141, 10, 151, 54))
    I_TICKET_CHALLENGE = _image("challenge", (1130, 593, 88, 45), (1115, 578, 118, 75))
    I_TICKET_SIMPLE = _image("simple", (1144, 277, 65, 67), (1134, 267, 85, 87))
    I_TICKET_HARD = _image("hard", (1144, 386, 65, 67), (1134, 376, 85, 87))
    I_TICKET_RECORDS = _image("records", (1006, 555, 57, 79), (996, 545, 77, 99))
    I_TICKET_REWARD_CONTINUE = _image("reward_continue", (552, 678, 178, 32), (532, 665, 218, 55))
    # Blank margins around the full loot grid, in the normalized 1280x720 frame.
    # Keep clear of the top announcement and the lower-left bonus indicator.
    C_TICKET_REWARD_BLANKS = tuple(
        RuleClick(roi_front=area, roi_back=area, name='event_ticket_reward_blank')
        for area in ((30, 180, 170, 400), (1100, 180, 140, 400), (350, 665, 650, 45))
    )
    # Follow upstream 27241886's whole-grid exclusion instead of individual
    # reward slots. The current ticket grid starts higher than its old layout.
    C_TICKET_REWARD_BODY = RuleClick((220, 70, 870, 575), (220, 70, 870, 575), 'ticket_reward_body')
    C_TICKET_REWARD_HEADER = RuleClick((0, 0, 1280, 180), (0, 0, 1280, 180), 'ticket_reward_header')
    C_TICKET_REWARD_BUFF = RuleClick((0, 620, 350, 100), (0, 620, 350, 100), 'ticket_reward_buff')
    # A tooltip can cover the bottom prompt: completion clicks only the sides.
    C_TICKET_REWARD_DETAILS = RuleClick((200, 0, 900, 720), (200, 0, 900, 720), 'ticket_reward_details')
    I_TICKET_LOCK = _image("lock", (895, 650, 29, 29), (889, 644, 41, 41), 0.9)
    I_TICKET_UNLOCK = _image("unlock", (895, 650, 29, 29), (889, 644, 41, 41), 0.9)
    # Single 保留原始识别文本：识别失败不能通过 Digit 的默认值变成 0。
    # Keep vertical context: the old tight crop added punctuation before 255/385.
    O_TICKET_BALANCE = RuleOcr(roi=(1110, 23, 96, 32), area=(1110, 23, 96, 32),
                             mode="Single", method="Default", keyword="", name="event_ticket_balance")
    O_TICKET_COST = RuleOcr(roi=(1168, 636, 42, 32), area=(1168, 636, 42, 32),
                          mode="Single", method="Default", keyword="", name="event_ticket_cost")


def parse_ticket_budget(balance_text, cost_text):
    """本期简单消耗 1 张、困难消耗 5 张，空白/混合字符返回未知。"""
    balance = str(balance_text or "").strip()
    cost = re.sub(r"\s+", "", str(cost_text or ""))
    if not re.fullmatch(r"[0-9]{1,6}", balance):
        return None
    match = re.fullmatch(r"[xX×]([15])", cost)
    if not match:
        return None
    return int(balance), int(match.group(1))


class CurrentEventTicket(CurrentEventAssets):
    def ticket_page_visible(self):
        return self.appear(self.I_TICKET_PAGE) and self.appear(self.I_TICKET_CHALLENGE)

    def ticket_read_budget(self, expected_cost=None):
        """同一页面连续两次读到相同数量后才允许挑战或判定不足。"""
        timeout = Timer(8).start()
        interval = Timer(0.6)
        previous = None
        while not timeout.reached():
            self.screenshot()
            if not self.ticket_page_visible():
                previous = None
                continue
            if not interval.reached():
                continue
            interval.reset()
            balance = self.O_TICKET_BALANCE.ocr_single(self.device.image)
            cost = self.O_TICKET_COST.ocr_single(self.device.image)
            budget = parse_ticket_budget(balance, cost)
            if budget is not None and expected_cost is not None and budget[1] != expected_cost:
                budget = None
            if budget is not None and budget == previous:
                logger.info(f'Event tickets: {budget[0]}, cost per battle: {budget[1]}')
                return budget
            previous = budget
        raise GameStuckError(f'磐长故地门票或挑战消耗未确认（预期消耗 {expected_cost or "1 或 5"}），不按 0 张处理')

    def ticket_select_difficulty(self):
        # 旧配置保持游戏中的选择；选择后固定本轮消耗，防止之后误打另一难度。
        choice = getattr(self.conf.general_climb, 'pass_difficulty', 'ticket_keep_current')
        costs = {'ticket_keep_current': None, 'ticket_simple': 1, 'ticket_hard': 5}
        if choice not in costs:
            raise RequestHumanTakeover(f'未知的活动门票难度：{choice}')
        _, current_cost = self.ticket_read_budget()
        expected = costs[choice]
        if expected is not None and current_cost != expected:
            target = self.I_TICKET_SIMPLE if expected == 1 else self.I_TICKET_HARD
            if not self.appear(target):
                raise GameStuckError('磐长故地难度按钮未识别，停止挑战')
            self.click(target)
            self.ticket_read_budget(expected_cost=expected)
        self._ticket_expected_cost = expected or current_cost
        logger.info(f'Event difficulty: {"简单" if self._ticket_expected_cost == 1 else "困难"}; '
                    f'cost {self._ticket_expected_cost}')

    def ticket_lock_team(self, enable_preset):
        target = self.I_TICKET_UNLOCK if enable_preset else self.I_TICKET_LOCK
        opposite = self.I_TICKET_LOCK if enable_preset else self.I_TICKET_UNLOCK
        timeout = Timer(10).start()
        while not timeout.reached():
            self.screenshot()
            if not self.ticket_page_visible():
                continue
            if self.appear(target):
                return
            self.appear_then_click(opposite, interval=1.5)
        raise GameStuckError('磐长故地阵容锁定状态未确认，停止挑战')

    def ticket_wait_return(self):
        timeout = Timer(25).start()
        click_interval = Timer(1.5)
        while not timeout.reached():
            self.screenshot()
            if self.ticket_page_visible():
                return
            # A large loot grid can remain after the generic reward handler returns.
            # Only dismiss its explicit continue prompt, never an unknown dialog.
            if self.appear(self.I_TICKET_REWARD_CONTINUE) and click_interval.reached():
                self.click(random.choice(self.C_TICKET_REWARD_BLANKS))
                click_interval.reset()
        raise GameStuckError('磐长故地结算后未返回挑战页，停止继续挑战')

    def ticket_battle_once(self):
        before, cost = self.ticket_read_budget()
        expected = getattr(self, '_ticket_expected_cost', None)
        if expected is not None and cost != expected:
            raise GameStuckError(f'磐长故地难度发生变化：预期消耗 {expected}，实际 {cost}，停止挑战')
        self.put_status()
        if before < cost:
            logger.info(f'Event tickets insufficient: {before} < {cost}; stop ticket battles')
            return False

        # 两次资源识别后的最新画面仍是专属挑战页。只点击一次，加载期间不连点。
        self.click(self.I_TICKET_CHALLENGE)
        timeout = Timer(35).start()
        while True:
            self.screenshot()
            if self.is_in_battle(False) or self.appear(self.I_REWARD) or self.appear(self.I_WIN):
                break
            if timeout.reached():
                # 不接受任意确认弹窗，尤其不能误点购买门票。
                raise GameStuckError('磐长故地点击挑战后未进入战斗，不重复点击或确认未知弹窗')

        strategies, options = self.loadout_from_config(self.get_general_battle_conf())
        # Reuse the normal loot handler without deliberate detail clicks or the
        # default handler's second click immediately after the victory screen.
        strategies['success'] = 'soul'
        safe = ['C_TICKET_REWARD_BODY', 'C_TICKET_REWARD_HEADER', 'C_TICKET_REWARD_BUFF']
        options['success'].excludes_1 = safe.copy()
        options['success'].excludes_2 = safe.copy()
        # As upstream does, keep completion active until the challenge page is
        # actually visible. Exhausting retries must not count a finished run.
        options['completion'].check_imgs = [self.I_TICKET_PAGE]
        options['completion'].excludes = safe + ['C_TICKET_REWARD_DETAILS']
        options['completion'].fallback_interval = 3
        options['completion'].fallback_limit = 5
        with battle_wait_strategy(**strategies), battle_wait_options(**options):
            win = self.battle_wait()
        if not win:
            raise RequestHumanTakeover('磐长故地挑战失败，请检查难度和阵容后再继续')

        self.ticket_wait_return()
        after, _ = self.ticket_read_budget()
        if before - after != cost:
            raise GameStuckError(f'磐长故地门票变动待核验：{before} -> {after}，预期消耗 {cost}')
        self.count_map['pass'] += 1
        logger.info(f"Count pass: {self.count_map['pass']}; tickets {before} -> {after}")
        return True
