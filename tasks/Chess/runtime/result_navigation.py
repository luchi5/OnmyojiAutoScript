from __future__ import annotations

"""百鬼棋局局内页面的全局恢复与退出流程。"""

import time
import json
from pathlib import Path

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from tasks.GameUi.assets import GameUiAssets
from module.exception import GameStuckError
from module.logger import logger
from tasks.Chess.assets import ChessAssets


def _share_panel_rule(name: str) -> RuleImage:
    """兼容旧 worker 缓存的 GameUiAssets；新规则始终以资源 JSON 为准。"""
    asset_name = 'I_' + name.upper()
    existing = getattr(GameUiAssets, asset_name, None)
    if existing is not None:
        return existing
    resource = Path(__file__).resolve().parents[2] / 'GameUi' / 'page' / 'image_chess.json'
    items = json.loads(resource.read_text(encoding='utf-8'))
    item = next(item for item in items if item['itemName'] == name)
    return RuleImage(
        roi_front=tuple(map(int, item['roiFront'].split(','))),
        roi_back=tuple(map(int, item['roiBack'].split(','))),
        method=item['method'], threshold=item['threshold'],
        file=str(resource.parent / item['imageName']),
    )


class ChessResultNavigationMixin:
    """让所有继承 GameUi 的任务都能退出遗留的百鬼棋局。"""

    I_CHESS_SHARE_PANEL_BAR = _share_panel_rule('chess_share_panel_bar')
    I_CHESS_SHARE_PANEL_CLOSE = _share_panel_rule('chess_share_panel_close')

    CHESS_EXIT_TIMEOUT = 60.0
    CHESS_EXIT_SCREENSHOT_INTERVAL = 0.35
    CHESS_LOBBY_CONFIRM_FRAMES = 2
    # 棋局右下角的开战和分享按钮位置重叠，结算推进只点击左侧空白。
    # 使用固定的规则名称，让主仓库连续点击保护仍正常累计。
    CHESS_RESULT_CONTINUE = RuleClick(
        roi_front=(24, 150, 40, 280), roi_back=(24, 150, 40, 280),
        name='CHESS_RESULT_CONTINUE',
    )

    def chess_share_panel_visible(self) -> bool:
        """棋局分享标志、平台栏和右上关闭按钮同时出现才关闭面板。"""
        return (
            self.appear(ChessAssets.I_SHARE)
            and self.appear(self.I_CHESS_SHARE_PANEL_BAR)
            and self.appear(self.I_CHESS_SHARE_PANEL_CLOSE)
        )

    def chess_lobby_visible(self) -> bool:
        """开战按钮匹配前先排除结算；两个旧标志都来自同一按钮。"""
        return (
            not self.chess_result_page_visible()
            and self.appear(self.I_CHECK_CHESS)
            and self.appear(ChessAssets.I_CHESS_START)
        )

    def _click_chess_result_continue(self) -> None:
        self.click(self.CHESS_RESULT_CONTINUE, interval=1.5)

    def _advance_chess_result_stage(self) -> bool:
        """优先处理奖励和分享阶段，避免背景被识别成大厅。"""
        if self.chess_share_panel_visible():
            self._chess_share_advancing = True
            logger.info('Chess share panel detected; close the panel without sharing')
            self.click(self.I_CHESS_SHARE_PANEL_CLOSE, interval=1.5)
            return True
        if self.appear(ChessAssets.I_REWARD_CHESS):
            self._chess_share_advancing = False
            self._click_chess_result_continue()
            return True
        # 过渡帧可能仍保留“分享”图标；明确的排名/返回按钮比该图标
        # 更能确定下一步，交回调用方处理，保留“再来一局”原流程。
        if any(self.appear(marker) for marker in (
            ChessAssets.I_RESTART_AGAIN,
            self.I_CHECK_CHESS_RANK,
            self.I_CHESS_RANK_GOTO_LOBBY,
            self.I_CHESS_EXIT_TO_LOBBY,
            self.I_CHESS_EXIT_TO_LOBBY_2,
        )):
            self._chess_share_advancing = False
            return False
        if self.appear(ChessAssets.I_SHARE):
            self._chess_share_advancing = True
        elif getattr(self, '_chess_share_advancing', False):
            if self.chess_lobby_visible():
                self._chess_share_advancing = False
        if getattr(self, '_chess_share_advancing', False):
            # 分享标志消失后仍推进未知过渡画面。保持规则名称和点击
            # 记录不变，让设备层的过多点击检查正常生效。
            self._click_chess_result_continue()
            return True
        return False

    def chess_result_page_visible(self) -> bool:
        """检测百鬼棋局任一结算、分享或排名页面。"""
        return (
            self.chess_share_panel_visible()
            or self.appear(self.I_CHESS_EXIT_TO_LOBBY)
            or self.appear(self.I_CHESS_EXIT_TO_LOBBY_2)
            or self.appear(ChessAssets.I_REWARD_CHESS)
            or self.appear(ChessAssets.I_SHARE)
            or self.appear(self.I_CHECK_CHESS_RANK)
            or self.appear(self.I_CHESS_RANK_GOTO_LOBBY)
            or self.appear(ChessAssets.I_RESTART_AGAIN)
        )

    def chess_result_flow_visible(self) -> bool:
        """检测百鬼棋局大厅或任一结算页面。"""
        return (
            self.chess_lobby_visible()
            or self.chess_result_page_visible()
        )

    def return_to_chess_lobby(self) -> bool:
        """完成返回按钮、分享页与排名页流程，最终回到棋局大厅。"""
        logger.debug('Global Chess result flow: return to lobby')
        self._chess_share_advancing = False
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        share_seen = False
        exit_clicked = False
        safe_clicks = 0
        lobby_frames = 0
        next_rank_safe_click_at = 0.0
        fallback_exit_at = time.monotonic() + 1.5

        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()

            if self._advance_chess_result_stage():
                lobby_frames = 0
                exit_clicked = True
                share_seen = True
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if self.chess_lobby_visible():
                lobby_frames += 1
                if lobby_frames >= self.CHESS_LOBBY_CONFIRM_FRAMES:
                    logger.debug('Global Chess result flow: stable lobby reached')
                    return True
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue
            lobby_frames = 0

            rank_page = self.appear(self.I_CHECK_CHESS_RANK)
            rank_button = self.appear(self.I_CHESS_RANK_GOTO_LOBBY)
            if rank_page or rank_button:
                if rank_button:
                    self.appear_then_click(
                        self.I_CHESS_RANK_GOTO_LOBBY,
                        interval=1.5,
                    )
                elif time.monotonic() >= next_rank_safe_click_at:
                    # 部分结算只显示“点击空白处继续”，没有返回大厅按钮。
                    logger.info(
                        'Global Chess result flow: advance rank page with '
                        'safe click'
                    )
                    self._click_chess_result_continue()
                    safe_clicks += 1
                    next_rank_safe_click_at = time.monotonic() + 1.5
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            # 奖励弹窗推进不代表真实返回按钮已经按下；只要按钮仍被
            # 当前帧识别到，就按主仓库间隔规则处理，不被历史标志跳过。
            if (
                not exit_clicked
                or self.appear(self.I_CHESS_EXIT_TO_LOBBY)
                or self.appear(self.I_CHESS_EXIT_TO_LOBBY_2)
            ):
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(self.I_CHESS_EXIT_TO_LOBBY_2):
                    self.appear_then_click(
                        self.I_CHESS_EXIT_TO_LOBBY_2,
                        interval=1.5,
                    )
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                if self.appear(ChessAssets.I_SHARE):
                    exit_clicked = True
                    share_seen = True
                    continue
                if time.monotonic() >= fallback_exit_at:
                    logger.warning(
                        'Global Chess result flow: return image missed; '
                        'click fixed return area'
                    )
                    self.click(self.I_CHESS_EXIT_TO_LOBBY)
                    exit_clicked = True
                    time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                    continue
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if not share_seen and self.appear(ChessAssets.I_SHARE):
                share_seen = True

            if not share_seen:
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            safe_clicks += 1
            self._click_chess_result_continue()
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)

        raise GameStuckError('Global Chess: failed to return to lobby after result')

    def exit_chess_battle(self, return_to_lobby: bool = True) -> bool:
        """主动退出当前百鬼棋局，可选择停在结算流程。"""
        logger.warning('Global Chess page handler: exit interrupted battle')
        deadline = time.monotonic() + self.CHESS_EXIT_TIMEOUT
        next_exit_click_at = 0.0
        next_confirm_click_at = 0.0
        dialog_seen = False
        confirm_clicked = False

        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()

            confirm_visible = self.appear(self.I_CHESS_EXIT_CONFIRM)
            cancel_visible = self.appear(self.I_CHESS_EXIT_CANCEL)
            if confirm_visible or cancel_visible:
                dialog_seen = True

            if dialog_seen and confirm_clicked and not confirm_visible:
                logger.debug('Global Chess page handler: exit confirmed')
                if return_to_lobby:
                    return self.return_to_chess_lobby()
                return True

            now = time.monotonic()
            if dialog_seen:
                if confirm_visible and now >= next_confirm_click_at:
                    self.click(self.I_CHESS_EXIT_CONFIRM)
                    confirm_clicked = True
                    next_confirm_click_at = now + 2.0
                time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)
                continue

            if now >= next_exit_click_at:
                if self.appear(self.I_CHESS_EXIT):
                    self.click(self.I_CHESS_EXIT)
                next_exit_click_at = now + 2.0
            time.sleep(self.CHESS_EXIT_SCREENSHOT_INTERVAL)

        logger.warning(
            'Global Chess page handler timed out: '
            f'dialog_seen={dialog_seen}, confirm_clicked={confirm_clicked}'
        )
        return False



def chess_game_ui_type(game_ui):
    """新加载的 Chess 使用新守卫，不要求重载旧 worker 的全局导航。"""
    if issubclass(game_ui, ChessResultNavigationMixin):
        return game_ui

    class ChessGameUi(ChessResultNavigationMixin, game_ui):
        pass

    return ChessGameUi
