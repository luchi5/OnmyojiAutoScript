"""AzurTian-compatible config transfer endpoints with explicit backup/share modes."""
import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from module.server.config_transfer import ConfigTransferError, ConfigTransferService, MAX_BYTES, parse_json_source


def create_config_transfer_router(service=None):
    router = APIRouter()

    def current_service():
        if service is not None:
            return service
        from module.server.main_manager import mm
        return ConfigTransferService(Path.cwd() / "config", manager=mm)

    def fail(exc):
        detail = {"message": str(exc), "fields": exc.fields} if exc.fields else str(exc)
        raise HTTPException(status_code=exc.status, detail=detail)

    async def content(file):
        try:
            raw = await file.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ConfigTransferError("配置 JSON 不能超过 2 MiB", 413)
            return raw
        finally:
            await file.close()

    def download(data, filename):
        return Response(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False),
                        media_type="application/json; charset=utf-8", headers={
                            "Content-Disposition": f"attachment; filename=\"config.json\"; filename*=UTF-8''{quote(filename, safe='')}",
                            "Cache-Control": "no-store",
                        })

    @router.get("/config/transfer/capabilities")
    async def capabilities():
        return {"version": 1, "max_bytes": MAX_BYTES, "modes": ["backup", "share"],
                "config_import": True, "task_import": True}

    @router.post("/config/import")
    async def import_config(name: str = Form(...), file: UploadFile = File(...)):
        try:
            return current_service().import_config(name, parse_json_source(file_content=await content(file)))
        except ConfigTransferError as exc:
            fail(exc)

    @router.get("/config/export")
    async def export_config(name: str, mode: Literal["backup", "share"] = "share"):
        try:
            actual, data = current_service().export_config(name, mode)
            return download(data, f"{actual}-{mode}.json")
        except ConfigTransferError as exc:
            fail(exc)

    @router.post("/config/task/import")
    async def import_task(config_name: str = Form(...), task_name: str = Form(...),
                          json_text: str | None = Form(None), file: UploadFile | None = File(None)):
        try:
            raw = await content(file) if file is not None else None
            data = parse_json_source(json_text=json_text, file_content=raw)
            return current_service().import_task(config_name, task_name, data)
        except ConfigTransferError as exc:
            fail(exc)

    @router.get("/config/task/export")
    async def export_task(config_name: str, task_name: str, mode: Literal["backup", "share"] = "share"):
        try:
            actual, data = current_service().export_config(config_name, mode, task_name)
            return download(data, f"{actual}-{next(iter(data))}-{mode}.json")
        except ConfigTransferError as exc:
            fail(exc)

    @router.get("/config/task/copy-json")
    async def copy_json(config_name: str, task_name: str):
        try:
            _, data = current_service().export_config(config_name, "backup", task_name)
            return data
        except ConfigTransferError as exc:
            fail(exc)

    return router


config_transfer_app = create_config_transfer_router()
