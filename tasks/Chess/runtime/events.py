"""同步内部事件；只报告状态变化，不启动后台轮询或并发点击。"""

from dataclasses import dataclass
from enum import Enum

from tasks.Chess.runtime.decision import ChessAction, ChessDecision
from tasks.Chess.runtime.state import ChessObservation


class ChessEventKind(str, Enum):
    MODE_CHANGED = 'mode_changed'
    ROUND_CONFIRMED = 'round_confirmed'
    GRIGRI_OPENED = 'grigri_opened'
    GAME_ENDED = 'game_ended'


@dataclass(frozen=True)
class ChessEvent:
    kind: ChessEventKind
    frame_id: str | int | None
    round_no: int | None = None
    mode: str | None = None


class ChessEventMonitor:
    """在原单线程循环中收集变化事件，避免旧事件跨局生效。"""

    def __init__(self) -> None:
        self._last_mode: str | None = None
        self._grigri_open = False

    def reset(self) -> None:
        self._last_mode = None
        self._grigri_open = False

    def collect(
        self,
        observation: ChessObservation | None,
        *,
        decision: ChessDecision | None = None,
        grigri_visible: bool = False,
        game_ended: bool = False,
    ) -> tuple[ChessEvent, ...]:
        frame_id = observation.frame_id if observation is not None else None
        round_no = observation.round_no if observation is not None else None
        mode = observation.mode if observation is not None else None
        events = []

        if mode is not None and mode != self._last_mode:
            events.append(ChessEvent(ChessEventKind.MODE_CHANGED, frame_id, round_no, mode))
            self._last_mode = mode
        if grigri_visible and not self._grigri_open:
            events.append(ChessEvent(ChessEventKind.GRIGRI_OPENED, frame_id, round_no, mode))
        self._grigri_open = grigri_visible
        if decision is not None and decision.action is ChessAction.NEXT_ROUND:
            events.append(ChessEvent(
                ChessEventKind.ROUND_CONFIRMED,
                frame_id,
                decision.next_round,
                mode,
            ))
        if game_ended:
            events.append(ChessEvent(ChessEventKind.GAME_ENDED, frame_id, round_no, mode))
        return tuple(events)
