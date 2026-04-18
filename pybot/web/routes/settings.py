"""Settings route — config editor."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def settings_view(
    request: Request,
    username: str = Depends(get_current_user),
    saved: str = "",
    error: str = "",
) -> HTMLResponse:
    bot = request.app.state.bot
    config_path = Path("config/config.yaml")
    if config_path.exists():
        raw_yaml = config_path.read_text(encoding="utf-8")
    else:
        raw_yaml = yaml.dump(bot.config.model_dump(mode="json"), default_flow_style=False)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "username": username,
            "raw_yaml": raw_yaml,
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
                url="/settings?error=Owner+required", status_code=303
            )

    try:
        parsed = yaml.safe_load(config_yaml)
        from pybot.core.config import BotConfig

        BotConfig.model_validate(parsed)  # validate before saving
    except Exception as exc:
        import urllib.parse

        return RedirectResponse(
            url=f"/settings?error={urllib.parse.quote(str(exc))}", status_code=303
        )

    config_path = Path("config/config.yaml")
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(config_yaml, encoding="utf-8")

    return RedirectResponse(url="/settings?saved=1", status_code=303)
