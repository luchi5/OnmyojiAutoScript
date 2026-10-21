"""Chess lineup, template loading, and hand-card recognition."""

# This Python file uses the following encoding: utf-8

from functools import cached_property
from pathlib import Path


from tasks.Chess.runtime.image import ChessImage as RuleImage
from tasks.Chess.runtime.frame_cards import ChessFrameCardsMixin
from module.logger import logger
from tasks.Chess.strategy.lineup import resolve_lineup_key
from tasks.Chess.strategy.shikigami_catalog import (
    SHIKIGAMI_BY_ROMAJI,
)
from tasks.Chess.strategy.soul_catalog import SOUL_BY_ROMAJI


CHESS_TASK_DIR = Path(__file__).resolve().parents[1]


class ChessRecognitionMixin(ChessFrameCardsMixin):
    """Internal mixin; use through ``ScriptTask`` only."""

    def get_lineup_strategy(
        self,
        lineup_key: str | None = None,
    ) -> dict:
        """返回当前阵容策略；新增体系只需实现同结构模块并注册。"""
        selected = (
            lineup_key
            or getattr(self, '_active_lineup_key', None)
            or self.DEFAULT_LINEUP_KEY
        )
        key = resolve_lineup_key(selected)
        entry = self.LINEUP_REGISTRY.get(key)
        if entry is None:
            logger.warning(
                f'Unknown Chess lineup strategy [{selected}], fallback to '
                f'[{self.DEFAULT_LINEUP_KEY}]'
            )
            entry = self.LINEUP_REGISTRY[self.DEFAULT_LINEUP_KEY]
        return entry['strategy']

    def select_lineup_strategy(self, lineup_key: str) -> dict:
        """切换当前阵容，并清除依赖阵容的图片规则缓存。"""
        strategy = self.get_lineup_strategy(lineup_key)
        self._active_lineup_key = strategy['key']
        for cache_name in (
            'hakuzosu_protect_rule',
        ):
            self.__dict__.pop(cache_name, None)
        logger.info(
            f'Select Chess lineup strategy: '
            f'{strategy["key"]} ({strategy["display_name"]})'
        )
        return strategy

    def _shikigami_display_name(self, name: str | None) -> str:
        """把内部罗马音转换成日志中更易读的中文名。"""
        if not name:
            return '未知'
        strategy_config = self.get_lineup_strategy()['shikigami'].get(name)
        if strategy_config is not None:
            return strategy_config.get('display_name', name)
        entry = SHIKIGAMI_BY_ROMAJI.get(name)
        return entry.chinese_name if entry is not None else str(name)


    @staticmethod
    def _soul_category_display(category: str | None) -> str:
        if category == 'attack':
            return '输出'
        if category == 'functional':
            return '功能'
        return str(category or '未知')

    @property
    def shikigami_deploy_positions(self) -> dict[str, int]:
        return {
            name: int(config['position'])
            for name, config in self.get_lineup_strategy()['shikigami'].items()
        }

    def _lineup_final_level(self) -> int:
        """阵容最终不保留经济的阶数，等于当前羁绊式神总人数。"""
        return len(self.get_lineup_strategy()['shikigami'])

    @classmethod
    def _load_hand_template_folder(
        cls,
        folder: str,
        prefix: str,
    ) -> list[tuple[str, RuleImage]]:
        """将指定目录中的 PNG 加载为整个手牌区域的识别模板。"""
        template_dir = CHESS_TASK_DIR / folder
        rules: list[tuple[str, RuleImage]] = []
        for file in sorted(template_dir.glob('*.png')):
            stem = file.stem
            if not stem.startswith(prefix):
                continue

            name = stem[len(prefix):]
            # `_1` 是从图鉴裁出的头像模板；完整手牌模板没有该后缀。
            # 两类模板归一为同一个式神名，并同时参与匹配。
            if folder == 'shikigami' and name.endswith('_1'):
                name = name[:-2]
            rule = RuleImage(profile="High",
                roi_front=(cls.HAND_AREA[0], cls.HAND_AREA[1], 1, 1),
                roi_back=cls.HAND_AREA,
                threshold=cls.HAND_TEMPLATE_THRESHOLD,
                method=RuleImage.METHOD_TEMPLATE_MATCH,
                file=file.as_posix(),
            )
            rules.append((name, rule))

        logger.debug(f'Loaded {len(rules)} Chess {folder} hand templates')
        return rules


    @cached_property
    def board_occupancy_rules(self) -> tuple[RuleImage, ...]:
        """直接加载三种场上勾玉；不依赖 assets/json 注册项。"""
        template_dir = CHESS_TASK_DIR / 'resource' / 'board'
        return tuple(
            RuleImage(profile="High",
                roi_front=(0, 0, 1, 1),
                roi_back=(0, 0, 1, 1),
                threshold=self.BOARD_OCCUPANCY_TEMPLATE_THRESHOLD,
                method=RuleImage.METHOD_TEMPLATE_MATCH,
                file=(template_dir / f'c_card_{index}.png').as_posix(),
            )
            for index in range(1, 4)
        )


    @cached_property
    def hakuzosu_protect_rule(self) -> RuleImage:
        """梦山白藏主伴生手牌：守护之印。"""
        file = (
            CHESS_TASK_DIR
            / self.HAKUZOSU_PROTECT_IMAGE
        )
        return RuleImage(profile="High",
            roi_front=(self.HAND_AREA[0], self.HAND_AREA[1], 1, 1),
            roi_back=self.HAND_AREA,
            threshold=self.HAND_TEMPLATE_THRESHOLD,
            method=RuleImage.METHOD_TEMPLATE_MATCH,
            file=file.as_posix(),
        )


    @cached_property
    def soul_hand_rules(self) -> list[tuple[str, RuleImage]]:
        """按拼音文件名加载御魂模板，运行时统一使用拼音键。"""
        rules = self._load_hand_template_folder('resource/soul', prefix='sou_')
        normalized = []
        for romaji, rule in rules:
            entry = SOUL_BY_ROMAJI.get(romaji)
            if entry is None:
                logger.warning(
                    f'Ignore unregistered Chess soul template: {romaji}'
                )
                continue
            normalized.append((entry.romaji, rule))
        return normalized

    def classify_hand_card(self, card_roi: tuple[int, int, int, int]) -> dict:
        """识别一个已定位的手牌框，未收录时返回 `unknown`。"""
        frame_card = self._frame_hand_in_roi(card_roi)
        if frame_card is not None:
            return dict(frame_card)
        x, y, width, height = card_roi
        return {
            'type': 'unknown',
            'name': None,
            'score': 0.0,
            'position': (x + width // 2, y + height // 2),
            'action': 'sell',
        }
