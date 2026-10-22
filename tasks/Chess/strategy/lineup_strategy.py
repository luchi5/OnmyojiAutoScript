# This Python file uses the following encoding: utf-8

"""百鬼棋局阵容羁绊配置。

阵容配置只描述阵容自身：

- ``shikigami_positions``：式神 ->
  ``(上阵权重, 站位, (限定御魂...))``。权重越低越
  优先上阵；同权重仍按手牌从左到右处理。
- ``hakuzosu_protect_position``：守护之印应装备到的式神站位；不使用
  守护之印时为 ``None``。
- ``arakawa_goldfish_position``：荒川羁绊召唤金鱼后应移动到的位置。
  不使用荒川金鱼时为 ``None``；仅在下阵失败后打开失败位置详情，
  被动确认金鱼并移动到该位置；上阵确认失败后不尝试识别金鱼。
- ``secondary_bond``：显式指定次要羁绊；未指定时按阵容羁绊计数选择。

经济策略属于通用运营流程，写在主任务中，不放入阵容配置。
"""

from tasks.Chess.strategy.shikigami_catalog import build_lineup_shikigami


def build_lineup_strategy(config: dict) -> dict:
    """把轻量阵容配置转换成主程序使用的标准结构。"""
    strategy = {
        'key': config['key'],
        'display_name': config['display_name'],
        'shikigami': build_lineup_shikigami(
            config.get('shikigami_positions', {})
        ),
    }
    if config.get('secondary_bond'):
        strategy['secondary_bond'] = config['secondary_bond']
    protect_position = config.get('hakuzosu_protect_position')
    if protect_position is not None:
        protect_position = int(protect_position)
        if not 1 <= protect_position <= 12:
            raise ValueError(
                'hakuzosu_protect_position must be between 1 and 12'
            )
    strategy['hakuzosu_protect_position'] = protect_position
    goldfish_position = config.get('arakawa_goldfish_position')
    if goldfish_position is not None:
        goldfish_position = int(goldfish_position)
        if not 1 <= goldfish_position <= 12:
            raise ValueError(
                'arakawa_goldfish_position must be between 1 and 12'
            )
    strategy['arakawa_goldfish_position'] = goldfish_position
    return strategy


QIJIAOSHAN_CONFIG = {
    'key': 'qijiaoshan',
    'display_name': '七角山',
    'hakuzosu_protect_position': None,
    'shikigami_positions': {
        '薰': (1, 1, ('魍魉之匣', '钓瓶火')),
        '白狼': (1, 2, ()),
        '鲸汐千姬': (1, 3, ()),
        '小松丸': (1, 4, ()),
        '一目连': (2, 5, ()),
        '山风': (2, 6, ()),
        '萤草': (1, 7, ()),
        '季': (1, 8, ()),
    },
}

QIJIAOSHAN = build_lineup_strategy(QIJIAOSHAN_CONFIG)


HAIGUO_CONFIG = {
    'key': 'haiguo',
    'display_name': '海国',
    'hakuzosu_protect_position': None,
    'shikigami_positions': {
        '灵海蝶': (1, 1, ()),
        '蟹姬': (1, 2, ()),
        '化鲸': (1, 3, ()),
        '久次良': (1, 4, ()),
        '铃鹿御前': (1, 5, ()),
        '河童': (1, 6, ()),
        '椒图': (2, 7, ()),
        '大岳丸': (1, 8, ()),
        '季': (2, 10, ()),
    },
}


HAIGUO = build_lineup_strategy(HAIGUO_CONFIG)


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


PINGANJING_CONFIG = {
    'key': 'pinganjing',
    'display_name': '平安京',
    'secondary_bond': '追击',
    'hakuzosu_protect_position': 1,
    'shikigami_positions': {
        '青行灯': (1, 1, ()),
        '姑获鸟': (1, 2, ()),
        '御馔津': (1, 3, ()),
        '妖刀姬': (1, 4, ()),
        '花鸟卷': (1, 5, ()),
        '鬼切': (1, 6, ()),
        '夜叉': (1, 7, ()),
        '山兔': (1, 8, ()),
        '梦山白藏主': (1, 9, ('招财猫', '狰')),
    },
}


PINGANJING = build_lineup_strategy(PINGANJING_CONFIG)
