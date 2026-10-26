"""保住前四退局：完整八人名单、多帧确认，未知画面继续正常对局。"""

from functools import lru_cache
from pathlib import Path
import re
import time

import cv2
import numpy as np

from module.atom.ocr import RuleOcr
from module.exception import GameStuckError
from module.logger import logger


_Y, _X = np.mgrid[:52, :52]
_RADIUS_SQUARED = (_X - 26) ** 2 + (_Y - 26) ** 2
_RIM_MASK = (_RADIUS_SQUARED >= 21 ** 2) & (_RADIUS_SQUARED <= 24 ** 2)


@lru_cache(maxsize=1)
def _eliminated_rim():
    path = Path(__file__).resolve().parents[1] / 'res/eliminated_player_rim.png'
    image = cv2.imread(str(path))
    if image is None or image.shape != (52, 52, 3):
        return None
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.int16)


def scoreboard_alive_count(image) -> int | None:
    """只接受普通布局中连续排列的存活行与明确淘汰行。

    淘汰行必须同时匹配灰暗的头像外圈、无血量文字。不能把 OCR
    读不到血量、少了头像、商店遮挡或暗色弹窗当作玩家已经淘汰。
    模板仅保留公共外圈，包含八行的检测不依赖玩家名字或头像内容。
    """
    if image is None or image.shape != (720, 1280, 3):
        return None
    reference = _eliminated_rim()
    if reference is None:
        return None
    alive = 0
    eliminated_seen = False
    for row in range(8):
        cy = 72 + 62 * row
        rim = image[cy - 26:cy + 26, 1218:1270].astype(np.int16)
        delta = np.max(np.abs(rim - reference), axis=2)[_RIM_MASK]
        eliminated_rim = (
            np.median(delta) <= 6 and np.mean(delta <= 12) >= 0.65
        )
        hp = image[cy + 4:cy + 28, 1180:1218]
        gray = cv2.cvtColor(hp, cv2.COLOR_RGB2GRAY)
        bright = (hp[:, :, 0] > 170) & (hp[:, :, 1] > 150) & (hp[:, :, 2] > 85)
        if eliminated_rim and not bright.any() and np.percentile(gray, 95) < 90:
            eliminated_seen = True
        elif not eliminated_rim and bright.sum() >= 15 and np.percentile(gray, 95) >= 150:
            if eliminated_seen:
                return None  # 排名变化动画或识别不完整。
            alive += 1
        else:
            return None
    return alive if 1 <= alive <= 8 else None


def parse_player_health(raw) -> int | None:
    """血量严格读取；空值与模糊字符不替换为零。"""
    value = str(raw).strip()
    if not re.fullmatch(r'[1-9][0-9]?|100', value):
        return None
    return int(value)


class ChessTopFourExitMixin:
    def _top_four_exit_enabled(self) -> bool:
        config = self.config.chess.chess_config
        return bool(
            config.coin_full_exit
            and getattr(config, 'top_four_exit', True)
            and not getattr(self, '_top_four_exit_blocked', False)
        )

    def _check_top_four_exit(self, round_no: int, mode: str | None) -> bool:
        """20 回目起，新回合的备阶段先收起自动展开的商店。"""
        if (
            not self._top_four_exit_enabled()
            or round_no < self.TOP_FOUR_MIN_ROUND
            or mode != '备'
            or getattr(self, '_rank_protection_exit_requested', False)
        ):
            return False
        if mode == '备' and getattr(self, '_top_four_shop_check_round', None) != round_no:
            # 每回合仅为检测主动收店一次；后续买卡仍走原来的开店流程。
            self._top_four_shop_check_round = round_no
            if self._is_shop_open():
                logger.info(f'Chess round {round_no}: close automatic shop to inspect survivors')
                if not self._ensure_shop_closed(allowed_modes=('备',)):
                    return False
                self.screenshot()
                mode = self._read_chess_mode()
        return self._try_top_four_exit(mode)

    def _read_surviving_player_count(self) -> int | None:
        count = scoreboard_alive_count(self.device.image)
        if count is None or count > 4:
            return count
        # 先用图像筛选，只有候选前四局面才做血量 OCR，避免拖慢日常回合。
        health = []
        for row in range(count):
            y = 76 + 62 * row
            crop = self.device.image[y:y + 24, 1180:1218]
            # 小字直接 OCR 会丢首位或混入背景笔画；仅提取亮字并放大。
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            digits = cv2.cvtColor((gray > 140).astype(np.uint8) * 255, cv2.COLOR_GRAY2RGB)
            digits = cv2.resize(digits, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            digits = cv2.copyMakeBorder(digits, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            height, width = digits.shape[:2]
            roi = (0, 0, width, height)
            rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                           keyword='', name=f'chess_player_health_{row + 1}')
            value = parse_player_health(rule.ocr(digits))
            if value is None:
                return None
            health.append(value)
        if health != sorted(health, reverse=True):
            return None
        return count

    def _try_top_four_exit(self, mode: str | None) -> bool:
        if (
            not self._top_four_exit_enabled()
            or mode != '备'
            or getattr(self, '_rank_protection_exit_requested', False)
        ):
            return False
        # 战斗中的灰头像/血量消失不是最终淘汰证明，连续多帧也可能
        # 低估存活人数。只接受同一备战回合的名单；战、待及跨回合
        # 画面一律不触发主动退出。
        confirmed_round = None
        candidate = None
        previous_image = None
        frames = []
        # 短间隔连续截图；计数仅存在本次调用内，绝不跨回合/买卡操作累计。
        for frame_index in range(self.TOP_FOUR_CONFIRM_FRAMES):
            if frame_index:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
                if self.device.image is previous_image:
                    return False
                mode = self._read_chess_mode()
            if (
                mode != '备'
                or not self._is_in_chess_game()
                or self._is_shop_open()
                or self.appear(self.I_SELECT_GRIGRI)
                or self.chess_result_page_visible()
            ):
                return False
            round_no = self._read_round_number()
            if (
                round_no is None
                or round_no < self.TOP_FOUR_MIN_ROUND
                or (confirmed_round is not None and round_no != confirmed_round)
            ):
                return False
            confirmed_round = round_no
            previous_image = self.device.image
            count = self._read_surviving_player_count()
            if count is None or not 1 <= count <= 4:
                return False
            if candidate is not None and count != candidate:
                return False
            candidate = count
            frames.append(self.device.image.copy())
        self._top_four_evidence_id = str(time.time_ns())
        for index, frame in enumerate(frames, 1):
            self._save_top_four_evidence(f'before-{index}', frame)
        logger.info(
            f'Chess weekly coins: {count} survivors confirmed in preparation '
            f'round {confirmed_round}; requesting exit, final rank pending'
        )
        self._top_four_exit_succeeded = False
        if not self.exit_chess_battle(return_to_lobby=False):
            # 可能已经点击确认，不能将未知结果当作仍可继续对局。
            raise GameStuckError('Chess: top-four exit was not confirmed')
        self._wait_for_top_four_result()
        rank = self._last_game_rank
        self._top_four_exit_succeeded = rank is not None and 1 <= rank <= 4
        if not self._top_four_exit_succeeded:
            self._top_four_exit_blocked = True
            logger.warning(
                f'Chess top-four exit not verified: rank={rank}; '
                'disable early exits for the rest of this task run'
            )
        return True

    def _save_top_four_evidence(self, stage: str, image) -> None:
        """保存退局前的三帧和结算页，供核对实际名单与最终名次。"""
        try:
            evidence_id = getattr(self, '_top_four_evidence_id', str(time.time_ns()))
            folder = Path('log/chess-top-four') / evidence_id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'{stage}.png'
            ok, encoded = cv2.imencode('.png', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            if not ok:
                raise ValueError('PNG encoding failed')
            path.write_bytes(encoded.tobytes())
            logger.info(f'Chess top-four evidence: {path.resolve()}')
        except Exception as exc:
            logger.warning(f'Chess top-four evidence unavailable: {exc}')

    def _wait_for_top_four_result(self) -> None:
        """确认到达结算；实际名次来自结算 OCR，不能按剩余人数填写。"""
        self._last_game_rank = None
        deadline = time.monotonic() + self.GAME_OVER_WAIT_TIMEOUT
        while True:
            self.screenshot()
            self.device.stuck_record_clear()
            if self.appear(self.I_GAME_OVER_1) or self.appear(self.I_GAME_OVER_2):
                self._last_game_rank, raw = self._read_game_rank()
                self._save_top_four_evidence('result', self.device.image)
                logger.info(f'Chess top-four exit result: rank={self._last_game_rank}, OCR=[{raw}]')
                return
            if self.chess_result_page_visible():
                self._save_top_four_evidence('result-unknown', self.device.image)
                logger.info('Chess top-four exit reached result flow; rank remains unknown')
                return
            if time.monotonic() >= deadline:
                raise GameStuckError('Chess: top-four exit did not reach result flow')
            time.sleep(self.SCREENSHOT_INTERVAL)
