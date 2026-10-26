# This Python file uses the following encoding: utf-8
# @author runhey
# 脚本进程
# github https://github.com/runhey
import sys, os
import signal
import multiprocessing
from asyncio import CancelledError, sleep, to_thread, wait_for
from contextlib import asynccontextmanager
from enum import Enum
from queue import Empty
from threading import Lock

from module.logger import logger
from module.server.config_manager import ConfigManager
from module.server.script_websocket import ScriptWSManager


class ScriptState(int, Enum):
    INACTIVE = 0
    RUNNING = 1
    WARNING = 2
    UPDATING = 3


class ScriptProcess(ScriptWSManager):

    def __init__(self, config_name: str) -> None:
        super().__init__()
        if config_name not in ConfigManager.all_script_files():
            raise FileNotFoundError(f'{config_name}.json not found')
        self.config_name = config_name  # config_name
        self.log_pipe_out, self.log_pipe_in = multiprocessing.Pipe(False)
        self.state_queue = multiprocessing.Queue()
        self.state: ScriptState = ScriptState.INACTIVE
        self._process = None
        # HTTP/WS and the manager's push thread use different asyncio loops.
        self._lifecycle_lock = Lock()
        self._retired = False

    @asynccontextmanager
    async def lifecycle_guard(self):
        while not self._lifecycle_lock.acquire(blocking=False):
            await sleep(0.01)
        try:
            yield
        finally:
            self._lifecycle_lock.release()

    async def _publish_state(self):
        # A stalled client must not indefinitely prevent stopping an account.
        try:
            await wait_for(self.broadcast_state({"state": self.state}), timeout=3)
        except Exception as error:
            logger.warning(f'[{self.config_name}] state delivery failed: {type(error).__name__}')

    def _close_channels(self):
        for pipe in (self.log_pipe_out, self.log_pipe_in):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        self.state_queue.cancel_join_thread()
        self.state_queue.close()

    async def start(self):
        async with self.lifecycle_guard():
            if self._retired:
                raise RuntimeError('This configuration was renamed or deleted; reconnect before starting')
            if self._process is not None and self._process.is_alive():
                logger.info(f'Script {self.config_name} is already running; keep the current process')
                await self._publish_state()
                return
            try:
                if self._process is not None:
                    if self._process.pid is not None:
                        self._process.join(timeout=0)
                    self._process.close()
                    self._process = None
                # A new generation never consumes queued status/logs from the last.
                self._close_channels()
                self.log_pipe_out, self.log_pipe_in = multiprocessing.Pipe(False)
                self.state_queue = multiprocessing.Queue()
                process = multiprocessing.Process(target=func,
                                                  args=(self.config_name, self.state_queue, self.log_pipe_in),
                                                  name=self.config_name, daemon=True)
                self._process = process
                process.start()
                if not process.is_alive():
                    raise RuntimeError(f'Script {self.config_name} exited during startup')
            except Exception:
                self.state = ScriptState.WARNING
                await self._publish_state()
                raise
            self.state = ScriptState.RUNNING
            await self._publish_state()

    async def stop(self):
        async with self.lifecycle_guard():
            await self._stop_locked()

    async def _stop_locked(self):
        process = self._process
        try:
            if process is not None:
                if process.is_alive():
                    process.terminate()
                    await to_thread(process.join, timeout=0.7)
                    if process.is_alive():
                        logger.warning(f'Script {self.config_name} did not terminate; killing this process')
                        process.kill()
                        await to_thread(process.join, timeout=0.7)
                    if process.is_alive():
                        raise RuntimeError(f'Script {self.config_name} is still alive; stop failed')
                else:
                    if process.pid is not None:
                        process.join(timeout=0)
                process.close()
                self._process = None
        except Exception:
            # Keep the process reference, so a failed stop cannot spawn a twin.
            self.state = ScriptState.WARNING
            await self._publish_state()
            raise
        self.state = ScriptState.INACTIVE
        await self._publish_state()

    async def retire(self, change):
        """Stop and mutate the config atomically with respect to all starts."""
        async with self.lifecycle_guard():
            if self._retired:
                return False
            await self._stop_locked()
            if not change():
                return False
            self._retired = True
            self._close_channels()
            return True

    async def refresh_state(self):
        async with self.lifecycle_guard():
            await self._refresh_state_locked()

    async def _refresh_state_locked(self):
        if self._retired or self.state != ScriptState.RUNNING:
            return
        if self._process is None or not self._process.is_alive():
            logger.warning(f'Script {self.config_name} exited unexpectedly')
            self.state = ScriptState.WARNING
            await self._publish_state()

    async def _poll_state(self):
        async with self.lifecycle_guard():
            if self._retired or self.state == ScriptState.INACTIVE:
                return
            await self._refresh_state_locked()
            if self._process is None or not self._process.is_alive():
                return
            try:
                data = self.state_queue.get_nowait()
            except Empty:
                return
            if not isinstance(data, dict) or not data:
                return
            if 'state' in data:
                if data['state'] == ScriptState.WARNING:
                    self.state = ScriptState.WARNING
                data = dict(data, state=self.state)
            await wait_for(self.broadcast_state(data), timeout=3)

    async def coroutine_broadcast_state(self):
        try:
            while not self._retired:
                await sleep(1 if self.state == ScriptState.INACTIVE else 0.1)
                try:
                    await self._poll_state()
                except Exception as e:
                    logger.error(f'Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} state coroutine is cancelled')
            return

    async def coroutine_broadcast_log(self):
        try:
            while not self._retired:
                if self.state == ScriptState.INACTIVE:
                    await sleep(1)
                    continue
                await sleep(0.05)
                try:
                    async with self.lifecycle_guard():
                        if self._retired:
                            return
                        pipe = self.log_pipe_out
                        log = pipe.recv() if pipe.poll() else None
                    if log is None:
                        await sleep(0.3)
                        continue
                    if not log:
                        await sleep(0.5)
                        continue
                    if pipe is self.log_pipe_out and not self._retired:
                        await wait_for(self.broadcast_log(log), timeout=3)
                except EOFError as e:
                    await sleep(0.5)
                    logger.warning(f'EOFError: {e}')
                    continue
                except Exception as e:
                    logger.error(f'Log Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} log coroutine is cancelled')
            return


def func(config: str, state_queue: multiprocessing.Queue, log_pipe_in) -> None:
    # Set only in the fresh account child; never change the GUI's environment.
    os.environ["OAS_ACCOUNT_NAME"] = config
    def signal_handler(signum, frame):
        logger.info(f'Script {config} received signal {signum}, exiting gracefully')
        log_pipe_in.close()
        state_queue.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    def start_log() -> None:
        try:
            from module.logger import set_file_logger, set_func_logger
            set_file_logger(name=config)
            set_func_logger(log_pipe_in.send)
        except Exception as e:
            logger.exception(f'Start log error')
            logger.error(f'Error: {e}')
            raise
    start_log()
    import time
    try:
        # while 1:
        #     time.sleep(1)
        #     logger.info(f'Script {config} is running')
        #     state_queue.put({"state": ScriptState.RUNNING})
        from script import Script
        script = Script(config_name=config)
        script.state_queue = state_queue
        script.loop()
    except SystemExit as e:
        logger.info(f'Script {config} process exit')
        logger.error(f'Error: {e}')
        state_queue.put({"state": ScriptState.WARNING})
        time.sleep(0.1)
        exit(-1)
    except Exception as e:
        logger.exception(f'Run script {config} error')
        logger.error(f'Error: {e}')
        raise


if __name__ == '__main__':
    p = ScriptProcess('oas1')
    p.start()
    from time import sleep
    sleep(10)
    logger.info(p._process.exitcode)


