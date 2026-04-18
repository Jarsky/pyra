"""Channels management route."""

from __future__ import annotations

from urllib.parse import quote

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
    server_name = f"{bot.config.primary_server.host}:{bot.config.primary_server.port}"
    grouped_channels = [{"server": server_name, "channels": channels}]

    channel_urls = {ch.name: quote(ch.name, safe="") for ch in channels}

    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "request": request,
            "username": username,
            "channels": channels,
            "grouped_channels": grouped_channels,
            "channel_urls": channel_urls,
        },
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


@router.get("/{channel_name:path}/admin", response_class=HTMLResponse)
async def channel_admin(
    channel_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    channel = bot.get_channel(channel_name)

    bot_nick_state = channel.get_nick(bot.nick) if channel else None
    can_moderate = bool(bot_nick_state and "o" in bot_nick_state.modes)

    users = []
    if channel:
        users = sorted(
            channel.nicks.values(),
            key=lambda ns: ("o" not in ns.modes, "v" not in ns.modes, ns.nick.lower()),
        )

    return templates.TemplateResponse(
        request,
        "channel_admin.html",
        {
            "request": request,
            "username": username,
            "channel_name": channel_name,
            "channel": channel,
            "users": users,
            "can_moderate": can_moderate,
            "bot_nick": bot.nick,
        },
    )


@router.post("/{channel_name:path}/admin/action")
async def channel_admin_action(
    channel_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    form = await request.form()
    action = str(form.get("action", "")).strip().lower()
    bot = request.app.state.bot
    channel = bot.get_channel(channel_name)
    bot_nick_state = channel.get_nick(bot.nick) if channel else None
    can_moderate = bool(bot_nick_state and "o" in bot_nick_state.modes)

    if action == "topic":
        topic = str(form.get("topic", "")).strip()
        if can_moderate and topic:
            await bot.topic(channel_name, topic)
    elif action == "mode":
        mode_str = str(form.get("mode", "")).strip()
        mode_args = str(form.get("mode_args", "")).strip().split()
        if can_moderate and mode_str:
            await bot.mode(channel_name, mode_str, *mode_args)
    elif action == "kick":
        nick = str(form.get("nick", "")).strip()
        reason = str(form.get("reason", "")).strip()
        if can_moderate and nick:
            await bot.kick(channel_name, nick, reason)
    elif action == "ban":
        hostmask = str(form.get("hostmask", "")).strip()
        if can_moderate and hostmask:
            await bot.ban(channel_name, hostmask)
    elif action == "unban":
        hostmask = str(form.get("hostmask", "")).strip()
        if can_moderate and hostmask:
            await bot.unban(channel_name, hostmask)
    elif action == "banlist":
        if can_moderate:
            await bot.mode(channel_name, "+b")

    return RedirectResponse(url=f"/channels/{channel_name}/admin", status_code=303)
