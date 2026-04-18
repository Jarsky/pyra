"""
Reminder plugin — set timed reminders that fire in channel or PM.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18


Commands:
  !remindme <duration> <message>         Remind yourself in this channel
  !remind <nick> <duration> <message>    Remind another user (op+)
  !reminders                             List your pending reminders
  !delremind <id>                        Cancel a reminder

Duration: 5m, 2h, 1h30m, 2d, 30s
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pybot import plugin
from pybot.plugin import Trigger

_DUR_RE = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def _parse_duration(s: str) -> int | None:
    m = _DUR_RE.match(s.strip())
    if not m or not any(m.groups()):
        return None
    d = int(m.group(1) or 0)
    h = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    secs = int(m.group(4) or 0)
    total = d * 86400 + h * 3600 + mins * 60 + secs
    return total if total > 0 else None


def _human_duration(secs: int) -> str:
    parts = []
    if secs >= 86400:
        parts.append(f"{secs // 86400}d")
        secs %= 86400
    if secs >= 3600:
        parts.append(f"{secs // 3600}h")
        secs %= 3600
    if secs >= 60:
        parts.append(f"{secs // 60}m")
        secs %= 60
    if secs:
        parts.append(f"{secs}s")
    return "".join(parts) or "0s"


@plugin.command(
    "remindme",
    help="Set a reminder for yourself",
    usage="!remindme <duration> <message>  (e.g. !remindme 30m check the oven)",
)
async def cmd_remindme(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !remindme <duration> <message>")  # type: ignore[attr-defined]
        return
    await _set_reminder(bot, trigger, trigger.nick, trigger.args[0], " ".join(trigger.args[1:]),
                        trigger.channel or trigger.nick)


@plugin.command(
    "remind",
    help="Set a reminder for another user (op+)",
    usage="!remind <nick> <duration> <message>",
    privilege="o",
)
async def cmd_remind(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 3:
        await bot.reply(trigger, "Usage: !remind <nick> <duration> <message>")  # type: ignore[attr-defined]
        return
    await _set_reminder(bot, trigger, trigger.args[0], trigger.args[1],
                        " ".join(trigger.args[2:]), trigger.channel or trigger.nick)


async def _set_reminder(
    bot: object, trigger: Trigger, nick: str, dur_str: str, message: str, deliver_to: str
) -> None:
    from pybot.core.database import Reminder, get_or_create_user_by_nick, get_session

    secs = _parse_duration(dur_str)
    if not secs:
        await bot.reply(trigger, f"Invalid duration '{dur_str}'. Examples: 30m, 2h, 1d, 1h30m")  # type: ignore[attr-defined]
        return

    fire_at = datetime.now(timezone.utc) + timedelta(seconds=secs)

    async with get_session() as session:
        user = await get_or_create_user_by_nick(session, nick, trigger.hostmask)
        reminder = Reminder(
            user_id=user.id,
            nick=nick,
            channel=deliver_to,
            message=message,
            fire_at=fire_at,
        )
        session.add(reminder)
        await session.flush()
        rid = reminder.id

    dur_human = _human_duration(secs)
    if nick == trigger.nick:
        await bot.reply(trigger, f"I'll remind you in {dur_human} (#{rid})")  # type: ignore[attr-defined]
    else:
        await bot.reply(trigger, f"Reminder set for {nick} in {dur_human} (#{rid})")  # type: ignore[attr-defined]


@plugin.command("reminders", help="List your pending reminders")
async def cmd_reminders(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Reminder, get_session

    now = datetime.now(timezone.utc)
    async with get_session() as session:
        rows = (await session.execute(
            select(Reminder).where(Reminder.nick == trigger.nick, Reminder.fired == False)  # noqa: E712
            .order_by(Reminder.fire_at)
        )).scalars().all()

    if not rows:
        await bot.reply(trigger, "No pending reminders.")  # type: ignore[attr-defined]
        return
    await bot.reply(trigger, f"{len(rows)} pending reminder(s):")  # type: ignore[attr-defined]
    for r in rows[:5]:
        fire_at = r.fire_at
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        remaining = max(0, int((fire_at - now).total_seconds()))
        line = f"  #{r.id}: in {_human_duration(remaining)} — {r.message[:60]}"
        await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]


@plugin.command("delremind", help="Cancel a pending reminder", usage="!delremind <id>")
async def cmd_delremind(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Reminder, get_session

    if not trigger.args:
        await bot.reply(trigger, "Usage: !delremind <id>")  # type: ignore[attr-defined]
        return
    try:
        rid = int(trigger.args[0])
    except ValueError:
        await bot.reply(trigger, "Reminder ID must be a number.")  # type: ignore[attr-defined]
        return

    async with get_session() as session:
        row = (await session.execute(
            select(Reminder).where(Reminder.id == rid, Reminder.nick == trigger.nick)
        )).scalar_one_or_none()
        if not row:
            await bot.reply(trigger, f"No reminder #{rid} found (or it's not yours).")  # type: ignore[attr-defined]
            return
        row.fired = True

    await bot.reply(trigger, f"Reminder #{rid} cancelled.")  # type: ignore[attr-defined]


@plugin.interval(30)
async def _check_reminders(bot: object) -> None:
    """Fire due reminders every 30 seconds."""
    from sqlalchemy import select

    from pybot.core.database import Reminder, get_session

    now = datetime.now(timezone.utc)
    async with get_session() as session:
        due = (await session.execute(
            select(Reminder).where(Reminder.fired == False, Reminder.fire_at <= now)  # noqa: E712
        )).scalars().all()
        for r in due:
            r.fired = True

    for r in due:
        target = r.channel if r.channel else r.nick
        await bot.say(target, f"{r.nick}: Reminder — {r.message}")  # type: ignore[attr-defined]
