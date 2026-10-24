"""Draft Duel scheduled entry: one full series, including autonomous recovery.

Do not treat the offline coordination/strategy tests as a completed live run.
The leader's configured count means fully settled series, not battles.
"""
from datetime import datetime
from pathlib import Path
import json
import time

from module.exception import TaskEnd
from module.logger import logger
from tasks.DraftDuel.recognition import observe_lobby
from tasks.DraftDuel.rules import GAME_TIMEZONE, next_closed_retry
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_draft_duel, page_main
from tasks.DraftDuel.config import TeamRole
from tasks.DraftDuel.preflight import load_pair_config, PreflightError
from tasks.DraftDuel.local_draft import TimedOCR
from tasks.DraftDuel.team import TeamVision, TaskTeamIO
from tasks.DraftDuel.workflow import DraftWorkflow, save_frame
from tasks.DraftDuel.session import SessionError


class ScriptTask(GameUi):
    def run(self):
        settings = getattr(getattr(getattr(self, 'config', None), 'draft_duel', None),
                           'draft_duel_config', None)
        if settings is not None and settings.user_status != TeamRole.UNASSIGNED:
            return self.run_workflow()
        self.goto_page(page_draft_duel)
        self.screenshot()
        observed = observe_lobby(self.device.image)
        if observed.is_lobby and observed.is_closed:
            retry = next_closed_retry(datetime.now(GAME_TIMEZONE))
            logger.info(f"DraftDuel is closed; next check at {retry.isoformat()}")
            # The existing scheduler stores naive local game timestamps.
            self.set_next_run(task="DraftDuel", target=retry.replace(tzinfo=None))
            self.goto_page(page_main)
            raise TaskEnd("DraftDuel")

        # Unconfigured tasks retain the original read-only lobby preflight.
        logger.warning(
            "DraftDuel live draft/battle adapter is pending in-game validation; "
            "no registration, selection or payment was attempted"
        )
        self.device.save_screenshot(genre="draft_duel_unverified")
        self.set_next_run(task="DraftDuel", success=False, finish=True)
        # Script.run maps TaskEnd to success. Normal return leaves its result
        # falsey, so the history cannot claim that this unverified task passed.
        return None

    def run_workflow(self):
        try:
            pair = load_pair_config(Path(__file__).resolve().parents[2] / 'config',
                                    self.config.config_name)
        except PreflightError as error:
            logger.warning(f'DraftDuel team settings: {error}')
            self.set_next_run(task='DraftDuel', success=False, finish=True)
            return None
        account = pair.for_account(self.config.config_name)
        settings = account.settings
        ocr = TimedOCR()
        evidence = Path(__file__).resolve().parents[2] / 'log/draft-duel-task-runtime' / (self.config.config_name+'-'+str(time.time_ns()))
        evidence.mkdir(parents=True, exist_ok=True)
        try:
            def event(record, image=None):
                logger.info(f'DraftDuel workflow: {record}')
                record = dict(record, time=time.time())
                if image is not None:
                    filename = str(time.time_ns())+'.png'
                    save_frame(evidence/filename, image)
                    record['screenshot'] = filename
                with (evidence/'events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False)+'\n')
            io = TaskTeamIO(self)
            workflow = DraftWorkflow(pair, self.config.config_name, io, TeamVision(ocr),
                                     Path(__file__).resolve().parents[2], event=event)
            if settings.user_status == TeamRole.LEADER and not workflow.has_pending_series():
                from tasks.DraftDuel.round_vision import RoundVision
                image, captured = io.capture()
                view = RoundVision(ocr).read(image, captured)
                if view.phase in ('lobby_ready', 'roster_detail', 'matching', 'deploying',
                                  'battle', 'result', 'series_result'):
                    workflow._save_draft_checkpoint()
                    event({'phase': 'existing_series_adopted', 'screen': view.phase}, image)
                else:
                    self.goto_page(page_draft_duel)
            # Members remain passive; a resumed series never navigates out.
            ready = workflow.run()
        except SessionError as error:
            logger.warning(f'DraftDuel stopped: {error}')
            ready = False
        finally:
            ocr.close()
        if ready:
            logger.info('DraftDuel task completed: configured full-series count settled for both accounts')
            self.set_next_run(task='DraftDuel', success=True, finish=True)
            raise TaskEnd('DraftDuel')
        self.device.save_screenshot(genre='draft_duel_workflow_failed')
        self.set_next_run(task='DraftDuel', success=False, finish=True)
        return None
