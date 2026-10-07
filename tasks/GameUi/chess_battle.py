from __future__ import annotations

"""百鬼棋局导航兼容导出；实现独立加载，兼容已运行 worker 的模块缓存。"""

# 保留旧调用方及测试补丁使用的 time 导出。
import time
from tasks.Chess.runtime.result_navigation import ChessResultNavigationMixin


class ChessBattleNavigationMixin(ChessResultNavigationMixin):
    """让所有继承 GameUi 的任务使用统一棋局结算守卫。"""


def handle_chess_battle_page(task) -> bool:
    """GameUi 页面边动作：退出棋局战斗并返回棋局大厅。"""
    return task.exit_chess_battle()


def handle_chess_result_page(task) -> bool:
    """GameUi 页面边动作：完成遗留结算并返回棋局大厅。"""
    return task.return_to_chess_lobby()
