"""
Antispam + flood protection plugin.

Tracks per-nick/channel message rates, CAPS ratio, and repeat detection.
Action escalation: warn → kick → tempban.
Users with the X (exempt) flag bypass all checks.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Deque

from pybot import plugin
from pybot.plugin import Trigger

# Per-nick-channel: deque of message timestamps
_rate_window: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=50))
# Per-nick-channel: repeat detection (message -> count)
_repeat_tracker: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
# Per-nick: escalation level (0=warn, 1=kick, 2=tempban)
_escalation: dict[str, int] = defaultdict(int)
# Nick-channel -> last warning time
_last_warn: dict[str, float] = {}

_DEFAULT_RATE = 5   # messages per window
_DEFAULT_WINDOW = 3  # seconds
_DEFAULT_CAPS_PCT = 75
_DEFAULT_REPEAT = 3


@plugin.event("PRIVMSG")
async def _on_privmsg(bot: object, trigger: Trigger) -> None:
    if not trigger.channel:
        return

    # Skip if user is exempt
    if await trigger.has_flag("X", trigger.channel):
        return

    # Skip if user is a channel op
    ch_state = bot.get_channel(trigger.channel)  # type: ignore[attr-defined]
    if ch_state:
        ns = ch_state.get_nick(trigger.nick)
        if ns and "o" in ns.modes:
            return

    # Load channel-specific thresholds
    from pybot.core.database import get_channel_setting, get_session

    async with get_session() as session:
        rate = int(
            await get_channel_setting(
                session, trigger.channel, "flood_lines", str(_DEFAULT_RATE)
            )
        )
        window = int(
            await get_channel_setting(
                session, trigger.channel, "flood_seconds", str(_DEFAULT_WINDOW)
            )
        )
        action = await get_channel_setting(session, trigger.channel, "flood_action", "kick")
        antispam_on = await get_channel_setting(session, trigger.channel, "antispam", "true")
        caps_pct = int(
            await get_channel_setting(
                session, trigger.channel, "caps_pct", str(_DEFAULT_CAPS_PCT)
            )
        )
        repeat_limit = int(
            await get_channel_setting(
                session, trigger.channel, "repeat_count", str(_DEFAULT_REPEAT)
            )
        )

    if antispam_on.lower() not in ("true", "1", "yes", "on"):
        return

    key = f"{trigger.nick}!{trigger.channel}"
    now = asyncio.get_event_loop().time()

    # Rate check
    q = _rate_window[key]
    q.append(now)
    recent = [t for t in q if now - t <= window]
    if len(recent) > rate:
        await _take_action(bot, trigger, action, "Flood detected")
        return

    text = trigger.text

    # CAPS check (only on longer messages)
    if len(text) > 10:
        caps = sum(1 for c in text if c.isupper())
        total_alpha = sum(1 for c in text if c.isalpha())
        if total_alpha > 5 and (caps / total_alpha * 100) >= caps_pct:
            await _take_action(bot, trigger, action, "Excessive CAPS")
            return

    # Repeat check
    repeat_key = f"{trigger.nick}!{trigger.channel}"
    msg_lower = text.lower().strip()
    _repeat_tracker[repeat_key][msg_lower] += 1
    if _repeat_tracker[repeat_key][msg_lower] >= repeat_limit:
        _repeat_tracker[repeat_key].clear()
        await _take_action(bot, trigger, action, "Repeated message")
        return

    # Clear repeat counters after 30s of no matches (approximate)
    asyncio.create_task(_clear_repeat_after(repeat_key, msg_lower))


async def _clear_repeat_after(key: str, msg: str) -> None:
    await asyncio.sleep(30)
    if key in _repeat_tracker and msg in _repeat_tracker[key]:
        del _repeat_tracker[key][msg]


async def _take_action(bot: object, trigger: Trigger, action: str, reason: str) -> None:
    nick = trigger.nick
    channel = trigger.channel
    if not channel:
        return

    level = _escalation.get(f"{nick}!{channel}", 0)

    if level == 0 or action == "none":
        # Warn
        await bot.say(channel, f"{nick}: {reason} — please stop.")  # type: ignore[attr-defined]
        _escalation[f"{nick}!{channel}"] = 1
        # Reset escalation after 60s
        asyncio.create_task(_reset_escalation(nick, channel))
    elif level == 1 or action == "kick":
        await bot.kick(channel, nick, f"Spam/flood: {reason}")  # type: ignore[attr-defined]
        _escalation[f"{nick}!{channel}"] = 2
        asyncio.create_task(_reset_escalation(nick, channel))
    else:
        # tempban: 5 minutes
        from datetime import timedelta

        from pybot.core.database import Ban, get_or_create_channel, get_session

        hostmask = f"*!*@{trigger.host}" if trigger.host else f"*!{trigger.user}@*"
        await bot.ban(channel, hostmask)  # type: ignore[attr-defined]
        await bot.kick(channel, nick, f"Spam/flood: {reason} (temp ban)")  # type: ignore[attr-defined]
        _escalation.pop(f"{nick}!{channel}", None)

        # Store in DB + auto-unban after 5 min
        async with get_session() as session:
            ch = await get_or_create_channel(session, channel)
            from datetime import datetime, timezone

            expires = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
            session.add(
                Ban(
                    channel_id=ch.id,
                    hostmask=hostmask,
                    reason=f"Spam/flood: {reason}",
                    set_by=bot.nick,  # type: ignore[attr-defined]
                    set_at=datetime.now(tz=timezone.utc),
                    expires_at=expires,
                    active=True,
                )
            )
        asyncio.create_task(_auto_unban(bot, channel, hostmask, 300))


async def _reset_escalation(nick: str, channel: str) -> None:
    await asyncio.sleep(60)
    _escalation.pop(f"{nick}!{channel}", None)


async def _auto_unban(bot: object, channel: str, hostmask: str, delay: float) -> None:
    await asyncio.sleep(delay)
    await bot.unban(channel, hostmask)  # type: ignore[attr-defined]

    from sqlalchemy import select

    from pybot.core.database import Ban, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Ban).where(Ban.hostmask == hostmask, Ban.active == True)  # noqa: E712
        )
        for ban in result.scalars().all():
            ban.active = False


# ---------------------------------------------------------------------------
# JOIN flood detection
# ---------------------------------------------------------------------------

_join_tracker: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))


@plugin.event("JOIN")
async def _on_join(bot: object, trigger: Trigger) -> None:
    if trigger.nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        return
    channel = trigger.message.params[0] if trigger.message.params else ""
    if not channel:
        return

    now = asyncio.get_event_loop().time()
    key = f"{trigger.host}!{channel}"
    q = _join_tracker[key]
    q.append(now)
    recent = [t for t in q if now - t <= 10]  # 10s window
    if len(recent) >= 4:
        # Join flood from this host
        hostmask = f"*!*@{trigger.host}"
        await bot.ban(channel, hostmask)  # type: ignore[attr-defined]
        asyncio.create_task(_auto_unban(bot, channel, hostmask, 180))
