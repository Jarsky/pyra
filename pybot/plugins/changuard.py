"""
Channel guard — Eggdrop-style channel enforcement.

Author:  Jarsky
Version: 1.0.0
Date:    2026-10-26

Passive, no commands. Acts only in channels where the bot has ops:
  - When someone joins:
      * a stored ban (this channel or global, see !ban/!tempban) that matches them:
        ban + kick (users with o/a/n flags are exempt)
      * 'k' flag (global or channel) or channel flag 'b': kickban
      * channel setting autoop=true: op users with the 'o' flag (global or channel);
        autovoice=true: voice users with 'v'
  - When the bot is opped: the same checks for everyone already in the channel.
  - Every 30 seconds: expired bans are lifted (-b) and marked inactive. This survives
    restarts, unlike timers.
Owners are never kicked or banned. Set the channel setting enforce_bans=false to stop
ban/kick enforcement in a channel (auto-op/voice still apply).
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-10-26",
    "description": "Enforces bans, autokick and channel bans on join; auto-op/voice; ban expiry.",
    "url": "https://github.com/Jarsky/pyra",
}

from datetime import datetime, timezone
from typing import Any

from pybot import plugin
from pybot.plugin import Trigger

_OP_MODES = {"o", "a", "q", "y"}  # op and higher prefixes


def _aware(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; they are stored as UTC
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _host_ban_mask(hostmask: str) -> str:
    """nick!user@host -> *!*@host (the usual Eggdrop kickban mask)."""
    if "@" in hostmask:
        return f"*!*@{hostmask.split('@', 1)[1]}"
    return hostmask


async def _matching_ban(session: Any, channel: str, hostmask: str) -> Any:
    from sqlalchemy import or_, select

    from pybot.core.database import Ban, Channel
    from pybot.core.permissions import match_hostmask

    result = await session.execute(
        select(Ban)
        .outerjoin(Channel, Ban.channel_id == Channel.id)
        .where(
            Ban.active == True,  # noqa: E712
            or_(Ban.channel_id.is_(None), Channel.name == channel.lower()),
        )
    )
    now = datetime.now(tz=timezone.utc)
    for ban in result.scalars().all():
        expires = _aware(ban.expires_at)
        if (expires is None or expires > now) and match_hostmask(ban.hostmask, hostmask):
            return ban
    return None


async def check_member(
    bot: Any, channel: str, nick: str, hostmask: str, account: str | None
) -> str | None:
    """Enforce bans/autokick for one user; return the mode they should get ('o'/'v') or None."""
    from pybot.core.database import get_channel_setting, get_session, setting_enabled
    from pybot.core.permissions import flags_allow, get_identity_flags

    async with get_session() as session:
        global_flags, channel_flags = await get_identity_flags(session, hostmask, channel, account)
        enforce = setting_enabled(
            await get_channel_setting(session, channel, "enforce_bans", "true")
        )
        autoop = setting_enabled(await get_channel_setting(session, channel, "autoop", "false"))
        autovoice = setting_enabled(
            await get_channel_setting(session, channel, "autovoice", "false")
        )
        ban = await _matching_ban(session, channel, hostmask) if enforce else None

    flags = global_flags | channel_flags
    if enforce and "n" not in flags:
        autokick = "k" in flags
        if autokick or "b" in channel_flags:
            await bot.ban(channel, _host_ban_mask(hostmask))
            await bot.kick(channel, nick, "Autokick" if autokick else "Banned")
            return None
        if ban is not None and not flags & {"o", "a"}:
            state = bot.get_channel(channel)
            if state is None or ban.hostmask not in state.bans:
                await bot.ban(channel, ban.hostmask)
            await bot.kick(channel, nick, ban.reason or "Banned")
            return None

    member = bot.get_nick_in_channel(channel, nick)
    current: set[str] = member.modes if member else set()
    if current & _OP_MODES:
        return None
    if autoop and flags_allow(flags, "o"):
        return "o"
    if autovoice and flags_allow(flags, "v") and "v" not in current:
        return "v"
    return None


async def give_modes(bot: Any, channel: str, grants: list[tuple[str, str]]) -> None:
    """Send +o/+v grants batched by the server's MODES limit (default 3 per line)."""
    limit = bot.irc.isupport.get("MODES")
    per_line = int(limit) if isinstance(limit, str) and limit.isdigit() else 3
    for mode in ("o", "v"):
        nicks = [nick for nick, m in grants if m == mode]
        for i in range(0, len(nicks), per_line):
            chunk = nicks[i : i + per_line]
            await bot.mode(channel, "+" + mode * len(chunk), *chunk)


@plugin.event("JOIN")
async def _on_join(bot: Any, trigger: Trigger) -> None:
    channel = trigger.message.params[0] if trigger.message.params else ""
    if not channel or trigger.nick.lower() == bot.nick.lower():
        return
    if not bot.can_set_modes(channel):
        return
    mode = await check_member(bot, channel, trigger.nick, trigger.hostmask, trigger.account)
    if mode:
        await give_modes(bot, channel, [(trigger.nick, mode)])


def _bot_was_opped(bot: Any, modestr: str, args: list[str]) -> bool:
    setting, index = True, 0
    for char in modestr:
        if char in "+-":
            setting = char == "+"
            continue
        arg = None
        if bot.irc.mode_takes_parameter(char, setting) and index < len(args):
            arg = args[index]
            index += 1
        if setting and char in _OP_MODES and arg and arg.lower() == bot.nick.lower():
            return True
    return False


@plugin.event("MODE")
async def _on_mode(bot: Any, trigger: Trigger) -> None:
    params = trigger.message.params
    if len(params) < 2 or params[0][:1] not in ("#", "&"):
        return
    if _bot_was_opped(bot, params[1], params[2:]):
        await sweep_channel(bot, params[0])


async def sweep_channel(bot: Any, channel: str) -> None:
    """Apply the join checks to everyone already in the channel (after the bot gets ops)."""
    state = bot.get_channel(channel)
    if state is None or not bot.can_set_modes(channel):
        return
    grants: list[tuple[str, str]] = []
    for member in list(state.nicks.values()):
        if member.nick.lower() == bot.nick.lower():
            continue
        mode = await check_member(bot, channel, member.nick, member.hostmask, member.account)
        if mode:
            grants.append((member.nick, mode))
    await give_modes(bot, channel, grants)


@plugin.interval(30)
async def expire_bans(bot: Any) -> None:
    """Lift and deactivate stored bans whose time is up."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import Ban, get_session

    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        result = await session.execute(
            select(Ban)
            .options(selectinload(Ban.channel_obj))
            .where(Ban.active == True, Ban.expires_at.is_not(None))  # noqa: E712
        )
        for ban in result.scalars().all():
            expires = _aware(ban.expires_at)
            if expires is None or expires > now:
                continue
            if ban.channel_obj is not None:
                targets = [ban.channel_obj.name]
            else:  # global ban: lift it wherever the bot has seen it set
                targets = [s.name for s in bot.channels.values() if ban.hostmask in s.bans]
            waiting = False
            for name in targets:
                if bot.get_channel(name) is None:
                    continue  # not in that channel: nothing the bot can lift
                if bot.can_set_modes(name):
                    await bot.unban(name, ban.hostmask)
                else:
                    waiting = True  # retry once the bot has ops
            if not waiting:
                ban.active = False
