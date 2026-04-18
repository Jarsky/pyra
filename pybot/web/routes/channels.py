"""Channels management route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def channels_list(
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    channels = list(bot.channels.values())
    return templates.TemplateResponse(
        request,
        "channels.html",
        {"request": request, "username": username, "channels": channels},
    )


@router.get("/{channel_name:path}/settings", response_class=HTMLResponse)
async def channel_settings(
    channel_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    ch = bot.get_channel(channel_name)

    from pybot.core.database import get_channel_setting, get_session

    settings_keys = [
        "greet",
        "greet_msg",
        "antispam",
        "url_titles",
        "flood_lines",
        "flood_seconds",
        "flood_action",
        "log",
        "autoop",
        "autovoice",
    ]
    settings = {}
    async with get_session() as session:
        for key in settings_keys:
            settings[key] = await get_channel_setting(session, channel_name, key, "")

    return templates.TemplateResponse(
        request,
        "channel_settings.html",
        {
            "request": request,
            "username": username,
            "channel": ch,
            "channel_name": channel_name,
            "settings": settings,
        },
    )


@router.post("/{channel_name:path}/settings")
async def save_channel_settings(
    channel_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    form = await request.form()
    from pybot.core.database import get_session, set_channel_setting

    async with get_session() as session:
        for key, value in form.items():
            await set_channel_setting(session, channel_name, key, str(value))

    return RedirectResponse(url=f"/channels/{channel_name}/settings?saved=1", status_code=303)
