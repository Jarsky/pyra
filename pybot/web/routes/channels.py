"""Channels management route."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import FormData

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


def get_form_str(form: FormData, key: str, default: str = "") -> str:
    """Safely extract and convert form field to string."""
    val_raw = form.get(key)
    if not val_raw:
        return default
    result = str(val_raw).strip()
    return result if result else default


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

    topic = channel.topic if channel else ""
    modes = channel.modes if channel else ""
    bans = sorted(channel.bans) if channel else []

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
            "topic": topic,
            "modes": modes,
            "bans": bans,
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
        topic = get_form_str(form, "topic")
        if can_moderate and topic:
            await bot.topic(channel_name, topic)
    elif action == "mode":
        mode_str = get_form_str(form, "mode")
        mode_args_raw = get_form_str(form, "mode_args")
        mode_args = mode_args_raw.split() if mode_args_raw else []
        if can_moderate and mode_str:
            await bot.mode(channel_name, mode_str, *mode_args)
    elif action == "kick":
        nicks = form.getlist("selected_nicks")
        if not nicks:
            nick_val = get_form_str(form, "nick")
            nicks = [nick_val] if nick_val else []
        reason_val = get_form_str(form, "reason")
        if can_moderate and nicks:
            for nick in nicks:
                nick_str = nick if isinstance(nick, str) else str(nick)
                await bot.kick(channel_name, nick_str.strip(), reason_val)
    elif action == "ban":
        hostmasks = form.getlist("selected_hostmasks")
        reason_val = get_form_str(form, "ban_reason")
        if not hostmasks:
            hostmask_val = get_form_str(form, "hostmask")
            hostmasks = [hostmask_val] if hostmask_val else []
        if can_moderate and hostmasks:
            for hostmask in hostmasks:
                hm = hostmask if isinstance(hostmask, str) else str(hostmask)
                hm = hm.strip()
                if hm:
                    await bot.ban(channel_name, hm)
                    if reason_val:
                        await bot.say(channel_name, f"Banned {hm} - {reason_val}")
    elif action == "unban":
        hostmasks = form.getlist("unban_selected")
        if not hostmasks:
            hostmask_val = get_form_str(form, "hostmask")
            hostmasks = [hostmask_val] if hostmask_val else []
        if can_moderate and hostmasks:
            for hostmask in hostmasks:
                hm = hostmask if isinstance(hostmask, str) else str(hostmask)
                hm = hm.strip()
                if hm:
                    await bot.unban(channel_name, hm)
    elif action == "banlist":
        if can_moderate:
            await bot.mode(channel_name, "+b")

    return RedirectResponse(url=f"/channels/{channel_name}/admin", status_code=303)
