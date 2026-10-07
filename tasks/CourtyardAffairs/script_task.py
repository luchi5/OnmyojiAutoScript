from module.exception import TaskEnd
from tasks.CourtyardAffairs.selected_skin import CompletionAwareCourtyardMixin
from tasks.CourtyardAffairs.themes import CourtyardSkin
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.Restart.assets import RestartAssets


class ScriptTask(CompletionAwareCourtyardMixin, GameUi, RestartAssets):
    """An independently scheduled daily task; login no longer runs it."""

    def run(self):
        options = self.config.courtyard_affairs.courtyard_affairs_config
        courtyard_skin = getattr(options, 'courtyard_skin', CourtyardSkin.AUTO)
        # Register task-local recovery before navigation, including when a
        # manually retried task starts on a leftover blue claim-result page.
        self.configure_courtyard_skin(courtyard_skin)
        self.goto_page(page_main)
        success = self.harvest_courtyard_affairs(
            timeout_seconds=options.timeout_seconds,
            max_complete_clicks=options.max_complete_clicks,
            courtyard_skin=courtyard_skin,
        )
        self.goto_page(page_main)
        self.set_next_run('CourtyardAffairs', success=success, finish=success, server=success)
        raise TaskEnd('CourtyardAffairs')
