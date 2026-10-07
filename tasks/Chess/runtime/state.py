"""Chess 的观察快照和局内状态；不负责截图、识别或点击。"""

from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class ChessObservation:
    """同一画面的已读取结果；None 表示尚未读取或识别失败。"""

    frame_id: str | int
    observed_at: float
    round_no: int | None = None
    mode: str | None = None
    level: int | None = None
    gold: int | None = None
    remaining_seconds: int | None = None
    in_game: bool | None = None
    shop_refresh_visible: bool | None = None
    game_rank: int | None = None
    grigri_visible: bool | None = None


@dataclass
class ChessRoundProgress:
    round_no: int
    phase: str = 'await_preparation'
    preparation_done: bool = False
    next_round_candidate: int | None = None
    next_round_confirmed: int = 0
    unknown_since: float | None = None
    battle_economy_done: bool = False
    battle_hand_cleanup_done: bool = False
    hyakki_round_seen: bool = False
    hyakki_lineup_checked: bool = False


@dataclass
class ChessGameState:
    """单局数据；局号变化时整体重置，避免继承上一局的动作进度。"""

    game_number: int = 0
    observation: ChessObservation | None = None
    round: ChessRoundProgress | None = None
    economy_pending: bool = False
    economy_step_state: str = 'idle'
    economy_sequence_level: int | None = None
    economy_sequence_index: int = 0
    economy_battle_mode: bool = False
    formation_pending: bool = False
    shop_assumed_open: bool = False
    board_lineup_names: set[str] = field(default_factory=set)
    board_actual_positions: dict[str, int] = field(default_factory=dict)
    player_deployed_positions: set[int] = field(default_factory=set)
    board_shikigami_attributes: dict[str, Any] = field(default_factory=dict)
    board_special_units: dict[str, Any] = field(default_factory=dict)
    arakawa_goldfish_current_position: int | None = None
    last_game_rank: int | None = None

    def begin_game(self) -> None:
        next_number = self.game_number + 1
        self.__dict__.update(type(self)(game_number=next_number).__dict__)

    def begin_round(self, round_no: int) -> ChessRoundProgress:
        self.round = ChessRoundProgress(round_no=int(round_no))
        return self.round

    def observe(self, frame_id: str | int, **values: Any) -> ChessObservation:
        current = self.observation
        if current is None or current.frame_id != frame_id:
            current = ChessObservation(frame_id=frame_id, observed_at=monotonic())
        self.observation = replace(current, **values)
        return self.observation
