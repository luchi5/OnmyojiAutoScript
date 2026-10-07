"""Optional read-only statistics API, compatible with OASX snapshots and SSE."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from module.server.log_stats import StatisticsInputError, log_stats_service

stats_app = APIRouter(prefix="/stats", tags=["stats"])


def _target_day(value: str) -> date:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "Invalid date format, expected YYYY-MM-DD") from exc


async def _read(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except StatisticsInputError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Log file changed or is temporarily unavailable; retry the request") from exc


@stats_app.get("/{script_name}/dates")
async def stats_available_dates(script_name: str):
    return await _read(log_stats_service.list_available_dates, script_name)


@stats_app.get("/{script_name}")
async def stats_snapshot(script_name: str, date_text: str = Query(..., alias="date")):
    return await _read(log_stats_service.build_stats, script_name, _target_day(date_text))


@stats_app.get("/{script_name}/stream")
async def stats_stream(request: Request, script_name: str, date_text: str = Query(..., alias="date")):
    target_day = _target_day(date_text)
    if target_day != date.today():
        raise HTTPException(400, "Only today's date supports SSE stream")
    # Validate and read before sending streaming response headers, preserving HTTP errors.
    initial = await _read(log_stats_service.build_stats, script_name, target_day)

    async def events():
        previous = initial
        yield "event: snapshot\ndata: " + json.dumps(previous, ensure_ascii=False, separators=(",", ":")) + "\n\n"
        ticks = 0
        while not await request.is_disconnected():
            await asyncio.sleep(1)
            if await request.is_disconnected():
                return
            if date.today() != target_day:
                yield "event: date_changed\ndata: {}\n\n"
                return
            current = await _read(log_stats_service.build_stats, script_name, target_day)
            if current != previous:
                # Repeated snapshots are supported by OASX and retain optional metadata.
                yield "event: snapshot\ndata: " + json.dumps(current, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                previous = current
            ticks += 1
            if ticks % 15 == 0:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
