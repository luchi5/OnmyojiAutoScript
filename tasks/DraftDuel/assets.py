"""Assets measured from the 2026-10-18 closed-lobby screenshot, at 1280x720.

Source: tests/fixtures/draft_duel/lobby_closed_20261018.png. No draft, lineup,
battle, invitation dialog, or open-lobby controls have been sampled yet.
Coordinates are (x, y, width, height), and must not be scaled implicitly.
"""
from dataclasses import dataclass
from pathlib import Path


FRAME_SIZE = (1280, 720)
RESOURCE_DIR = Path(__file__).resolve().parent / "res"


@dataclass(frozen=True)
class TemplateAsset:
    name: str
    roi: tuple[int, int, int, int]
    filename: str
    threshold: float = 0.92
    max_mean_error: float = 18.0
    search_margin: int = 3

    @property
    def path(self) -> Path:
        return RESOURCE_DIR / self.filename


@dataclass(frozen=True)
class ButtonAsset:
    """Observed button geometry only; visibility must be checked before use."""

    name: str
    roi: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x, y, width, height = self.roi
        return x + width // 2, y + height // 2


LOBBY_TITLE = TemplateAsset("lobby_title", (140, 9, 133, 40), "lobby_title.png")
LOBBY_RULES = TemplateAsset("lobby_rules", (88, 328, 260, 91), "lobby_rules.png")
LOBBY_NAVIGATION = TemplateAsset("lobby_navigation", (739, 609, 383, 87), "lobby_navigation.png")
CLOSED_LABEL = TemplateAsset("closed_label", (136, 105, 157, 64), "closed_label.png")
FREE_LABEL = TemplateAsset("free_label", (1145, 687, 91, 25), "free_label.png")
HELP_ICON = TemplateAsset("help_icon", (282, 11, 28, 36), "help_icon.png")
ADD_TEAMMATE_ICON = TemplateAsset("add_teammate_icon", (988, 330, 50, 56), "add_teammate_icon.png")

# Only these two non-battle controls have been seen. No generic "battle" click.
HELP_BUTTON = ButtonAsset("draft_duel_help", (286, 15, 18, 26))
ADD_TEAMMATE_BUTTON = ButtonAsset("draft_duel_add_teammate", (998, 341, 30, 30))

LOBBY_ANCHORS = (LOBBY_TITLE, LOBBY_RULES, LOBBY_NAVIGATION)
ALL_TEMPLATES = LOBBY_ANCHORS + (CLOSED_LABEL, FREE_LABEL, HELP_ICON, ADD_TEAMMATE_ICON)


class DraftDuelAssets:
    """Convenient namespace for the task adapter, without Device imports."""

    HELP_BUTTON = HELP_BUTTON
    ADD_TEAMMATE_BUTTON = ADD_TEAMMATE_BUTTON
