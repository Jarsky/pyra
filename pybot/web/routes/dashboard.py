"""Dashboard route — bot status, stats, recent logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot

    uptime_secs = int(bot.uptime_seconds)
    days, rem = divmod(uptime_secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    uptime_str = f"{days}d {hours}h {minutes}m"

    channels = [
        {"name": ch.name, "users": len(ch.nicks), "topic": ch.topic[:80]}
        for ch in bot.channels.values()
    ]

    from sqlalchemy import select

    from pybot.core.database import Log, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Log).order_by(Log.logged_at.desc()).limit(50)
        )
        logs = result.scalars().all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "username": username,
            "nick": bot.nick,
            "server": bot.config.primary_server.host,
            "uptime": uptime_str,
            "channels": channels,
            "connected": bot.irc.connected,
            "registered": bot.irc.registered,
            "logs": logs,
            "version": _get_version(),
        },
    )


@router.get("/api/stats")
async def api_stats(
    request: Request,
    username: str = Depends(get_current_user),
) -> dict:
    """HTMX polling endpoint — returns JSON stats for live dashboard update."""
    bot = request.app.state.bot
    uptime_secs = int(bot.uptime_seconds)
    return {
        "uptime_seconds": uptime_secs,
        "channel_count": len(bot.channels),
        "nick": bot.nick,
        "connected": bot.irc.connected,
    }


def _get_version() -> str:
    from pybot import __version__

    return __version__
