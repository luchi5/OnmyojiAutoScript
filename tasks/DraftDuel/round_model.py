"""Screen evidence shared by the full-series recognizer and controller."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoundView:
    phase: str
    captured: float
    actions: dict = field(default_factory=dict)
    wins: int | None = None
    losses: int | None = None
    outcome: str | None = None
    signature: str = ''
    evidence: tuple = ()

    def fresh(self, now, max_age=5):
        return 0 <= now-self.captured < max_age


# Action coordinates always come from observed buttons; never from this model.
# phases: unknown, lobby_ready, matching, deploying, battle, result,
# series_result, lobby_unopened, disconnected, popup, roster_detail.
# actions: match, cancel_match, auto, result_continue, collect, close, reconnect,
# open_roster, close_roster. A battle result carries outcome='win'/'loss'.
