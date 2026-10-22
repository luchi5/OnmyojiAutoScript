"""拾此一瞬二阶段：神降尘世，额外奖励已完成次数达到100结束。"""

import re

from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from tasks.ActivityShikigami.boss_progress import BossProgressStore
from tasks.ActivityShikigami.current_event import CurrentEventAssets, _image
from tasks.Component.GeneralBattle.battle_wait import battle_wait_strategy, battle_wait_options, HookSignal


def parse_boss_bonus(text):
    """An unreadable counter is unknown, never an inferred zero."""
    text = re.sub(r'\s+', '', str(text or '')).replace('／', '/')
    text = text.replace('（', '(').replace('）', ')')
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    match = re.fullmatch(r'(?:额外奖励)?(0|[1-9][0-9]{0,2})/100', text)
    if not match:
        return None
    completed = int(match.group(1))
    return completed if completed <= 100 else None


def resolve_boss_bonus(primary_text, backup_text, expected_min=None):
    """Cross-check fresh UI counters, without repairing malformed digits/slashes."""
    primary, backup = parse_boss_bonus(primary_text), parse_boss_bonus(backup_text)
    if primary is not None and backup is not None and primary != backup:
        return None, None
    completed = primary if primary is not None else backup
    source = 'both' if primary is not None and backup is not None else 'right' if primary is not None else 'left'
    if completed is None:
        return None, None
    # Several battles may have finished while OCR was unavailable. Accept a
    # fresh unchanged or advanced counter without requiring an exact +1.
    if expected_min is not None and completed < expected_min:
        return None, None
    return completed, source


class CurrentBossAssets:
    I_EVENT_BOSS_ENTRY = _image('boss_entry', (1070,303,115,33), (1035,275,195,95), 0.8)
    I_EVENT_BOSS_PAGE = _image('boss_page', (155,10,128,32), (130,0,190,65), 0.8)
    I_EVENT_BOSS_CHALLENGE = _image('boss_challenge', (1140,592,95,43), (1100,555,160,105), 0.8)
    I_EVENT_BOSS_RECORDS = _image('boss_records', (1025,558,55,69), (995,530,110,110), 0.8)
    I_EVENT_BOSS_SHARE_DAMAGE = _image('boss_share_damage', (724,245,103,28), (710,235,135,48), 0.88)
    I_EVENT_BOSS_SHARE_LINEUP = _image('boss_share_lineup', (725,403,100,28), (710,390,135,53), 0.88)
    I_EVENT_BOSS_SHARE_CLOSE = _image('boss_share_close', (1190,36,40,39), (1160,10,105,90), 0.85)
    O_EVENT_BOSS_BONUS = RuleOcr(
        roi=(1183,671,95,33), area=(1183,671,95,33), mode='Single',
        method='Default', keyword='', name='event_boss_bonus')
    # Wider left crop keeps all three digits of 100 and both parentheses.
    O_EVENT_BOSS_BONUS_BACKUP = RuleOcr(
        roi=(253,585,120,33), area=(253,585,120,33), mode='Single',
        method='Default', keyword='', name='event_boss_bonus_backup')


class CurrentEventBoss(CurrentBossAssets, CurrentEventAssets):
    def boss_fixed_limit(self):
        con = getattr(getattr(self, 'conf', None), 'general_climb', None)
        if getattr(con, 'boss_count_mode', 'boss_auto_progress') == 'boss_fixed_count':
            return con.boss_limit
        return None

    def boss_budget_exhausted(self):
        fixed = self.boss_fixed_limit()
        if fixed is not None:
            return self.count_map['boss'] >= fixed
        # Even without any readable game counter, never start win 101 in
        # this execution. A known anchor can only reduce the remaining budget.
        return getattr(self, '_boss_counted', 0) >= 100 or self.count_map['boss'] >= 100

    def boss_progress_store(self):
        account = getattr(getattr(self, 'config', None), 'config_name', None)
        if not account:
            return None
        if not hasattr(self, '_boss_progress_store'):
            self._boss_progress_store = BossProgressStore('data/ActivityShikigami/event_202610_boss', account)
        return self._boss_progress_store

    def boss_load_progress(self):
        store = self.boss_progress_store()
        if store is None:
            return
        try:
            record = store.load()
            self._boss_local_progress = record
            if record:
                self._boss_completed = record['confirmed']
                self._boss_counted = record['counted']
                logger.info(f"BOSS checkpoint: confirmed {record['confirmed']}, "
                            f"counted with wins {record['counted']}/100")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(f'BOSS checkpoint unreadable; verify current game counter: {exc}')

    def boss_record_progress(self, confirmed, expected=None):
        counted = max(getattr(self, '_boss_counted', 0), confirmed or 0, expected or 0)
        self._boss_counted = counted
        state = (confirmed, expected, counted)
        if getattr(self, '_boss_last_record', None) == state:
            return
        # Keep the OCR observation distinct from progress counted by wins.
        self._boss_local_progress = dict(confirmed=confirmed, expected_after_settlement=expected,
                                        counted=counted)
        store = self.boss_progress_store()
        if store is not None:
            try:
                store.save(confirmed, expected, counted=counted)
            except (OSError, ValueError) as exc:
                logger.warning(f'BOSS checkpoint save failed; current run keeps its counted wins: {exc}')
                return
        self._boss_last_record = state

    def boss_page_visible(self):
        return self.appear(self.I_EVENT_BOSS_PAGE) and self.appear(self.I_EVENT_BOSS_CHALLENGE)

    def boss_share_page_visible(self):
        return (self.appear(self.I_EVENT_BOSS_SHARE_DAMAGE)
                and self.appear(self.I_EVENT_BOSS_SHARE_LINEUP)
                and self.appear(self.I_EVENT_BOSS_SHARE_CLOSE))

    def boss_close_share(self):
        if not self.boss_share_page_visible():
            self._boss_share_close_attempts = 0
            return False
        attempts = getattr(self, '_boss_share_close_attempts', 0)
        if attempts >= 3:
            raise GameStuckError('首领伤害分享页关闭三次后仍未消失')
        if self.click(self.I_EVENT_BOSS_SHARE_CLOSE, interval=1.5):
            self._boss_share_close_attempts = attempts + 1
            logger.info('BOSS伤害分享页：点击右上角关闭，继续结算')
        return True

    def _bw_interrupt_boss(self, pub, pri):
        # New personal-best damage can insert this page after rewards, before
        # the shared completion hook sees the normal challenge page.
        self.boss_close_share()
        return HookSignal.CONTINUE

    def _bw_completion_boss(self, pub, pri):
        if self.boss_close_share():
            return HookSignal.CONTINUE
        if not self.boss_page_visible():
            state = pri.per_battle
            if state.fallback_timer is None:
                state.fallback_timer = Timer(self.BOSS_PAGE_WAIT_SECONDS).start()
            if not state.fallback_timer.reached():
                return HookSignal.CONTINUE
            logger.warning('首领结算等待30秒仍未到挑战页，开始定位；不再随机点空白处')
            self.boss_goto_challenge(allow_battle=False)
        pub.per_task.count += 1
        return HookSignal.DONE

    def boss_read_bonus(self, expected_min=None):
        """Read progress briefly; unknown digits never prevent a challenge."""
        timeout = Timer(3).start()
        interval = Timer(0.6)
        previous = None
        consecutive = 0
        page_seen = False
        while not timeout.reached():
            self.screenshot()
            if not self.boss_page_visible():
                previous = None
                consecutive = 0
                continue
            page_seen = True
            if not interval.reached():
                continue
            interval.reset()
            completed, source = resolve_boss_bonus(
                self.O_EVENT_BOSS_BONUS.ocr_single(self.device.image),
                self.O_EVENT_BOSS_BONUS_BACKUP.ocr_single(self.device.image),
                expected_min)
            if completed is None:
                consecutive = 0
            elif completed == previous:
                consecutive += 1
            else:
                consecutive = 1
            # A terminal OCR value from one location needs another fresh frame.
            required = 3 if completed == 100 and source != 'both' else 2
            if completed is not None and consecutive >= required:
                self.boss_record_progress(completed)
                logger.info(f'BOSS extra rewards completed: {completed}/100; remaining: {100-completed}; source: {source}')
                return completed
            previous = completed
        if not page_seen or not self.boss_page_visible():
            raise GameStuckError('神降尘世未确认首领挑战页')
        logger.warning('BOSS奖励数字暂未读清，继续挑战；识别恢复后校准进度')
        return None

    BOSS_PAGE_WAIT_SECONDS = 30

    def _boss_wait_page(self, page=None):
        """Wait through a complete transition without navigation/back clicks."""
        timeout = Timer(self.BOSS_PAGE_WAIT_SECONDS).start()
        confirmed_frames = 0
        while not timeout.reached():
            self.screenshot()
            if self.boss_close_share():
                confirmed_frames = 0
                continue
            visible = (self.boss_page_visible() if page is None or page.key == 'page_climb_boss'
                       else self.match_page_once(page))
            if visible:
                confirmed_frames += 1
                if confirmed_frames >= 2:
                    return True
            else:
                confirmed_frames = 0
        return False

    def boss_goto_challenge(self, allow_battle=True):
        """Follow recognized page edges, once each, with a full 30s wait."""
        destination = self.navigator.pages['page_climb_boss']
        visited = set()
        for _ in range(8):
            self.screenshot()
            if self.boss_close_share():
                if self._boss_wait_page():
                    return
            current = self.get_current_page(skip_first_screenshot=True, fallback=True)
            if current is None:
                if self._boss_wait_page():
                    return
                raise GameStuckError('等待30秒后仍无法识别首领所在页面，停止操作，不点击返回')
            logger.info(f'BOSS当前位置: {current.key}')
            if current.key == 'page_climb_boss':
                if self._boss_wait_page():
                    return
                raise GameStuckError('首领挑战页未通过新截图确认，停止导航')
            if current.key in ('page_battle_prepare', 'page_battle', 'page_battle_result'):
                if not allow_battle:
                    raise GameStuckError('首领结算恢复仍检测到战斗页，停止嵌套接管')
                strategies, options = self.boss_battle_loadout()
                with battle_wait_strategy(**strategies), battle_wait_options(**options):
                    if not self.boss_battle_wait():
                        raise RequestHumanTakeover('接管首领战失败，请检查阵容')
                if self._boss_wait_page():
                    return
                continue
            path = self._build_path(current, destination)
            if not path or current.key in visited or path[0].destination.key in visited:
                raise GameStuckError(f'首领导航没有向前路径: {current.key}；停止，避免反复进退')
            visited.add(current.key)
            edge = path[0]
            logger.info(f'BOSS导航: {current.key} -> {edge.destination.key}；点击一次后等待30秒')
            if not self._execute_action(edge.action, interval=1.5, skip_first_screenshot=False):
                raise GameStuckError(f'首领入口未确认: {current.key}，停止重复点击')
            if self._boss_wait_page(edge.destination):
                if edge.destination.key == 'page_climb_boss':
                    return
                self._run_hooks(edge.destination.on_enter_success)
                continue
            # Only after the whole transition budget expires, locate again.
            actual = self.get_current_page(skip_first_screenshot=False, fallback=True)
            if actual is None or actual.key == current.key:
                where = actual.key if actual is not None else 'unknown'
                raise GameStuckError(f'首领导航等待30秒仍未到达目标，当前位置: {where}；停止，不补点返回')
        raise GameStuckError('首领导航超过8个步骤，停止操作')

    def boss_wait_return(self):
        if self._boss_wait_page():
            return
        logger.warning('首领结算后等待30秒仍未到挑战页，开始识别当前位置')
        self.boss_goto_challenge(allow_battle=False)

    def boss_battle_wait(self):
        """This timed boss can outlast the device's default five-minute wait."""
        original = self.device.stuck_timer_long
        self.device.stuck_timer_long = Timer(600, count=300).start()
        try:
            return self.battle_wait()
        finally:
            self.device.stuck_timer_long = original
            self.device.stuck_record_clear()

    def boss_battle_once(self):
        fixed = self.boss_fixed_limit()
        if self.boss_budget_exhausted():
            return False
        if fixed is None:
            before = self.boss_read_bonus(expected_min=getattr(self, '_boss_completed', None))
            if before is not None:
                self._boss_completed = before
                # boss_read_bonus also records real observations; keep this
                # explicit so callers supplying an observation behave alike.
                self.boss_record_progress(before)
            if self.boss_budget_exhausted():
                logger.info('BOSS可靠进度加后续胜场已达100，停止挑战')
                return False
        self.put_status()
        # Only one challenge click; keep all loading/unknown dialogs intact.
        self.click(self.I_EVENT_BOSS_CHALLENGE)
        timeout = Timer(45).start()
        while True:
            self.screenshot()
            if (self.is_in_battle(False) or self.appear(self.I_PREPARE_HIGHLIGHT)
                    or self.appear(self.I_REWARD) or self.appear(self.I_WIN)):
                break
            if timeout.reached():
                raise GameStuckError('神降尘世点击挑战后未进入战斗，不重复点击挑战')

        strategies, options = self.boss_battle_loadout()
        with battle_wait_strategy(**strategies), battle_wait_options(**options):
            win = self.boss_battle_wait()
        if not win:
            raise RequestHumanTakeover('神降尘世挑战失败，请检查阵容后再继续')
        self.boss_wait_return()
        # A confirmed victory counts even if its reward digits are unreadable.
        self.count_map['boss'] += 1
        if fixed is not None:
            logger.info(f"BOSS指定场次: {self.count_map['boss']}/{fixed}；完全忽略奖励数字")
            return not self.boss_budget_exhausted()
        self._boss_counted = getattr(self, '_boss_counted', 0) + 1
        self.boss_record_progress(getattr(self, '_boss_completed', None))
        if self.boss_budget_exhausted():
            logger.info('BOSS可靠进度加后续胜场或本次胜场已达100，停止挑战')
            return False
        after = self.boss_read_bonus(expected_min=getattr(self, '_boss_completed', None))
        if after is not None:
            self._boss_completed = after
            self.boss_record_progress(after)
        progress = f'{after}/100' if after is not None else '暂未识别'
        logger.info(f"BOSS completed this run: {self.count_map['boss']}; extra rewards: {progress}; "
                    f"counted: {self._boss_counted}/100")
        return not self.boss_budget_exhausted()

    def boss_battle_loadout(self):
        strategies, options = self.loadout_from_config(self.get_general_battle_conf())
        # This boss still opens manual preparation when presets are disabled.
        # Keep preparation active until the real battle marker appears.
        options['prepare'].lock_team = False
        strategies['success'] = 'soul'
        strategies['interrupt'] = 'boss'
        strategies['completion'] = 'boss'
        # Reuse the verified ticket settlement exclusions and shared battle
        # preparation/preset/wait hooks; return directly to this boss page.
        safe = ['C_TICKET_REWARD_BODY', 'C_TICKET_REWARD_HEADER', 'C_TICKET_REWARD_BUFF']
        options['success'].excludes_1 = safe.copy()
        options['success'].excludes_2 = safe.copy()
        options['completion'].check_imgs = [self.I_EVENT_BOSS_PAGE]
        options['completion'].excludes = safe + ['C_TICKET_REWARD_DETAILS']
        options['completion'].fallback_interval = self.BOSS_PAGE_WAIT_SECONDS
        options['completion'].fallback_limit = 0
        return strategies, options
