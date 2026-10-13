"""Lifecycle regressions with fake processes, queues and configuration storage.

Run: toolkit/python.exe -B tests/test_script_lifecycle.py
AST loading deliberately excludes the live logger/server/device imports and the
MainManager singleton. No real process is started, stopped or enumerated.
"""
import ast
import asyncio
from collections import deque
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_isolated(relative, namespace):
    source = ROOT / relative
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [name for name in node.names if name.name != "multiprocessing"]
            if names:
                nodes.append(ast.Import(names=names))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module.startswith(("module", "tasks", "fastapi", "starlette")):
                nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.decorator_list = []
            nodes.append(node)
    compiled = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(compiled, str(source), "exec"), namespace)
    return SimpleNamespace(**namespace)


class FakeQueue:
    def __init__(self):
        self.items = deque()
        self.closed = False

    def put(self, data):
        self.items.append(data)

    def get_nowait(self):
        if not self.items:
            raise queue.Empty
        return self.items.popleft()

    def empty(self):
        return not self.items

    def close(self):
        self.closed = True

    def join_thread(self):
        pass

    def cancel_join_thread(self):
        pass


class FakePipe:
    def close(self):
        pass

    def poll(self):
        return False


class FakeProcess:
    def __init__(self, *, target, args, name, daemon):
        self.target, self.args, self.name = target, args, name
        self.alive = False
        self.pid = None
        self.exitcode = None
        self.start_error = None
        self.exits_during_start = False
        self.terminate_error = None
        self.kill_error = None
        self.terminate_exits = True
        self.kill_exits = True
        self.calls = []

    def start(self):
        self.calls.append("start")
        if self.start_error:
            raise self.start_error
        self.alive = True
        self.pid = 70001
        if self.exits_during_start:
            self.alive = False
            self.exitcode = 1

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.calls.append("terminate")
        if self.terminate_error:
            raise self.terminate_error
        if self.terminate_exits:
            self.alive = False
            self.exitcode = -15

    def kill(self):
        self.calls.append("kill")
        if self.kill_error:
            raise self.kill_error
        if self.kill_exits:
            self.alive = False
            self.exitcode = -9

    def join(self, timeout=None):
        self.calls.append("join")

    def close(self):
        self.calls.append("close")


class FakeWSManager:
    def __init__(self):
        self.active_connections = []
        self.messages = []
        self.before_broadcast = None

    async def broadcast_state(self, data):
        if self.before_broadcast:
            await self.before_broadcast(data)
        self.messages.append(dict(data))
        await asyncio.sleep(0)

    async def broadcast_log(self, data):
        await asyncio.sleep(0)


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class ScriptLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processes = []
        self.on_create = None

        def process_factory(**kwargs):
            process = FakeProcess(**kwargs)
            self.processes.append(process)
            if self.on_create:
                self.on_create(process)
            return process

        self.mp = SimpleNamespace(
            Process=process_factory, Queue=FakeQueue,
            Pipe=lambda duplex: (FakePipe(), FakePipe()))
        self.namespace = {
            "multiprocessing": self.mp,
            "logger": Mock(), "ScriptWSManager": FakeWSManager,
            "ConfigManager": SimpleNamespace(all_script_files=lambda: ["oas7", "oas10"]),
        }
        self.module = load_isolated("module/server/script_process.py", self.namespace)
        self.State = self.module.ScriptState
        self.script = self.module.ScriptProcess("oas7")

    async def run_monitor(self, steps=30):
        async def fast_sleep(seconds):
            await asyncio.sleep(0)

        original = self.namespace["sleep"]
        self.namespace["sleep"] = fast_sleep
        monitor = asyncio.create_task(self.script.coroutine_broadcast_state())
        try:
            for _ in range(steps):
                await asyncio.sleep(0)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            self.namespace["sleep"] = original

    async def test_concurrent_start_is_idempotent(self):
        await asyncio.gather(self.script.start(), self.script.start())
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.processes[0].alive)
        self.assertNotIn("terminate", self.processes[0].calls)
        self.assertEqual(self.script.state, self.State.RUNNING)

    async def test_running_only_announced_after_process_start(self):
        observed = []

        async def observe(data):
            if data.get("state") == self.State.RUNNING:
                observed.append(bool(self.processes and self.processes[-1].alive))

        self.script.before_broadcast = observe
        await self.script.start()
        self.assertEqual(observed, [True])

    async def test_start_then_stop_cannot_leave_an_untracked_live_process(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def pause_running(data):
            if data.get("state") == self.State.RUNNING:
                started.set()
                await release.wait()

        self.script.before_broadcast = pause_running
        starting = asyncio.create_task(self.script.start())
        await asyncio.wait_for(started.wait(), 1)
        stopping = asyncio.create_task(self.script.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(starting, stopping), 2)
        self.assertTrue(all(not process.alive for process in self.processes))
        self.assertEqual(self.script.state, self.State.INACTIVE)

    async def test_start_failure_never_announces_running(self):
        self.on_create = lambda process: setattr(process, "start_error", OSError("fake spawn failure"))
        with self.assertRaises(Exception):
            await self.script.start()
        self.assertEqual(self.script.state, self.State.WARNING)
        self.assertNotIn(self.State.RUNNING, [message.get("state") for message in self.script.messages])
        self.assertTrue(all(not process.alive for process in self.processes))

    async def test_exit_during_spawn_is_reported_as_warning(self):
        self.on_create = lambda process: setattr(process, "exits_during_start", True)
        with self.assertRaises(Exception):
            await self.script.start()
        self.assertEqual(self.script.state, self.State.WARNING)
        self.assertNotIn(self.State.RUNNING, [message.get("state") for message in self.script.messages])

    async def test_failed_spawn_can_be_retried_without_reusing_channels(self):
        self.on_create = lambda process: setattr(process, "start_error", OSError("fake spawn failure"))
        with self.assertRaises(Exception):
            await self.script.start()
        failed_process, failed_queue = self.script._process, self.script.state_queue
        self.on_create = None
        await self.script.start()
        self.assertEqual(len(self.processes), 2)
        self.assertIsNot(self.script._process, failed_process)
        self.assertIsNot(self.script.state_queue, failed_queue)
        self.assertEqual(self.script.state, self.State.RUNNING)
        self.assertFalse(failed_process.alive)
        self.assertTrue(self.script._process.alive)

    async def test_channel_allocation_failure_reports_warning_without_spawning(self):
        for factory_name in ("Pipe", "Queue"):
            with self.subTest(factory=factory_name):
                script = self.module.ScriptProcess("oas7")
                with patch.object(self.mp, factory_name, side_effect=OSError("fake channel allocation failure")):
                    with self.assertRaises(OSError):
                        await script.start()
                self.assertEqual(script.state, self.State.WARNING)
                self.assertIsNone(script._process)
                self.assertIn(self.State.WARNING, [message.get("state") for message in script.messages])
                self.assertNotIn(self.State.RUNNING, [message.get("state") for message in script.messages])
        self.assertEqual(self.processes, [])

    async def test_stop_confirms_exit_before_announcing_inactive(self):
        await self.script.start()
        process = self.processes[0]
        process.terminate_exits = False
        observed = []

        async def observe(data):
            if data.get("state") == self.State.INACTIVE:
                observed.append(process.alive)

        self.script.before_broadcast = observe
        await self.script.stop()
        self.assertEqual(observed, [False])
        self.assertEqual(process.calls[:5], ["start", "terminate", "join", "kill", "join"])
        self.assertIsNone(self.script._process)

    async def test_unstoppable_process_remains_tracked_and_warns(self):
        await self.script.start()
        process = self.processes[0]
        process.terminate_exits = process.kill_exits = False
        with self.assertRaises(Exception):
            await self.script.stop()
        self.assertIs(self.script._process, process)
        self.assertTrue(process.alive)
        self.assertEqual(self.script.state, self.State.WARNING)
        try:
            await self.script.start()
        except Exception:
            pass
        self.assertEqual(len(self.processes), 1, "failed stop must never spawn a replacement")
        self.assertEqual(self.script.state, self.State.WARNING)

    async def test_termination_exception_cannot_forget_live_process(self):
        await self.script.start()
        process = self.processes[0]
        process.terminate_error = PermissionError("fake access denied")
        process.kill_error = PermissionError("fake kill denied")
        with self.assertRaises(Exception):
            await self.script.stop()
        self.assertIs(self.script._process, process)
        self.assertTrue(process.alive)
        self.assertEqual(self.script.state, self.State.WARNING)

    async def test_unexpected_process_exit_updates_clients_to_warning(self):
        await self.script.start()
        self.processes[0].alive = False
        self.processes[0].exitcode = 1
        await self.run_monitor()
        self.assertEqual(self.script.state, self.State.WARNING)
        self.assertIn(self.State.WARNING, [message.get("state") for message in self.script.messages])

    async def test_stop_after_natural_exit_is_inactive(self):
        await self.script.start()
        process = self.processes[0]
        process.alive = False
        process.exitcode = 0
        await self.script.stop()
        self.assertEqual(self.script.state, self.State.INACTIVE)
        self.assertIsNone(self.script._process)
        self.assertNotIn("terminate", process.calls)

    async def test_restart_uses_fresh_queue_and_ignores_old_warning(self):
        await self.script.start()
        old_queue = self.script.state_queue
        old_queue.put({"state": self.State.WARNING, "generation": "old"})
        await self.script.stop()
        await self.script.start()
        self.assertIsNot(self.script.state_queue, old_queue)
        self.script.messages.clear()
        await self.run_monitor()
        self.assertEqual(self.script.state, self.State.RUNNING)
        self.assertFalse(any(message.get("generation") == "old" for message in self.script.messages))
        self.assertIs(self.processes[-1].args[1], self.script.state_queue)

    async def test_multiple_accounts_have_independent_lifecycle(self):
        other = self.module.ScriptProcess("oas10")
        await asyncio.gather(self.script.start(), other.start())
        await self.script.stop()
        self.assertTrue(other._process.is_alive())
        self.assertEqual(other.state, self.State.RUNNING)
        self.assertIsNot(self.script.state_queue, other.state_queue)

    async def test_stop_from_background_loop_serializes_with_http_loop_start(self):
        started = asyncio.Event()
        release = asyncio.Event()
        thread_entered = threading.Event()

        async def pause_running(data):
            if data.get("state") == self.State.RUNNING:
                started.set()
                await release.wait()

        async def background_stop():
            thread_entered.set()
            await self.script.stop()

        self.script.before_broadcast = pause_running
        starting = asyncio.create_task(self.script.start())
        await asyncio.wait_for(started.wait(), 1)
        stopping = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(background_stop())))
        try:
            self.assertTrue(await asyncio.to_thread(thread_entered.wait, 1))
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(starting, stopping), 2)
        self.assertEqual(self.script.state, self.State.INACTIVE)
        self.assertTrue(all(not process.alive for process in self.processes))

    async def test_cancelled_lock_waiter_does_not_lock_out_later_stop(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def pause_running(data):
            if data.get("state") == self.State.RUNNING:
                started.set()
                await release.wait()

        self.script.before_broadcast = pause_running
        starting = asyncio.create_task(self.script.start())
        await asyncio.wait_for(started.wait(), 1)
        waiter = asyncio.create_task(self.script.stop())
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        release.set()
        await asyncio.wait_for(starting, 1)
        await asyncio.wait_for(self.script.stop(), 1)
        self.assertEqual(self.script.state, self.State.INACTIVE)
        self.assertTrue(all(not process.alive for process in self.processes))

    async def test_dead_process_cannot_replay_queued_running_state(self):
        await self.script.start()
        self.script.state_queue.put({"state": self.State.RUNNING})
        self.script._process.alive = False
        self.script._process.exitcode = 1
        self.script.messages.clear()
        await self.run_monitor()
        self.assertEqual(self.script.state, self.State.WARNING)
        self.assertNotIn(self.State.RUNNING, [message.get("state") for message in self.script.messages])

    async def test_live_child_warning_is_preserved_by_duplicate_start(self):
        await self.script.start()
        self.script.state_queue.put({"state": self.State.WARNING})
        await self.run_monitor()
        self.assertEqual(self.script.state, self.State.WARNING)
        await self.script.start()
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.script.state, self.State.WARNING)


class ScriptRouterLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Execute actual HTTP handlers with an in-memory manager and no web server."""

    def setUp(self):
        ScriptLifecycleTests.setUp(self)
        self.mm = SimpleNamespace(script_process={"oas7": self.script}, _all_script_files=["oas7", "oas10"],
                                  rename=Mock(return_value=True), delete=Mock(return_value=True))
        namespace = {
            "mm": self.mm, "ScriptProcess": self.module.ScriptProcess,
            "ScriptState": self.State, "HTTPException": FakeHTTPException,
            "WebSocket": object, "WebSocketDisconnect": Exception,
            "logger": Mock(), "StreamingResponse": object, "TimeDelta": object,
        }
        self.router = load_isolated("module/server/script_router.py", namespace)

    async def test_http_start_waits_for_spawn(self):
        await self.router.script_start("oas7")
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.processes[0].alive)

    async def test_http_stop_waits_for_exit(self):
        await self.script.start()
        process = self.processes[0]
        await self.router.script_stop("oas7")
        self.assertFalse(process.alive)
        self.assertEqual(self.script.state, self.State.INACTIVE)

    async def assert_mutation_does_not_forget_process(self, operation):
        await self.script.start()
        process = self.processes[0]
        process.terminate_exits = process.kill_exits = False
        with self.assertRaises(Exception):
            if operation == "delete":
                await self.router.config_delete("oas7")
            else:
                await self.router.config_rename("oas7", "new-name")
        self.assertIs(self.mm.script_process.get("oas7"), self.script)
        self.assertIs(self.script._process, process)
        self.mm.rename.assert_not_called()
        self.mm.delete.assert_not_called()

    async def test_delete_stop_failure_preserves_mapping_and_config(self):
        await self.assert_mutation_does_not_forget_process("delete")

    async def test_rename_stop_failure_preserves_mapping_and_config(self):
        await self.assert_mutation_does_not_forget_process("rename")

    async def test_delete_file_failure_preserves_mapping(self):
        await self.script.start()
        original = self.script._process
        self.mm.delete.return_value = False
        with self.assertRaises(FakeHTTPException):
            await self.router.config_delete("oas7")
        self.assertIs(self.mm.script_process.get("oas7"), self.script)
        self.assertFalse(original.alive)
        await self.script.start()
        self.assertTrue(self.script._process.is_alive())

    async def test_rename_file_failure_preserves_mapping(self):
        await self.script.start()
        original = self.script._process
        self.mm.rename.return_value = False
        with self.assertRaises(FakeHTTPException):
            await self.router.config_rename("oas7", "new-name")
        self.assertIs(self.mm.script_process.get("oas7"), self.script)
        self.assertFalse(original.alive)
        await self.script.start()
        self.assertTrue(self.script._process.is_alive())

    async def assert_successful_mutation_blocks_old_reference(self, operation):
        await self.script.start()
        old_process = self.processes[0]
        if operation == "delete":
            self.mm.delete.side_effect = lambda name: not old_process.alive
            result = await self.router.config_delete("oas7")
        else:
            self.mm.rename.side_effect = lambda old_name, new_name: not old_process.alive
            result = await self.router.config_rename("oas7", "new-name")
        self.assertTrue(result)
        self.assertFalse(old_process.alive)
        self.assertNotIn("oas7", self.mm.script_process)
        try:
            await self.script.start()
        except Exception:
            pass
        self.assertEqual(len(self.processes), 1, "old websocket reference must not restart a removed account")

    async def test_delete_waits_for_stop_and_retires_old_websocket_reference(self):
        await self.assert_successful_mutation_blocks_old_reference("delete")

    async def test_rename_waits_for_stop_and_retires_old_websocket_reference(self):
        await self.assert_successful_mutation_blocks_old_reference("rename")


class MainManagerMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ScriptLifecycleTests.setUp(self)
        namespace = {
            "ConfigManager": type("FakeConfigManager", (), {}), "Config": object,
            "ScriptProcess": self.module.ScriptProcess, "ScriptState": self.State,
            "logger": Mock(),
        }
        self.manager_module = load_isolated("module/server/main_manager.py", namespace)
        self.manager_namespace = namespace
        self.manager = self.manager_module.MainManager.__new__(self.manager_module.MainManager)
        self.manager.script_process = {}
        self.manager_module.MainManager.signal_kill_server = False
        self.real_sleep = asyncio.sleep

    def test_cached_name_without_managed_object_is_registered(self):
        self.manager._all_script_files = ["oas7"]
        self.manager.all_script_files = Mock(return_value=["oas7", "oas10"])
        created = object()
        factory = Mock(return_value=created)
        with patch.dict(self.manager_namespace, ScriptProcess=factory):
            self.manager.add_script_file("oas7")
        factory.assert_called_once_with("oas7")
        self.assertIs(self.manager.script_process["oas7"], created)
        self.assertEqual(self.manager._all_script_files, ["oas7", "oas10"])

    def test_managed_object_survives_add_when_name_cache_is_stale(self):
        existing = object()
        self.manager.script_process = {"oas7": existing}
        self.manager._all_script_files = []
        self.manager.all_script_files = Mock(return_value=["oas7"])
        factory = Mock()
        with patch.dict(self.manager_namespace, ScriptProcess=factory):
            self.manager.add_script_file("oas7")
        factory.assert_not_called()
        self.assertIs(self.manager.script_process["oas7"], existing)

    def monitor(self, *, finish_state_once=False):
        real_sleep = self.real_sleep
        monitor = SimpleNamespace(state=self.State.RUNNING, _retired=False, starts={"state": 0, "log": 0},
                                  cancelled={"state": 0, "log": 0})

        async def watch(kind):
            monitor.starts[kind] += 1
            if kind == "state" and finish_state_once and monitor.starts[kind] == 1:
                return
            try:
                while True:
                    await real_sleep(0)
            except asyncio.CancelledError:
                monitor.cancelled[kind] += 1
                raise

        monitor.coroutine_broadcast_state = lambda: watch("state")
        monitor.coroutine_broadcast_log = lambda: watch("log")
        return monitor

    async def drive_push_loop(self, update=None):
        iterations = 0
        before = asyncio.all_tasks()

        async def scheduler_sleep(seconds):
            nonlocal iterations
            iterations += 1
            await self.real_sleep(0)
            if update:
                update(iterations)
            if iterations >= 8:
                raise asyncio.CancelledError()

        try:
            with patch.object(asyncio, "sleep", scheduler_sleep):
                try:
                    await self.manager.push_data_handle()
                except asyncio.CancelledError:
                    pass
            await self.real_sleep(0)
        finally:
            created = asyncio.all_tasks() - before
            for task in created:
                task.cancel()
            await asyncio.gather(*created, return_exceptions=True)

    async def test_same_name_replacement_gets_new_monitors(self):
        old, replacement = self.monitor(), self.monitor()
        self.manager.script_process["oas7"] = old

        def replace(iteration):
            if iteration == 3:
                self.manager.script_process["oas7"] = replacement

        await self.drive_push_loop(replace)
        self.assertEqual(old.starts, {"state": 1, "log": 1})
        self.assertEqual(old.cancelled, {"state": 1, "log": 1})
        self.assertEqual(replacement.starts, {"state": 1, "log": 1})

    async def test_finished_state_monitor_is_recreated(self):
        monitor = self.monitor(finish_state_once=True)
        self.manager.script_process["oas7"] = monitor
        await self.drive_push_loop()
        self.assertEqual(monitor.starts["state"], 2)
        self.assertEqual(monitor.starts["log"], 1)

    async def test_shutdown_failure_still_stops_other_accounts_and_keeps_monitoring(self):
        failed, stopped = self.monitor(), self.monitor()
        failed.config_name, stopped.config_name = "oas7", "oas10"
        calls = []

        async def fail_stop():
            calls.append("oas7")
            failed.state = self.State.WARNING
            raise RuntimeError("fake unkillable process")

        async def stop_other():
            calls.append("oas10")
            stopped.state = self.State.INACTIVE

        failed.stop, stopped.stop = fail_stop, stop_other
        self.manager.script_process = {"oas7": failed, "oas10": stopped}
        self.manager_module.MainManager.signal_kill_server = True
        await self.drive_push_loop()
        self.assertEqual(calls, ["oas7", "oas10"])
        self.assertFalse(self.manager_module.MainManager.signal_kill_server)
        self.assertIs(self.manager.script_process["oas7"], failed)
        self.assertEqual(failed.state, self.State.WARNING)
        self.assertEqual(failed.starts, {"state": 1, "log": 1})
        self.assertEqual(stopped.starts, {"state": 0, "log": 0})

    async def test_successful_shutdown_exits_only_after_every_account_stops(self):
        first, second = self.monitor(), self.monitor()
        first.config_name, second.config_name = "oas7", "oas10"
        calls = []

        async def stop_first():
            calls.append("oas7")
            first.state = self.State.INACTIVE

        async def stop_second():
            calls.append("oas10")
            second.state = self.State.INACTIVE

        first.stop, second.stop = stop_first, stop_second
        self.manager.script_process = {"oas7": first, "oas10": second}
        self.manager_module.MainManager.signal_kill_server = True
        with self.assertRaises(SystemExit) as shutdown:
            await self.drive_push_loop()
        self.assertEqual(shutdown.exception.code, 0)
        self.assertEqual(calls, ["oas7", "oas10"])
        self.assertEqual(first.state, self.State.INACTIVE)
        self.assertEqual(second.state, self.State.INACTIVE)


if __name__ == "__main__":
    unittest.main()
