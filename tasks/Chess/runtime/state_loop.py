"""Chess 状态驱动回合循环：沿用原动作，集中观察与单局状态。"""

import time

from module.exception import GameStuckError
from module.logger import logger
from tasks.Chess.runtime.decision import ChessAction, decide_round
from tasks.Chess.runtime.events import ChessEventKind, ChessEventMonitor
from tasks.Chess.runtime.state import ChessGameState


class ChessStateLoopMixin:
    def __init__(self, config, device) -> None:
        self.chess_state = ChessGameState()
        self.chess_events = ChessEventMonitor()
        super().__init__(config=config, device=device)

    def _record_observation(self, **values) -> None:
        image = getattr(self.device, 'image', None)
        if image is not None:
            frame_id = getattr(self.device, 'image_frame_id', None)
            self.chess_state.observe(frame_id or id(image), **values)

    def _read_round_number(self) -> int | None:
        value = super()._read_round_number()
        self._record_observation(round_no=value)
        return value

    def _read_chess_mode(self) -> str | None:
        value = super()._read_chess_mode()
        self._record_observation(mode=value)
        return value

    def _read_level(self) -> int | None:
        value = super()._read_level()
        self._record_observation(level=value)
        return value

    def _read_shop_gold(self) -> int | None:
        value = super()._read_shop_gold()
        self._record_observation(gold=value)
        return value

    def _read_remaining_time(self) -> int | None:
        value = super()._read_remaining_time()
        self._record_observation(remaining_seconds=value)
        return value

    def _is_in_chess_game(self) -> bool:
        value = super()._is_in_chess_game()
        self._record_observation(in_game=value)
        return value

    def _shop_refresh_marker_visible(self) -> bool:
        value = super()._shop_refresh_marker_visible()
        self._record_observation(shop_refresh_visible=value)
        return value

    def _read_game_rank(self) -> tuple[int | None, str]:
        value, raw = super()._read_game_rank()
        self._record_observation(game_rank=value)
        return value, raw

    def _start_chess_game(self) -> None:
        self.chess_state.begin_game()
        self.chess_events.reset()
        logger.info(f'Chess state: begin game {self.chess_state.game_number}')
        super()._start_chess_game()

    def _publish_chess_events(self, *, decision=None,
                              grigri_visible=False, game_ended=False):
        events = self.chess_events.collect(
            self.chess_state.observation,
            decision=decision,
            grigri_visible=grigri_visible,
            game_ended=game_ended,
        )
        for event in events:
            logger.debug(
                f'Chess event: {event.kind.value}, '
                f'round={event.round_no}, mode={event.mode}, '
                f'frame={event.frame_id}'
            )
        return events

    def _select_grigri_and_check_lineup(self) -> bool:
        """选符咒确认关闭后，按当前棋盘画面核对上阵名单。"""
        if not self.select_grigri():
            return False
        deadline = time.monotonic() + 5
        current_round = None
        while time.monotonic() < deadline:
            self.screenshot()
            current_round = self._read_round_number()
            if current_round is not None and self._read_chess_mode() == '备':
                break
            time.sleep(self.SLOW_POLL_INTERVAL)
        else:
            logger.warning(
                'Skip post-grigri lineup check: preparation board not '
                'confirmed within 5 seconds'
            )
            return True
        if current_round in self.BOSS_CHALLENGE_ROUNDS:
            logger.info(
                'Skip post-grigri lineup check during boss round: '
                f'round={current_round}'
            )
            return True
        logger.info(f'Chess post-grigri lineup check: round={current_round}')
        self._reconcile_lineup_after_hyakki_round(next_round_no=current_round)
        return True

    def run_one_round(self, round_no: int) -> int | None:
        """观察→决策→同步执行；原动作及优先级不变。"""
        progress = self.chess_state.begin_round(round_no)
        self._current_round_no = int(round_no)
        logger.debug(f'Chess round {round_no}')

        while True:
            self.device.stuck_record_clear()
            self.screenshot()
            if self.appear(self.I_SELECT_GRIGRI):
                self._record_observation(grigri_visible=True)
                self._publish_chess_events(grigri_visible=True)
                logger.info(
                    'Chess round-state refresh interrupted by grigri selection; '
                    'resolve grigri before reading round and mode'
                )
                self._select_grigri_and_check_lineup()
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue
            self._record_observation(grigri_visible=False)
            if self._finish_chess_game_after_markers_missing(
                f'round_{round_no}'
            ):
                self._publish_chess_events(game_ended=True)
                return None
            if getattr(self, '_rank_protection_exit_requested', False):
                logger.info(
                    'Chess rank protection: actively exit this game; '
                    'the game will not count toward completed runs'
                )
                if self.exit_chess_battle(return_to_lobby=False):
                    self._rank_protection_exit_succeeded = True
                    self._publish_chess_events(game_ended=True)
                    return None
                logger.warning(
                    'Chess rank-protection exit was unavailable; '
                    'retry on the next state refresh'
                )
            observed_round = self._read_round_number()
            mode = self._read_chess_mode()
            round_transition_pending = (
                observed_round is not None and observed_round != round_no
            )
            # 首帧回目变化时立即冻结旧回目的后续检测和动作。
            in_game = True if round_transition_pending else (
                mode is not None or self._is_in_chess_game()
            )
            grigri_visible = (
                self.appear(self.I_SELECT_GRIGRI)
                if not round_transition_pending and mode == '备'
                else False
            )
            self._record_observation(
                in_game=in_game, grigri_visible=grigri_visible,
            )
            decision = decide_round(
                self.chess_state.observation,
                progress,
                now=time.monotonic(),
                confirm_frames=self.ROUND_CONFIRM_FRAMES,
                unknown_timeout=self.UNKNOWN_STATE_TIMEOUT,
                in_game=in_game,
                grigri_visible=grigri_visible,
            )
            progress = decision.progress
            self.chess_state.round = progress
            events = self._publish_chess_events(
                decision=decision, grigri_visible=grigri_visible,
            )

            if any(event.kind is ChessEventKind.ROUND_CONFIRMED for event in events):
                logger.debug(
                    f'Chess round boundary confirmed: {round_no} -> '
                    f'{observed_round}, phase={progress.phase}, '
                    f'preparation_done={progress.preparation_done}'
                )
                return observed_round

            if decision.action is ChessAction.ROUND_PENDING:
                time.sleep(self.SLOW_POLL_INTERVAL)
                continue
            if decision.action is ChessAction.LOST_MARKERS:
                raise GameStuckError(
                    f'Chess: lost all markers during round {round_no}'
                )

            if decision.action is ChessAction.RESOLVE_GRIGRI:
                logger.debug('Chess preparation interrupted by grigri selection')
                self._select_grigri_and_check_lineup()
                continue

            if decision.action in (ChessAction.BATTLE, ChessAction.PASSIVE):
                if mode == '鬼' and not progress.hyakki_round_seen:
                    progress.hyakki_round_seen = True
                    logger.info(
                        f'Chess Hyakki mode observed: round={round_no}'
                    )
                if decision.action is ChessAction.BATTLE:
                    if not progress.battle_hand_cleanup_done:
                        progress.battle_hand_cleanup_done = (
                            self._handle_battle_sell_stage()
                        )
                    if (
                        progress.battle_hand_cleanup_done
                        and not progress.battle_economy_done
                        and self._read_chess_mode() == '战'
                    ):
                        self._run_battle_economy_until_budget_limit()
                        progress.battle_economy_done = True
                    self._handle_passive_stage('战')
                else:
                    self._handle_passive_stage(mode)
                if progress.phase == 'await_preparation':
                    progress.preparation_done = True
                    progress.phase = 'await_battle_end'
                    logger.warning(
                        f'Chess round {round_no}: resumed in passive mode '
                        f'{mode}; treat preparation as already passed'
                    )

            elif decision.action is ChessAction.PREPARE:
                if progress.phase == 'await_preparation':
                    self.purchase_lineup_cards_once()
                    self._run_preparation_economy_until_time_limit()
                    if not self._is_preparation_mode():
                        time.sleep(self.SLOW_POLL_INTERVAL)
                        continue
                    if self._handle_preparation_stage(1):
                        progress.preparation_done = True
                        progress.phase = 'await_battle'
                        logger.debug(
                            f'Chess round {round_no}: preparation '
                            'complete; wait for 战/鬼/待'
                        )

            interval = (
                3 * self.SLOW_POLL_INTERVAL
                if mode == '鬼'
                else self.SLOW_POLL_INTERVAL
            )
            time.sleep(interval)


def _game_state_property(name):
    """让旧执行方法读写同一份状态，避免维护两套可变副本。"""
    return property(
        lambda self: getattr(self.chess_state, name),
        lambda self, value: setattr(self.chess_state, name, value),
    )


_GAME_STATE_FIELDS = {
    '_economy_pending': 'economy_pending',
    '_economy_step_state': 'economy_step_state',
    '_economy_sequence_level': 'economy_sequence_level',
    '_economy_sequence_index': 'economy_sequence_index',
    '_economy_battle_mode': 'economy_battle_mode',
    '_formation_pending': 'formation_pending',
    '_shop_assumed_open': 'shop_assumed_open',
    '_board_lineup_names': 'board_lineup_names',
    '_board_actual_positions': 'board_actual_positions',
    '_player_deployed_positions': 'player_deployed_positions',
    '_board_shikigami_attributes': 'board_shikigami_attributes',
    '_board_special_units': 'board_special_units',
    '_arakawa_goldfish_current_position': 'arakawa_goldfish_current_position',
    '_last_game_rank': 'last_game_rank',
}
for _legacy_name, _state_name in _GAME_STATE_FIELDS.items():
    setattr(ChessStateLoopMixin, _legacy_name, _game_state_property(_state_name))
