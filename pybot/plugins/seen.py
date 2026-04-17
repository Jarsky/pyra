"""Seen plugin — track when nicks were last seen."""

from __future__ import annotations

from datetime import datetime, timezone

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command("seen", help="Show when a nick was last seen", usage="!seen <nick>")
async def cmd_seen(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !seen <nick>")  # type: ignore[attr-defined]
        return

    from sqlalchemy import select

    from pybot.core.database import SeenEntry, get_session

    nick = trigger.args[0]
    if nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        await bot.say(trigger.target, "That's me!")  # type: ignore[attr-defined]
        return
    if nick.lower() == trigger.nick.lower():
        await bot.say(trigger.target, f"{trigger.nick}: That's you!")  # type: ignore[attr-defined]
        return

    async with get_session() as session:
        result = await session.execute(
            select(SeenEntry)
            .where(SeenEntry.nick == nick)
            .order_by(SeenEntry.seen_at.desc())
            .limit(1)
        )
        entry = result.scalar_one_or_none()

    if not entry:
        await bot.say(trigger.target, f"I haven't seen {nick}.")  # type: ignore[attr-defined]
        return

    delta = datetime.now(tz=timezone.utc) - entry.seen_at.replace(tzinfo=timezone.utc)
    age = _format_age(int(delta.total_seconds()))

    if entry.action == "said":
        msg = f"{nick} was last seen in {entry.channel} {age} ago saying: {entry.message}"
    elif entry.action == "joined":
        msg = f"{nick} was last seen joining {entry.channel} {age} ago."
    elif entry.action == "parted":
        msg = f"{nick} was last seen leaving {entry.channel} {age} ago."
    elif entry.action == "quit":
        msg = f"{nick} was last seen quitting {age} ago."
    else:
        msg = f"{nick} was last seen {age} ago ({entry.action})."

    await bot.say(trigger.target, msg)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Passive listeners — update SeenEntry on IRC events
# ---------------------------------------------------------------------------


@plugin.event("PRIVMSG")
async def _on_privmsg(bot: object, trigger: Trigger) -> None:
    if not trigger.channel:
        return
    await _update_seen(trigger.nick, trigger.channel, "said", trigger.text)


@plugin.event("JOIN")
async def _on_join(bot: object, trigger: Trigger) -> None:
    if trigger.nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        return
    channel = trigger.message.params[0] if trigger.message.params else ""
    if channel:
        await _update_seen(trigger.nick, channel, "joined")


@plugin.event("PART")
async def _on_part(bot: object, trigger: Trigger) -> None:
    if trigger.nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        return
    channel = trigger.message.params[0] if trigger.message.params else ""
    if channel:
        await _update_seen(trigger.nick, channel, "parted", trigger.message.text)


@plugin.event("QUIT")
async def _on_quit(bot: object, trigger: Trigger) -> None:
    await _update_seen(trigger.nick, "*", "quit", trigger.message.text)


@plugin.event("NICK")
async def _on_nick(bot: object, trigger: Trigger) -> None:
    await _update_seen(trigger.nick, "*", "quit", f"Changed nick to {trigger.message.text}")


async def _update_seen(
    nick: str, channel: str, action: str, message: str | None = None
) -> None:
    from pybot.core.database import SeenEntry, get_session

    async with get_session() as session:
        session.add(
            SeenEntry(
                nick=nick,
                channel=channel,
                action=action,
                message=message[:512] if message else None,
                seen_at=datetime.now(tz=timezone.utc),
            )
        )


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h"
