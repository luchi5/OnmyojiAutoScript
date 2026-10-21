# This Python file uses the following encoding: utf-8

"""百鬼棋局冥火阵容配置。"""

from tasks.Chess.strategy.lineup_strategy import build_lineup_strategy


MINGHUO_CONFIG = {
    'key': 'minghuo',
    'display_name': '冥火',
    'hakuzosu_protect_position': None,
    'shikigami_positions': {
        '思金神': (1, 1, ()),
        '鬼使白': (1, 2, ()),
        '寻香行': (2, 3, ()),
        '判官': (1, 4, ()),
        '荒骷髅': (1, 5, ()),
        '鬼使黑': (2, 6, ()),
        '黑童子': (1, 7, ()),
        '平将门': (2, 8, ()),
        '白童子': (1, 9, ()),
    },
}


MINGHUO = build_lineup_strategy(MINGHUO_CONFIG)
