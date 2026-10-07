"""Chess 的纯决策层：观察和进度入，下一步动作出。"""

from dataclasses import dataclass, replace
from enum import Enum

from tasks.Chess.runtime.state import ChessObservation, ChessRoundProgress


class ChessAction(str, Enum):
    WAIT = 'wait'
    RESOLVE_GRIGRI = 'resolve_grigri'
    ROUND_PENDING = 'round_pending'
    NEXT_ROUND = 'next_round'
    LOST_MARKERS = 'lost_markers'
    PREPARE = 'prepare'
    BATTLE = 'battle'
    PASSIVE = 'passive'


@dataclass(frozen=True)
class ChessDecision:
    action: ChessAction
    progress: ChessRoundProgress
    mode: str | None = None
    next_round: int | None = None


def decide_round(
    observation: ChessObservation,
    progress: ChessRoundProgress,
    *,
    now: float,
    confirm_frames: int,
    unknown_timeout: float,
    in_game: bool,
    grigri_visible: bool,
) -> ChessDecision:
    """保持原优先级；不读取画面、不点击、不修改传入的进度。"""
    state = replace(progress)
    observed_round = observation.round_no
    mode = observation.mode

    if observed_round is not None and observed_round != state.round_no:
        if observed_round == state.next_round_candidate:
            state.next_round_confirmed += 1
        else:
            state.next_round_candidate = observed_round
            state.next_round_confirmed = 1
        if state.next_round_confirmed >= confirm_frames:
            return ChessDecision(
                ChessAction.NEXT_ROUND, state, mode, observed_round,
            )
        return ChessDecision(ChessAction.ROUND_PENDING, state, mode)

    state.next_round_candidate = None
    state.next_round_confirmed = 0
    if in_game:
        state.unknown_since = None
    elif state.unknown_since is None:
        state.unknown_since = now
    elif now - state.unknown_since >= unknown_timeout:
        return ChessDecision(ChessAction.LOST_MARKERS, state, mode)

    if mode == '备' and grigri_visible:
        return ChessDecision(ChessAction.RESOLVE_GRIGRI, state, mode)
    if mode in ('战', '鬼', '待'):
        action = ChessAction.BATTLE if mode == '战' else ChessAction.PASSIVE
        return ChessDecision(action, state, mode)
    if mode == '备' and state.phase == 'await_preparation':
        return ChessDecision(ChessAction.PREPARE, state, mode)
    return ChessDecision(ChessAction.WAIT, state, mode)
