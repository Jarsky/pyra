"""Dashboard route — bot status, stats, recent logs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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

    from sqlalchemy import select, text

    from pybot.core.database import Log, get_session

    database_ok = False
    async with get_session() as session:
        await session.execute(text("SELECT 1"))
        database_ok = True
        result = await session.execute(select(Log).order_by(Log.logged_at.desc()).limit(50))
        logs = result.scalars().all()

    loaded_plugins = bot.plugin_loader.get_loaded_plugins() if bot.plugin_loader else {}
    available_plugins = bot.plugin_loader.get_available_plugins() if bot.plugin_loader else {}
    partyline_sessions = getattr(bot.partyline, "_sessions", []) if bot.partyline else []
    config_path = str(bot._resolve_runtime_config_path())
    health = {
        "irc": "Connected" if bot.irc.connected else "Disconnected",
        "registration": "Registered" if bot.irc.registered else "Handshake pending",
        "database": "Healthy" if database_ok else "Unavailable",
        "plugins": f"{len(loaded_plugins)} loaded / {len(available_plugins)} available",
    }
    runtime = {
        "config_path": config_path,
        "database_url": bot.config.database.url,
        "web_bind": f"{bot.config.web.host}:{bot.config.web.port}",
        "partyline_bind": f"{bot.config.partyline.host}:{bot.config.partyline.port}",
        "admins": str(len([session for session in partyline_sessions if session.authenticated])),
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "username": username,
            "nick": bot.nick,
            "server": f"{bot.config.primary_server.host}:{bot.config.primary_server.port}",
            "uptime": uptime_str,
            "channels": channels,
            "connected": bot.irc.connected,
            "registered": bot.irc.registered,
            "logs": logs,
            "version": _get_version(),
            "health": health,
            "runtime": runtime,
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


@router.post("/control/{action}")
async def dashboard_control(
    action: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    bot = request.app.state.bot

    if username.lower() != bot.config.core.owner.lower():
        return RedirectResponse(
            url="/?error=Only+the+configured+owner+can+run+dashboard+control+actions.",
            status_code=303,
        )

    try:
        if action == "reload":
            await bot.reload_runtime()
            return RedirectResponse(url="/?success=Reloaded+config+and+plugins.", status_code=303)
        if action == "restart":
            await bot.restart_process()
            return RedirectResponse(url="/?info=Restart+requested.", status_code=303)
        if action == "shutdown":
            await bot.shutdown_process("Shutdown from dashboard")
            return RedirectResponse(url="/?info=Shutdown+requested.", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            url=f"/?error=Dashboard+action+failed%3A+{type(exc).__name__}",
            status_code=303,
        )

    return RedirectResponse(url="/?error=Unknown+dashboard+action.", status_code=303)
