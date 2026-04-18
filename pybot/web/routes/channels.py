"""Channels management route."""

from __future__ import annotations

import ipaddress
from urllib.parse import quote, urlencode

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


def _channel_url(channel_name: str, suffix: str, **params: str) -> str:
    base = f"/channels/{quote(channel_name, safe='')}/{suffix}"
    filtered = {key: value for key, value in params.items() if value}
    return f"{base}?{urlencode(filtered)}" if filtered else base


def _domain_mask(host: str) -> str | None:
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        parts = [part for part in host.split(".") if part]
        if len(parts) < 2:
            return None
        return f"*!*@*.{'.'.join(parts[-2:])}"


def _build_ban_mask(channel: object, nick_value: object, preset: str) -> str:
    nick = nick_value if isinstance(nick_value, str) else str(nick_value)
    nick = nick.strip()
    nick_state = channel.get_nick(nick) if channel and hasattr(channel, "get_nick") else None
    user = getattr(nick_state, "user", "") or "*"
    host = getattr(nick_state, "host", "") or "*"

    if preset == "exact":
        return f"{nick}!{user}@{host}"
    if preset == "ident" and user != "*":
        return f"*!{user}@*"
    if preset == "host" and host != "*":
        return f"*!*@{host}"
    if preset == "domain" and host != "*":
        domain_mask = _domain_mask(host)
        if domain_mask:
            return domain_mask
    return f"{nick}!*@*"


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

    return RedirectResponse(
        url=_channel_url(channel_name, "settings", success="Channel settings saved."),
        status_code=303,
    )


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
    moderated_actions = {"topic", "mode", "kick", "ban", "unban", "banlist"}

    if action in moderated_actions and not can_moderate:
        return RedirectResponse(
            url=_channel_url(
                channel_name,
                "admin",
                error="Bot needs channel operator mode (+o) for that action.",
            ),
            status_code=303,
        )

    if action == "topic":
        topic = get_form_str(form, "topic")
        if topic:
            await bot.topic(channel_name, topic)
            return RedirectResponse(
                url=_channel_url(channel_name, "admin", success="Channel topic updated."),
                status_code=303,
            )
    elif action == "mode":
        mode_str = get_form_str(form, "mode")
        mode_args_raw = get_form_str(form, "mode_args")
        mode_args = mode_args_raw.split() if mode_args_raw else []
        if mode_str:
            await bot.mode(channel_name, mode_str, *mode_args)
            return RedirectResponse(
                url=_channel_url(channel_name, "admin", success="Channel modes updated."),
                status_code=303,
            )
    elif action == "kick":
        nicks = form.getlist("selected_nicks")
        if not nicks:
            nick_val = get_form_str(form, "nick")
            nicks = [nick_val] if nick_val else []
        reason_val = get_form_str(form, "reason")
        if nicks:
            for nick in nicks:
                nick_str = nick if isinstance(nick, str) else str(nick)
                await bot.kick(channel_name, nick_str.strip(), reason_val)
            return RedirectResponse(
                url=_channel_url(channel_name, "admin", success="Selected users kicked."),
                status_code=303,
            )
        return RedirectResponse(
            url=_channel_url(channel_name, "admin", error="Select at least one user to kick."),
            status_code=303,
        )
    elif action == "ban":
        hostmasks = form.getlist("selected_hostmasks")
        selected_nicks = form.getlist("selected_nicks")
        preset = get_form_str(form, "ban_preset", "nick")
        reason_val = get_form_str(form, "ban_reason")
        if not hostmasks and selected_nicks:
            hostmasks = [
                _build_ban_mask(channel, nick, preset)
                for nick in selected_nicks
                if str(nick).strip()
            ]
        if not hostmasks:
            hostmask_val = get_form_str(form, "hostmask")
            hostmasks = [hostmask_val] if hostmask_val else []
        if hostmasks:
            for hostmask in hostmasks:
                hm = hostmask if isinstance(hostmask, str) else str(hostmask)
                hm = hm.strip()
                if hm:
                    await bot.ban(channel_name, hm)
                    if reason_val:
                        await bot.say(channel_name, f"Banned {hm} - {reason_val}")
            return RedirectResponse(
                url=_channel_url(channel_name, "admin", success="Ban mask(s) applied."),
                status_code=303,
            )
        return RedirectResponse(
            url=_channel_url(channel_name, "admin", error="Select a user or enter a ban mask."),
            status_code=303,
        )
    elif action == "unban":
        hostmasks = form.getlist("unban_selected")
        if not hostmasks:
            hostmask_val = get_form_str(form, "hostmask")
            hostmasks = [hostmask_val] if hostmask_val else []
        if hostmasks:
            for hostmask in hostmasks:
                hm = hostmask if isinstance(hostmask, str) else str(hostmask)
                hm = hm.strip()
                if hm:
                    await bot.unban(channel_name, hm)
            return RedirectResponse(
                url=_channel_url(channel_name, "admin", success="Selected bans removed."),
                status_code=303,
            )
        return RedirectResponse(
            url=_channel_url(
                channel_name,
                "admin",
                error="Select at least one ban mask to remove.",
            ),
            status_code=303,
        )
    elif action == "banlist":
        await bot.mode(channel_name, "+b")
        return RedirectResponse(
            url=_channel_url(
                channel_name,
                "admin",
                info="Requested fresh ban list from the server.",
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=_channel_url(channel_name, "admin", error="Unknown channel admin action."),
        status_code=303,
    )
