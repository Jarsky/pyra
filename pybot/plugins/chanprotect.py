"""
Channel protection — Eggdrop-style flood protection and channel mode policing.

Author:  Jarsky
Version: 1.0.0
Date:    2026-10-26

Passive, no commands. Configure per channel with !chanset <key> <value> or the web UI:

  flood_join         5:60   joins per host within seconds -> kickban (0 = off)
  flood_nick         5:60   nick changes per host -> kickban
  flood_ctcp         3:60   CTCPs to the channel per host -> kickban
  flood_ban_minutes  10     how long flood kickbans last (lifted by changuard)
  autolimit          0      keep +l at (users + N) so mass joins stop at N; 0 = off
  chanmode           ""     modes to enforce, e.g. "+nt-i" or "+ntk secretkey"
  bitch              false  only users with the o flag may hold ops
  protectops         false  re-op o-flagged users deopped by someone without m/a/n

CTCP floods sent to the bot itself (5 within 30s from one host) get that host ignored for
5 minutes, so the bot can't be used to flood others with replies.

Everything except the CTCP ignore needs the bot to have ops. Flood checks skip owners,
admins, masters, users with o/v/X flags and users who are currently opped or voiced.
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-10-26",
    "description": "Join/nick/CTCP flood protection, auto +l, enforced modes, bitch, protectops.",
    "url": "https://github.com/Jarsky/pyra",
}

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from pybot import plugin
from pybot.plugin import Trigger

DEFAULTS = {
    "flood_join": "5:60",
    "flood_nick": "5:60",
    "flood_ctcp": "3:60",
    "flood_ban_minutes": "10",
    "autolimit": "0",
    "chanmode": "",
    "bitch": "false",
    "protectops": "false",
}
BOT_CTCP_FLOOD = (5, 30.0)  # CTCPs to the bot from one host within seconds
BOT_CTCP_IGNORE = timedelta(minutes=5)
LIMIT_DELAY = 15.0  # seconds to wait after joins/parts before adjusting +l
SETTINGS_TTL = 10.0  # seconds channel settings are cached
_OP_MODES = {"o", "a", "q", "y"}
_EXEMPT_FLAGS = {"n", "a", "m", "o", "v", "X"}

_events: dict[tuple[str, str, str], deque[float]] = defaultdict(lambda: deque(maxlen=50))
_settings_cache: dict[str, tuple[float, dict[str, str]]] = {}
_limit_tasks: dict[str, asyncio.Task[None]] = {}


# ── Settings ─────────────────────────────────────────────────────────────────


async def settings(channel: str) -> dict[str, str]:
    key = channel.lower()
    cached = _settings_cache.get(key)
    if cached and time.monotonic() - cached[0] < SETTINGS_TTL:
        return cached[1]
    from pybot.core.database import get_channel_setting, get_session

    async with get_session() as session:
        values = {k: await get_channel_setting(session, channel, k, d) for k, d in DEFAULTS.items()}
    _settings_cache[key] = (time.monotonic(), values)
    return values


def parse_rate(value: str) -> tuple[int, float] | None:
    """'5:60' -> (5, 60.0); '0', '' or nonsense -> None (off)."""
    try:
        count, seconds = value.split(":", 1)
        rate = (int(count), float(seconds))
    except ValueError:
        return None
    return rate if rate[0] > 0 and rate[1] > 0 else None


def enabled(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def parse_chanmode(value: str) -> tuple[dict[str, str | None], set[str]]:
    """'+ntk key -i' -> ({'n': None, 't': None, 'k': 'key'}, {'i'})."""
    words = value.split()
    on: dict[str, str | None] = {}
    off: set[str] = set()
    params = [w for w in words if w[:1] not in ("+", "-")]
    setting = True
    for word in (w for w in words if w[:1] in ("+", "-")):
        for char in word:
            if char in "+-":
                setting = char == "+"
            elif setting:
                on[char] = params.pop(0) if char in ("k", "l") and params else None
                off.discard(char)
            else:
                off.add(char)
                on.pop(char, None)
    if "l" in on and not (on["l"] or "").isdigit():
        on.pop("l")  # +l needs a number
    if "k" in on and not on["k"]:
        on.pop("k")  # +k needs a key
    return on, off


# ── Helpers ──────────────────────────────────────────────────────────────────


def _flooded(kind: str, channel: str, host: str, rate: tuple[int, float]) -> bool:
    now = time.monotonic()
    events = _events[(kind, channel.lower(), host.lower())]
    events.append(now)
    recent = [t for t in events if now - t <= rate[1]]
    if len(recent) >= rate[0]:
        events.clear()
        return True
    return False


async def _identity_flags(bot: Any, channel: str, hostmask: str, account: str | None) -> set[str]:
    from pybot.core.database import get_session
    from pybot.core.permissions import get_flags

    async with get_session() as session:
        return await get_flags(session, hostmask, channel, account)


async def _exempt(bot: Any, channel: str, nick: str, hostmask: str, account: str | None) -> bool:
    member = bot.get_nick_in_channel(channel, nick)
    if member and member.modes & (_OP_MODES | {"h", "v"}):
        return True
    return bool(await _identity_flags(bot, channel, hostmask, account) & _EXEMPT_FLAGS)


async def _flood_kickban(bot: Any, channel: str, nick: str, hostmask: str, reason: str) -> None:
    from pybot.core.database import add_ban, get_session

    mask = f"*!*@{hostmask.split('@', 1)[1]}" if "@" in hostmask else hostmask
    minutes = int((await settings(channel))["flood_ban_minutes"] or 10)
    await bot.ban(channel, mask)
    await bot.kick(channel, nick, reason)
    async with get_session() as session:  # expired and lifted by changuard
        await add_ban(
            session,
            channel,
            mask,
            reason,
            bot.nick,
            datetime.now(tz=timezone.utc) + timedelta(minutes=max(1, minutes)),
        )


async def _check_flood(
    bot: Any, kind: str, channel: str, nick: str, hostmask: str, account: str | None
) -> bool:
    """Count one event; kickban if the channel's limit for this kind is exceeded."""
    rate = parse_rate((await settings(channel))[f"flood_{kind}"])
    host = hostmask.split("@", 1)[1] if "@" in hostmask else hostmask
    if rate is None or not bot.can_set_modes(channel) or not _flooded(kind, channel, host, rate):
        return False
    if await _exempt(bot, channel, nick, hostmask, account):
        return False
    await _flood_kickban(bot, channel, nick, hostmask, f"{kind.capitalize()} flood")
    return True


# ── Flood events ─────────────────────────────────────────────────────────────


@plugin.event("JOIN")
async def _on_join(bot: Any, trigger: Trigger) -> None:
    channel = trigger.message.params[0] if trigger.message.params else ""
    if not channel or trigger.nick.lower() == bot.nick.lower():
        return
    await _check_flood(bot, "join", channel, trigger.nick, trigger.hostmask, trigger.account)
    schedule_limit(bot, channel)


@plugin.event("NICK")
async def _on_nick(bot: Any, trigger: Trigger) -> None:
    new_nick = trigger.message.text
    if trigger.nick.lower() == bot.nick.lower() or not new_nick:
        return
    hostmask = f"{new_nick}!{trigger.user}@{trigger.host}"
    for state in list(bot.channels.values()):
        if state.get_nick(new_nick):
            await _check_flood(bot, "nick", state.name, new_nick, hostmask, trigger.account)


@plugin.event("PRIVMSG")
async def _on_privmsg(bot: Any, trigger: Trigger) -> None:
    ctcp = trigger.message.ctcp_command
    if not ctcp or ctcp == "ACTION" or trigger.nick.lower() == bot.nick.lower():
        return
    if trigger.channel:
        await _check_flood(
            bot, "ctcp", trigger.channel, trigger.nick, trigger.hostmask, trigger.account
        )
    elif not trigger.admin and _flooded(
        "ctcp", "@bot", trigger.host or trigger.nick, BOT_CTCP_FLOOD
    ):
        await _ignore_temporarily(bot, trigger)


async def _ignore_temporarily(bot: Any, trigger: Trigger) -> None:
    from pybot.core.audit import audit
    from pybot.core.database import Ignore, get_session

    mask = f"*!*@{trigger.host}" if trigger.host else f"{trigger.nick}!*@*"
    async with get_session() as session:
        session.add(
            Ignore(
                hostmask=mask,
                reason="CTCP flood",
                set_by=bot.nick,
                set_at=datetime.now(tz=timezone.utc),
                expires_at=datetime.now(tz=timezone.utc) + BOT_CTCP_IGNORE,
                active=True,
            )
        )
        await audit(bot.nick, "ignore.add", mask, "CTCP flood, 5 minutes", "system", session)


@plugin.event("PART")
@plugin.event("QUIT")
@plugin.event("KICK")
async def _on_leave(bot: Any, trigger: Trigger) -> None:
    if trigger.message.command == "QUIT":
        for state in list(bot.channels.values()):
            schedule_limit(bot, state.name)
    elif trigger.message.params:
        schedule_limit(bot, trigger.message.params[0])


# ── Auto limit ───────────────────────────────────────────────────────────────


def schedule_limit(bot: Any, channel: str) -> None:
    """Adjust +l shortly, once, however many joins/parts happen meanwhile."""
    key = channel.lower()
    task = _limit_tasks.get(key)
    if task is None or task.done():
        _limit_tasks[key] = asyncio.create_task(_delayed_limit(bot, channel))


async def _delayed_limit(bot: Any, channel: str) -> None:
    await asyncio.sleep(LIMIT_DELAY)
    await apply_limit(bot, channel)


async def apply_limit(bot: Any, channel: str) -> None:
    state = bot.get_channel(channel)
    if state is None or not bot.can_set_modes(channel):
        return
    try:
        extra = int((await settings(channel))["autolimit"] or 0)
    except ValueError:
        return
    if extra <= 0:
        return
    target = len(state.nicks) + extra
    # Leave small drifts alone so the limit doesn't change on every join
    if state.limit is None or state.limit <= len(state.nicks) or abs(state.limit - target) >= 2:
        if state.limit != target:
            await bot.mode(channel, "+l", str(target))


@plugin.interval(60)
async def check_limits(bot: Any) -> None:
    for state in list(bot.channels.values()):
        await apply_limit(bot, state.name)


# ── Mode policing: chanmode, bitch, protectops ───────────────────────────────


def _list_modes(bot: Any) -> set[str]:
    return set(bot.irc.chanmodes_a) | {"b", "e", "I"}


def _enforce_changes(
    bot: Any, chanmode: str, changes: list[tuple[str, bool, str | None]]
) -> list[tuple[str, bool, str | None]]:
    """Reverts for changes that break the channel's chanmode."""
    on, off = parse_chanmode(chanmode)
    skip = _list_modes(bot) | set(bot.irc.nick_prefix_modes)
    reverts: list[tuple[str, bool, str | None]] = []
    for char, setting, arg in changes:
        if char in skip:
            continue
        if setting and char in off:
            reverts.append((char, False, arg if char == "k" else None))
        elif not setting and char in on:
            reverts.append((char, True, on[char]))
    return reverts


def _missing_modes(state: Any, chanmode: str) -> list[tuple[str, bool, str | None]]:
    """What to change so the channel matches its chanmode (used when the bot gets ops)."""
    on, off = parse_chanmode(chanmode)
    current = set(state.modes)
    changes: list[tuple[str, bool, str | None]] = []
    for char, arg in on.items():
        if char not in current or (char == "l" and str(state.limit) != arg):
            changes.append((char, True, arg))
    for char in off & current:
        changes.append((char, False, None))  # -k with key unknown: most servers accept "*"
    return [(c, s, "*" if (c == "k" and not s and a is None) else a) for c, s, a in changes]


async def _member_flags(bot: Any, channel: str, nick: str) -> set[str] | None:
    """Flags of a channel member, or None if we don't know their user@host yet."""
    member = bot.get_nick_in_channel(channel, nick)
    if member is None or not (member.user and member.host):
        return None
    return await _identity_flags(bot, channel, member.hostmask, member.account)


async def _authorized_op(bot: Any, channel: str, nick: str) -> bool | None:
    from pybot.core.permissions import flags_allow

    flags = await _member_flags(bot, channel, nick)
    return None if flags is None else flags_allow(flags, "o")


@plugin.event("MODE")
async def _on_mode(bot: Any, trigger: Trigger) -> None:
    params = trigger.message.params
    if len(params) < 2 or params[0][:1] not in ("#", "&"):
        return
    channel = params[0]
    if trigger.nick.lower() == bot.nick.lower() or not bot.can_set_modes(channel):
        return
    changes = bot.mode_changes(params[1], params[2:])
    config = await settings(channel)

    if any(s and c in _OP_MODES and (a or "").lower() == bot.nick.lower() for c, s, a in changes):
        await sweep(bot, channel)  # we were just opped
        return

    fixes = _enforce_changes(bot, config["chanmode"], changes) if config["chanmode"] else []
    for char, setting, nick in changes:
        if char != "o" or not nick or nick.lower() == bot.nick.lower():
            continue
        if setting and enabled(config["bitch"]):
            if await _authorized_op(bot, channel, nick) is False:
                fixes.append(("o", False, nick))
        elif not setting and enabled(config["protectops"]) and nick.lower() != trigger.nick.lower():
            if await _authorized_op(bot, channel, nick) and not await _actor_may_deop(
                bot, channel, trigger
            ):
                fixes.append(("o", True, nick))
    if fixes:
        await bot.send_modes(channel, fixes)


async def _actor_may_deop(bot: Any, channel: str, trigger: Trigger) -> bool:
    """Masters, admins and owners may deop; so may servers and services."""
    if not (trigger.user and trigger.host) or trigger.nick.lower().endswith("serv"):
        return True
    flags = await _identity_flags(bot, channel, trigger.hostmask, trigger.account)
    return bool(flags & {"m", "a", "n"})


async def sweep(bot: Any, channel: str) -> None:
    """After getting ops: apply chanmode, deop unauthorized ops (bitch), set the limit."""
    state = bot.get_channel(channel)
    if state is None:
        return
    config = await settings(channel)
    changes = _missing_modes(state, config["chanmode"]) if config["chanmode"] else []
    if enabled(config["bitch"]):
        for member in list(state.nicks.values()):
            if member.nick.lower() == bot.nick.lower() or "o" not in member.modes:
                continue
            if await _authorized_op(bot, channel, member.nick) is False:
                changes.append(("o", False, member.nick))
    if changes:
        await bot.send_modes(channel, changes)
    await apply_limit(bot, channel)
