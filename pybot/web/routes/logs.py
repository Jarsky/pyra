"""Logs route — log viewer with filtering."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

if TYPE_CHECKING:
    from pybot.core.database import Log

router = APIRouter()


def _serialize_log_row(log: "Log") -> str:
    ts = log.logged_at.strftime("%Y-%m-%d %H:%M:%S") if log.logged_at else "?"
    channel = log.channel or "—"
    message = log.message or ""
    return f"[{ts}] [{channel}] [{log.event_type}] <{log.nick}> {message}"


@router.get("/", response_class=HTMLResponse)
async def logs_view(
    request: Request,
    username: str = Depends(get_current_user),
    channel: str = "",
    nick: str = "",
    event_type: str = "",
    page: int = 1,
) -> HTMLResponse:
    from sqlalchemy import select

    from pybot.core.database import Log, get_session

    limit = 100
    offset = (page - 1) * limit

    async with get_session() as session:
        query = select(Log).order_by(Log.logged_at.desc())
        if channel:
            query = query.where(Log.channel == channel)
        if nick:
            query = query.where(Log.nick.ilike(f"%{nick}%"))
        if event_type:
            query = query.where(Log.event_type == event_type.upper())
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        logs = result.scalars().all()

    file_logs: list[str] = []
    if not logs:
        log_path = Path(request.app.state.bot.config.core.log_file)
        if not log_path.is_absolute():
            data_dir = Path(os.environ.get("DATA_DIR", "data"))
            if log_path.parts and log_path.parts[0] == "data":
                remainder = Path(*log_path.parts[1:]) if len(log_path.parts) > 1 else Path()
                log_path = data_dir / remainder
            else:
                log_path = data_dir / log_path

        if log_path.exists():
            file_logs = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "request": request,
            "username": username,
            "logs": logs,
            "filter_channel": channel,
            "filter_nick": nick,
            "filter_event": event_type,
            "file_logs": file_logs,
            "page": page,
        },
    )


@router.get("/stream")
async def logs_stream(
    request: Request,
    username: str = Depends(get_current_user),
    channel: str = "",
    nick: str = "",
    event_type: str = "",
) -> dict[str, list[str]]:
    from sqlalchemy import select

    from pybot.core.database import Log, get_session

    async with get_session() as session:
        query = select(Log).order_by(Log.logged_at.desc())
        if channel:
            query = query.where(Log.channel == channel)
        if nick:
            query = query.where(Log.nick.ilike(f"%{nick}%"))
        if event_type:
            query = query.where(Log.event_type == event_type.upper())
        query = query.limit(200)
        result = await session.execute(query)
        logs = list(reversed(result.scalars().all()))

    lines = [_serialize_log_row(log) for log in logs]

    if not lines:
        log_path = Path(request.app.state.bot.config.core.log_file)
        if not log_path.is_absolute():
            data_dir = Path(os.environ.get("DATA_DIR", "data"))
            if log_path.parts and log_path.parts[0] == "data":
                remainder = Path(*log_path.parts[1:]) if len(log_path.parts) > 1 else Path()
                log_path = data_dir / remainder
            else:
                log_path = data_dir / log_path
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]

    return {"lines": lines}
