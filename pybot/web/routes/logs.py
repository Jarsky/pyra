"""Logs route — log viewer with filtering."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


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
            "page": page,
        },
    )
