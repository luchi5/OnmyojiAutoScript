# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
from module.config.utils import convert_to_underscore
from module.config.config_model import ConfigConflictError, EXPECTED_UNSET

from module.logger import logger
from module.server.main_manager import mm
from module.server.script_process import ScriptProcess, ScriptState

from tasks.Component.config_base import TimeDelta
from tasks.Component.daily_closeout import closeout_enabled


script_app = APIRouter()


def _expected_value(value):
    if value is None:
        return EXPECTED_UNSET
    try:
        return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail='expected_value must be valid JSON') from None


@script_app.get('/test')
async def script_test():
    return 'success'

@script_app.get('/script_menu')
async def script_menu():
    return mm.config_cache('template').gui_menu_list
# ----------------------------------   配置文件管理   ----------------------------------
@script_app.get('/config_list')
async def config_list():
    return mm.all_script_files()

@script_app.post('/config_copy')
async def config_copy(file: str, template: str = 'template'):
    mm.copy(file, template)
    return mm.all_script_files()

@script_app.get('/config_new_name')
async def config_new_name():
    return mm.generate_script_name()

@script_app.get('/config_all')
async def config_all():
    return mm.all_json_file()


@script_app.put('/config')
async def config_rename(old_name: str = '', new_name: str = ''):
    """
    update config name
    :param old_name: old config name
    :param new_name: new config name
    :return: True or False
    """
    if old_name == new_name or new_name == '':
        return False
    process = mm.script_process.get(old_name)
    changed = (await process.retire(lambda: mm.rename(old_name, new_name))
               if process is not None else mm.rename(old_name, new_name))
    if not changed:
        raise HTTPException(status_code=400, detail='Rename failed')
    if process is not None and mm.script_process.get(old_name) is process:
        del mm.script_process[old_name]
    mm._all_script_files = [new_name if name == old_name else name
                            for name in mm._all_script_files]
    return True


@script_app.delete('/config')
async def config_delete(name: str = ''):
    """
    delete config file
    :param name: config name
    :return: True or False
    """
    if name == '' or name == 'template':
        raise HTTPException(status_code=400, detail='Delete failed')
    process = mm.script_process.get(name)
    changed = (await process.retire(lambda: mm.delete(name))
               if process is not None else mm.delete(name))
    if not changed:
        raise HTTPException(status_code=400, detail='Delete failed')
    if process is not None and mm.script_process.get(name) is process:
        del mm.script_process[name]
    mm._all_script_files = [item for item in mm._all_script_files if item != name]
    return True


@script_app.put('/config/task/copy')
async def task_copy(task_name: str, dest_config_name: str, source_config_name: str):
    if dest_config_name not in mm.script_process or source_config_name not in mm.script_process:
        return False
    source_task = getattr(mm.config_cache(source_config_name).model, convert_to_underscore(task_name), None)
    if source_task is None:
        return False
    return mm.config_cache(dest_config_name).model.copy_script_task(task_name, source_task)


@script_app.put('/config/task/group/copy')
async def task_group_copy(task_name: str, group_name: str, dest_config_name: str, source_config_name: str):
    if dest_config_name not in mm.script_process or source_config_name not in mm.script_process:
        return False
    source_task = getattr(mm.config_cache(source_config_name).model, convert_to_underscore(task_name), None)
    if source_task is None:
        return False
    return mm.config_cache(dest_config_name).model.copy_task_group(task_name, group_name, source_task)


# ---------------------------------   脚本实例管理   ----------------------------------
@script_app.get('/{script_name}/start')
async def script_start(script_name: str):
    if script_name not in mm.script_process:
        mm.script_process[script_name] = ScriptProcess(script_name)
    await mm.script_process[script_name].start()
    return

@script_app.get('/{script_name}/stop')
async def script_stop(script_name: str):
    if script_name not in mm.script_process:
        logger.warning(f'[{script_name}] script process does not exist')
        return
    await mm.script_process[script_name].stop()
    return

@script_app.get('/{script_name}/{task}/args')
async def script_task(script_name: str, task: str):
    return mm.config_cache(script_name).model.script_task(task)

@script_app.put('/{script_name}/{task}/{group}/{argument}/value')
async def script_task(script_name: str, task: str, group: str, argument: str, types: str, value,
                      expected_value: str | None = None):
    try:
        match types:
            case 'integer':
                value = int(value)
            case 'number':
                value = float(value)
            case 'boolean':
                if isinstance(value, str):
                    logger.warning(f'[{script_name}] script argument {argument} value is string, try to convert to bool')
                    if value.lower() in ['true', '1']:
                        value = True
                    elif value.lower() in ['false', '0']:
                        value = False
                value = bool(value)
            case 'string':
                pass
            case 'date_time':
                value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            case 'time_delta':
                # strptime 是个好东西，但是不能解析00的天数
                day = int(value[1])
                date_time = datetime.strptime(value[3:], '%H:%M:%S')
                value = TimeDelta(days=day, hours=date_time.hour, minutes=date_time.minute, seconds=date_time.second)
            case 'time':
                value = datetime.strptime(value, '%H:%M:%S').time()
            case _: pass
    except Exception as e:
        # 类型不正确
        raise HTTPException(status_code=400, detail=f'Argument type error: {e}')
    expected = _expected_value(expected_value)
    try:
        model = mm.config_cache(script_name).model
        if expected is EXPECTED_UNSET:
            return model.script_set_arg(task, group, argument, value)
        return model.script_set_arg(task, group, argument, value, expected_value=expected)
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@script_app.put('/{script_name}/{task}/sync_next_run')
async def sync_next_run(script_name: str, task: str, target_dt: str, expected_value: str | None = None):
    if script_name not in mm.script_process:
        return False
    config = mm.config_cache(script_name)
    target = datetime.strptime(target_dt, '%Y-%m-%d %H:%M:%S') if target_dt else None
    expected = _expected_value(expected_value)
    expected_args = {} if expected is EXPECTED_UNSET else {'expected_value': expected}
    try:
        if (convert_to_underscore(task) == 'talisman_pass' and closeout_enabled(config)
                and target is not None and target <= datetime.now()):
            # Preserve explicit immediate-run intent for dynamic closeout as well.
            config.model.script_set_arg(task, 'scheduler', 'next_run', target, **expected_args)
        else:
            config.task_delay(task=task, success=True, target=target, **expected_args)
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    script_process = mm.script_process[script_name]
    config.get_next()
    await script_process.broadcast_state({"schedule": config.get_schedule_data()})
    return True


# --------------------------------------  SSE  --------------------------------------
@script_app.get('/{script_name}/state')
async def script_task_state(script_name: str):
    async def state_generate_events():
        while True:
            # 生成 SSE 事件数据
            event_data = "data: Hello, SSE!\n\n"
            yield event_data

            # 模拟异步操作，可以替换为您的实际处理逻辑
            await asyncio.sleep(1)

    response = StreamingResponse(state_generate_events(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    return response

@script_app.get('/{script_name}/log')
async def script_task_log(script_name: str):
    async def log_generate_events():
        while True:
            # 生成 SSE 事件数据
            event_data = "data: log\n"
            yield event_data

            # 模拟异步操作，可以替换为您的实际处理逻辑
            await asyncio.sleep(1)

    response = StreamingResponse(log_generate_events(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    return response

# -------------------------------------- websocket --------------------------------------

@script_app.websocket("/ws/{script_name}")
async def websocket_endpoint(websocket: WebSocket, script_name: str):
    if script_name not in mm.script_process:
        mm.script_process[script_name] = ScriptProcess(script_name)
    script_process = mm.script_process[script_name]
    await script_process.connect(websocket)

    try:
        await script_process.refresh_state()
        await script_process.send_json(websocket, {"state": script_process.state})
        config = mm.config_cache(script_name)
        config.get_next()
        await script_process.send_json(websocket, {"schedule": config.get_schedule_data()})

        while True:
            # 初次进入，广播state schedule
            data = await websocket.receive_text()
            if data == 'get_state':
                await script_process.refresh_state()
                await script_process.broadcast_state({"state": script_process.state})
            elif data == 'get_schedule':
                config = mm.config_cache(script_name)
                config.get_next()
                await script_process.broadcast_state({"schedule": config.get_schedule_data()})
            elif data == 'start':
                await script_process.start()
            elif data == 'stop':
                await script_process.stop()

    except WebSocketDisconnect:
        logger.warning(f'[{script_name}] websocket disconnect')
        await script_process.disconnect(websocket)
    except Exception as e:
        logger.exception(f'[{script_name}] websocket error: {e}')
        await script_process.disconnect(websocket)
