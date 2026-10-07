# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from functools import wraps
import zerorpc
import zmq
import msgpack
import random
import re
import cv2
import time
import os
import inflection
import asyncio
import json

from datetime import date
import threading
from typing import Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue


from module.config.utils import convert_to_underscore
from module.config.config import Config, Function
from module.config.config_model import ConfigModel
from module.config.instance_guard import InstanceGuard
from module.config.anti_ban import AntiBanGuard
from module.device.device import Device
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.ocr.rpc import ensure_ocr_server_started



_log_switch_lock = threading.Lock()#线程锁


class Script:
    def __init__(self, config_name: str ='oas') -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        self._account_session_restart_pending = False
        # 运行loop的线程
        self.loop_thread: Thread = None
        # 跨进程排队管理器（仅在 queue_mode=True 时初始化）
        self.instance_guard: InstanceGuard = None
        self.anti_ban_guard: AntiBanGuard = AntiBanGuard()

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> "Device":
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            folder_name = str(int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            logger.info('保存详细错误的日志和截图到路径:')
            logger.info(f'{str( Path.cwd() / "log" / "error" / folder_name)}')
            os.mkdir(folder)
            metadata = {
                'version': 1,
                'config_name': self.config_name,
                'task': getattr(getattr(self.config, 'task', None), 'command', None),
                'timestamp_ms': int(folder_name),
            }
            metadata_path = Path(folder) / 'metadata.json'
            pending_metadata = metadata_path.with_suffix('.json.tmp')
            pending_metadata.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
            os.replace(pending_metadata, metadata_path)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        # pandtic验证
        if isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass


        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            logger.info(f'Set arg {task}.{group}.{argument}.{argument_object}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def _release_token_before_wait(func):
        @wraps(func)
        def wrapper(self, future):
            if self.instance_guard and self.instance_guard.should_release(
                pending_task=self.config.pending_task,
                waiting_task=self.config.waiting_task,
                idle_threshold_minutes=self.config.script.optimization.queue_idle_threshold
            ):
                self.instance_guard.release()
            return func(self, future)
        return wrapper

    @_release_token_before_wait
    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if getattr(self, '_account_session_restart_pending', False):
                return False
            closeout = getattr(getattr(self.config, 'talisman_pass', None), 'closeout_config', None)
            if closeout is not None and getattr(closeout, 'enable', False):
                try:
                    if self._evaluate_daily_closeout().queued:
                        return False
                except Exception as error:
                    logger.warning(f'Daily closeout idle check failed: {type(error).__name__}')
            if datetime.now() > future:
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False

    def _evaluate_daily_closeout(self):
        """Queue enabled closeout only between game tasks, after due dailies."""
        from tasks.Component.daily_closeout import (
            evaluate_daily_closeout, arm_daily_closeout,
            is_automatic_closeout, is_manual_talisman,
        )

        now = datetime.now()
        # Only work already due is a dependency. Repeating foster/wanted jobs
        # scheduled for later and long farming/event jobs never hold closeout.
        daily_tasks = (
            'CourtyardAffairs', 'KekkaiUtilize', 'KekkaiActivation',
            'DemonEncounter', 'AreaBoss', 'GoldYoukai', 'ExperienceYoukai',
            'Nian', 'Tako', 'AutoCheckinBigGod', 'RealmRaid', 'RyouToppa',
            'DailyTrifles', 'WantedQuests', 'Pets', 'Delegation',
        )
        due = []
        for name in daily_tasks:
            task_config = getattr(self.config, convert_to_underscore(name), None)
            scheduler = getattr(task_config, 'scheduler', None)
            if scheduler is not None and scheduler.enable and scheduler.next_run <= now:
                due.append(name)
        decision = evaluate_daily_closeout(self.config, now=now, due_tasks=due)
        # Retry an optional feedback notification only at this idle boundary.
        # A push failure never requeues gameplay or interrupts another task.
        from tasks.Component.daily_feedback import maybe_notify_feedback
        maybe_notify_feedback(self.config, now=now)
        scheduler = self.config.talisman_pass.scheduler
        if (scheduler.next_run <= now
                and not is_automatic_closeout(self.config, now=now)
                and not is_manual_talisman(self.config, now=now)):
            # Automatic checkpoints must not run the old fixed-time job early.
            # Explicit flash requests are tracked separately and stay usable.
            armed = arm_daily_closeout(self.config, now=now)
            if not armed and decision.reason == 'state_unavailable' and scheduler.next_run <= now:
                # A damaged/unwritable proof file must not cause a hot loop.
                scheduler.next_run = (now + timedelta(minutes=3)).replace(microsecond=0)
                self.config.save()
        return decision

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        while True:
            if getattr(self, '_account_session_restart_pending', False):
                return self._get_next_task_with_recovery()
            closeout = getattr(getattr(self.config, 'talisman_pass', None), 'closeout_config', None)
            if closeout is not None and getattr(closeout, 'enable', False):
                try:
                    self._evaluate_daily_closeout()
                except Exception as error:
                    # A closeout bookkeeping failure must not stop game tasks.
                    logger.warning(f'Daily closeout check failed: {type(error).__name__}')
            task = self.config.get_next()
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            now = datetime.now()
            antiban_wake = self.anti_ban_guard.wake_time(now, self.config.script.anti_ban)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            if not self._try_acquire_queue_token():
                del_cached_property(self, "config")
                continue
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                return task.command
            # 根据策略执行等待逻辑
            if not self._handle_wait_during_idle(task.next_run):
                # 若等待被打断, 则刷新配置
                del_cached_property(self, "config")

    def _try_acquire_queue_token(self) -> bool:
        """
        尝试获取排队执行权。
        如果排队模式未启用，返回 True。
        如果排队模式启用但未能获取到执行权，进入等待循环直到获取成功或配置变更。

        Returns:
            True: 获取得执行权，可以执行任务
            False: 等待被配置变更打断，调用方应重新加载配置后重试
        """
        # 是否开启排队模式
        if not self.config.script.optimization.queue_mode:
            if self.instance_guard:
                self.instance_guard.remove_from_queue()
                self.instance_guard = None
            return True

        # 懒加载instance_guard
        if self.instance_guard is None:
            try:
                self.instance_guard = InstanceGuard(self.config_name)
                logger.info(f"[Queue] Queue mode enabled for '{self.config_name}'")
            except Exception:
                self.instance_guard = None
                return True

        # 尝试获取执行权
        if self.instance_guard.try_acquire():
            return True

        # 执行权获取失败，关闭模拟器并进入等待循环
        logger.info(f"[Queue] '{self.config_name}' waiting for execution token...")
        if (self.config.script.optimization.when_task_queue_empty == 'close_game'
                and not self._emulator_down
                and 'device' in self.__dict__):
            try:
                self.device.emulator_stop()
                self._emulator_down = True
                logger.info(f"[Queue] Emulator closed during queue wait")
            except Exception:
                pass
        self.config.start_watching()
        while True:
            time.sleep(30)

            if self.config.should_reload():
                logger.info(f"[Queue] Config changed, re-evaluating")
                return False

            if self.instance_guard.try_acquire():
                return True

    def _handle_wait_during_idle(self, next_run: datetime) -> bool:
        """
        处理任务空闲期间的行为策略
        :param next_run: 下一个任务的时间
        :return: True 表示等待成功完成, False 表示等待被中断
        """
        method = self.config.script.optimization.when_task_queue_empty
        strategy_map = {
            "close_game": self._wait_close_game,
            "goto_main": self._wait_goto_main,
        }
        func = strategy_map.get(method)
        if func is None:
            logger.warning(f"Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to stay_there")
            func = self._wait_stay_there
        return func(next_run)

    @staticmethod
    def _time_to_timedelta(value) -> timedelta:
        if value is None:
            return timedelta(0)
        return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)

    def _wait_until_with_emulator_preheat(self, next_run: datetime) -> bool:
        """Wait until next_run; if emulator is down, preheat startup before next task."""
        if not self._emulator_down:
            return self.wait_until(next_run)

        startup_lead = self._time_to_timedelta(self.config.script.optimization.emulator_startup_lead_time)
        now = datetime.now()
        wake_time = next_run - startup_lead if startup_lead > timedelta(0) else next_run
        if wake_time < now:
            wake_time = now

        now = datetime.now()
        if wake_time > now:
            logger.info(f"Wait before wake emulator: {wake_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if not self.wait_until(wake_time):
                return False

        if self._emulator_down:
            logger.info("Wake emulator before next task")
            if not self._try_acquire_queue_token():
                return False
            self.device = Device(self.config)
            self._emulator_down = False

        if wake_time < next_run:
            return self.wait_until(next_run)
        return True

    def _wait_close_game(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Emulator is down, skip close_game/goto_main action and wait with preheat")
            return self._wait_until_with_emulator_preheat(next_run)

        close_game_wait_duration = self.config.script.optimization.close_game_wait_duration
        close_game_wait = self._time_to_timedelta(close_game_wait_duration)
        close_emulator_wait_duration = self.config.script.optimization.close_emulator_wait_duration
        close_emulator_wait = self._time_to_timedelta(close_emulator_wait_duration)

        if close_emulator_wait > timedelta(0) and next_run > datetime.now() + close_emulator_wait:
            logger.info("Close emulator during wait")
            self.device.emulator_stop()
            self._emulator_down = True

            if not self._wait_until_with_emulator_preheat(next_run):
                return False

            self.run("Restart")
            return True

        if close_game_wait <= timedelta(0):
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        if next_run > datetime.now() + close_game_wait:
            logger.info("Close game during wait")
            self.device.app_stop()
            self.device.release_during_wait()
            if not self.wait_until(next_run):
                return False
            self.run("Restart")
            return True

        logger.info("Wait without closing game (close_game wait duration not reached)")
        self.device.release_during_wait()
        if not self.wait_until(next_run):
            return False
        return True

    def _wait_goto_main(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("前往庭院：立即恢复已关闭的模拟器")
            self.device = Device(self.config)
            self._emulator_down = False

        # Explicitly keeping the courtyard open takes priority over both
        # close timers. Those timers belong to the close_game strategy only.
        logger.info("空闲策略：前往庭院，保持游戏和模拟器运行，忽略关闭计时")
        if not self.device.app_is_running():
            if not self.run("Restart"):
                return False
        if not self.run("GotoMain"):
            if getattr(self, '_account_session_restart_pending', False):
                self._record_task_result('GotoMain', False)
            return False
        if getattr(self, 'failure_record', {}).get('GotoMain', 0):
            self._record_task_result('GotoMain', True)
        self.device.release_during_wait()
        return self.wait_until(next_run)

    def _wait_stay_there(self, next_run: datetime) -> bool:
        if self._emulator_down:
            logger.info("Stay_there during wait (emulator is down, with preheat)")
            return self._wait_until_with_emulator_preheat(next_run)

        logger.info("Stay_there (no action) during wait")
        self.device.release_during_wait()
        return self.wait_until(next_run)

    def exception_handler(self, e: Exception, command: str) -> None:
        # 处理御魂溢出
        from tasks.Utils.post_diagnotor import PostDiagnotor, AnalyzeType
        image = getattr(self.device, 'image', None)
        # image为None则不做处理
        if image is None:
            return
        analyse_type = PostDiagnotor().handle(e=e, command=command, image=image)
        if analyse_type == AnalyzeType.SoulOverflow:
            self.config.task_call('SoulsTidy')
            time.sleep(1)

    def _recover_account_session(self, command: str, error: AccountLoggedInElsewhere) -> bool:
        logger.critical(str(error))
        logger.warning('账号在其他设备登录，将按故障流程自动重启游戏并恢复任务')
        self.config.model.running_task = ''
        self._account_session_restart_pending = True
        try:
            self.save_error_log()
        except Exception as save_error:
            logger.warning(f'无法保存顶号证据：{type(save_error).__name__}')
        try:
            accepted = self.config.notifier.push(
                title=f'{self.config_name} 自动恢复：账号在其他设备登录',
                content='检测到其他设备登录，将自动重启游戏并恢复任务；同一任务连续失败三次仍按默认规则停止。'
            )
            if not accepted:
                logger.warning('顶号恢复通知请求失败，请检查推送服务状态；继续自动恢复')
        except Exception as notify_error:
            logger.warning(f'顶号恢复通知失败：{type(notify_error).__name__}')
        self.device.sleep(10)
        return False

    def _get_next_task_with_recovery(self) -> str:
        if not getattr(self, '_account_session_restart_pending', False):
            return self.get_next_task()
        self._account_session_restart_pending = False
        # One recovery run must work even when scheduled Restart is disabled.
        # Only this in-memory Function is enabled; preserve the user setting.
        recovery_task = Function('restart', self.config.model.restart.dict())
        recovery_task.enable = True
        recovery_task.next_run = datetime.now().replace(microsecond=0)
        self.config.task = recovery_task
        self.is_first_task = False
        logger.info('Run one-shot Restart after other-device login')
        return 'Restart'

    def _record_task_result(self, task: str, success: bool) -> None:
        """Use the same failure limit for scheduled work and idle takeovers."""
        failed = self.failure_record.get(task, 0)
        failed = 0 if success else failed + 1
        self.failure_record[task] = failed
        if failed >= 3:
            logger.critical(f"Task `{task}` failed 3 or more times.")
            logger.critical("Possible reason #1: You haven't used it correctly. "
                            "Please read the help text of the options.")
            logger.critical("Possible reason #2: There is a problem with this task. "
                            "Please contact developers or try to fix it yourself.")
            logger.critical('Request human takeover')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(task)}{task}',
                content=f"<{self.config_name}> 任务连续失败三次，请上线查看"
            )
            if self.config.script.error.error_repeated:
                self.device.emulator_stop()
            exit(1)

    def _check_account_session_before_recovery(self, command: str) -> bool:
        try:
            self.device.check_account_session()
            self.device.refresh_account_session()
        except AccountLoggedInElsewhere as error:
            self._recover_account_session(command, error)
            return True
        except Exception as capture_error:
            # A failed diagnostic screenshot must not replace the original
            # network/stuck error or disable its established recovery path.
            logger.warning(f'恢复前无法复核顶号画面：{type(capture_error).__name__}')
        return False

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        if not self._try_acquire_queue_token():
            return False

        if self.instance_guard and self.instance_guard.token_lost:
            logger.warning(f'Token lost, stopping emulator and rejoining queue')
            try:
                self.device.emulator_stop()
            except Exception:
                pass
            self._emulator_down = True
            self.instance_guard.release()
            return False

        recovery_was_active = getattr(self.device, '_account_session_recovery', False)
        self.device._account_session_recovery = command == 'Restart'
        try:
            self.device.screenshot()
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name+'.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            task_module.ScriptTask(config=self.config, device=self.device).run()
        except AccountLoggedInElsewhere as error:
            return self._recover_account_session(command, error)
        except TaskEnd:
            return True
        except GameNotRunningError as e:
            if self._check_account_session_before_recovery(command):
                return False
            logger.warning(e)
            self.exception_handler(e=e, command=command)
            self.config.task_call('Restart')
            return True
        except (GameStuckError, GameTooManyClickError) as e:
            if self._check_account_session_before_recovery(command):
                return False
            logger.error(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GameBugError as e:
            if self._check_account_session_before_recovery(command):
                return False
            logger.warning(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError as e:
            if self._check_account_session_before_recovery(command):
                return False
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            # 这个还不重要 留着坑填
            logger.critical('Game page unknown')
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> GamePageUnknownError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except ScriptError as e:
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> ScriptError")
            exit(1)
        except RequestHumanTakeover as e:
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('Request human takeover')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> RequestHumanTakeover")
            exit(1)
        except Exception as e:
            logger.exception(e)
            self.exception_handler(e=e, command=command)
            self.save_error_log()
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}', content=f"<{self.config_name}> Exception occured")
            exit(1)
        finally:
            self.device._account_session_recovery = recovery_was_active

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        with _log_switch_lock:
            logger.set_file_logger(self.config_name, do_cleanup=True)
        start_day = date.today()
        logger.info(f'Start scheduler loop: {self.config_name}')
        self.config.model.running_task = ''
        self.anti_ban_guard.reset()

        # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
        if not self.config.script.device.run_background_only and IS_WINDOWS:
            from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            if self.config.script.device.emulator_window_minimize:
                minimize_by_name(target_window_name)
                logger.info(f'重新显示: {target_window_name}')
            else:
                show_window_by_name(target_window_name)
                
        while 1:
            if date.today() > start_day:
                with _log_switch_lock:
                    logger.set_file_logger(self.config_name, do_cleanup=True)
                start_day = date.today()
            # Check update event from GUI
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"Alas [{self.config_name}] exited.")
            #         break

            # Check game server maintenance
            # self.checker.wait_until_available()
            # if self.checker.is_recovered():
            #     # There is an accidental bug hard to reproduce
            #     # Sometimes, config won't be updated due to blocking
            #     # even though it has been changed
            #     # So update it once recovered
            #     del_cached_property(self, 'config')
            #     logger.info('Server or network is recovered. Restart game client')
            #     self.config.task_call('Restart')

            # Get task
            task = self._get_next_task_with_recovery()
            # Skip first restart
            if self.is_first_task and task == 'Restart':
                logger.info('Skip task `Restart` at scheduler start')
                self.config.task_delay(task='Restart', success=True, server=True)
                del_cached_property(self, 'config')
                continue

            if self._emulator_down:
                self.device = Device(self.config)
                self._emulator_down = False
            else:
                _ = self.device

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            self.config.model.running_task = task
            _task_start = datetime.now()
            success = self.run(inflection.camelize(task))
            self.config.model.running_task = ''
            logger.info(f'Scheduler: End task `{task}`')
            self.is_first_task = False
            self.anti_ban_guard.record_active((datetime.now() - _task_start).total_seconds())

            self._record_task_result(task, success)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.script.error.handle_error:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                # self.checker.check_now()
                continue
            else:
                break

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_ocr_server_started()
    script = Script("oas1")
    script.loop()
