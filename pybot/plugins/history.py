"""
History plugin — catch up on what was said in a channel.

Author:  Jarsky
Version: 1.0.0
Date:    2026-10-26

Commands:
  !history [#channel] [lines]   Send you the last messages of a channel in private
                                (default and maximum: max_lines, 50 unless configured)

Uses the bot's message log (passwords are already redacted there). You must be in the
channel yourself; owners and admins can read any logged channel. One request per minute.
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-10-26",
    "description": "!history: the last messages of a channel, sent privately to catch up.",
    "url": "https://github.com/Jarsky/pyra",
}

__plugin_config__ = {
    "max_lines": {
        "type": "int",
        "default": 50,
        "min": 5,
        "max": 200,
        "help": "Most lines one request can return (also the default)",
    },
}

from datetime import datetime, timezone
from typing import Any

from pybot import plugin
from pybot.plugin import Trigger

_USAGE = "Usage: !history [#channel] [lines]"


def _max_lines(bot: Any) -> int:
    try:
        return max(5, min(200, int(bot.plugin_config("history").get("max_lines", 50))))
    except (TypeError, ValueError):
        return 50


def _format(row: Any, today: str) -> str:
    stamp = (
        row.logged_at.replace(tzinfo=timezone.utc)
        if row.logged_at.tzinfo is None
        else row.logged_at
    )
    when = (
        stamp.strftime("%H:%M")
        if stamp.strftime("%Y-%m-%d") == today
        else stamp.strftime("%b %d %H:%M")
    )
    text = row.message or ""
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        return f"[{when}] * {row.nick} {text[8:-1]}"
    return f"[{when}] <{row.nick}> {text}"


@plugin.command(
    "history",
    help="Send you the last messages of a channel in private, to catch up",
    usage="!history [#channel] [lines]",
    cooldown=60,
)
async def cmd_history(bot: Any, trigger: Trigger) -> None:
    channel = trigger.channel
    limit = _max_lines(bot)
    requested = limit
    for arg in trigger.args:
        if arg[:1] in ("#", "&"):
            channel = arg
        elif arg.isdigit() and int(arg) > 0:
            requested = min(int(arg), limit)
        else:
            await bot.notice(trigger.nick, _USAGE)
            return
    if not channel:
        await bot.notice(trigger.nick, _USAGE)
        return

    # Only people in the channel may read it (no snooping on channels you're not in)
    if not (trigger.admin or bot.get_nick_in_channel(channel, trigger.nick)):
        await bot.notice(trigger.nick, f"You need to be in {channel} to see its history.")
        return

    from sqlalchemy import func, select

    from pybot.core.database import Log, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Log)
            .where(func.lower(Log.channel) == channel.lower(), Log.event_type == "PRIVMSG")
            .order_by(Log.logged_at.desc(), Log.id.desc())
            .limit(requested + 1)
        )
        rows = list(result.scalars().all())

    # Leave out the !history request itself (it is logged before plugins run)
    if rows and rows[0].nick == trigger.nick and (rows[0].message or "") == trigger.text:
        rows = rows[1:]
    rows = list(reversed(rows[:requested]))

    if not rows:
        await bot.say(trigger.nick, f"No messages logged in {channel} yet.")
        return
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    await bot.say(trigger.nick, f"Last {len(rows)} messages in {channel} (times UTC):")
    for row in rows:
        await bot.say(trigger.nick, _format(row, today))
    await bot.say(trigger.nick, f"End of {channel} history.")
