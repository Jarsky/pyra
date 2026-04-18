"""Settings route — config editor."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


def _resolve_config_path() -> Path:
    configured = os.environ.get("CONFIG_FILE")
    if configured:
        return Path(configured)

    docker_default = Path("/data/config.yaml")
    if docker_default.exists() or Path("/.dockerenv").exists():
        return docker_default

    return Path("config/config.yaml")


@router.get("/", response_class=HTMLResponse)
async def settings_view(
    request: Request,
    username: str = Depends(get_current_user),
    saved: str = "",
    error: str = "",
) -> HTMLResponse:
    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import has_flag

    bot = request.app.state.bot
    config_path = _resolve_config_path()
    if config_path.exists():
        config_yaml = config_path.read_text(encoding="utf-8")
    else:
        config_yaml = yaml.dump(bot.config.model_dump(mode="json"), default_flow_style=False)

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == username))
        user = result.scalar_one_or_none()
        is_owner = bool(user and await has_flag(session, user.hostmask, "n"))

    status = {
        "nick": bot.nick,
        "server": f"{bot.config.primary_server.host}:{bot.config.primary_server.port}",
        "uptime": f"{int(bot.uptime_seconds)}s",
        "channels": len(bot.channels),
        "plugins": len(bot.plugin_loader.get_loaded_plugins()) if bot.plugin_loader else 0,
        "db_url": bot.config.database.url,
    }

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "username": username,
            "config_yaml": config_yaml,
            "is_owner": is_owner,
            "status": status,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/")
async def save_settings(
    request: Request,
    username: str = Depends(get_current_user),
    config_yaml: str = Form(...),
) -> RedirectResponse:
    # Require owner flag
    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import has_flag

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == username))
        user = result.scalar_one_or_none()
        if not user or not await has_flag(session, user.hostmask, "n"):
            return RedirectResponse(
                url="/settings?error=Owner+flag+required+to+edit+settings.",
                status_code=303,
            )

    try:
        parsed = yaml.safe_load(config_yaml)
        from pybot.core.config import BotConfig

        BotConfig.model_validate(parsed)  # validate before saving
    except Exception as exc:
        import urllib.parse

        return RedirectResponse(
            url=f"/settings?error={urllib.parse.quote(str(exc))}",
            status_code=303,
        )

    config_path = _resolve_config_path()
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(config_yaml, encoding="utf-8")

    return RedirectResponse(
        url="/settings?success=Settings+saved+successfully.",
        status_code=303,
    )
