"""Tell plugin — leave a message for a nick to receive when they next speak."""

from __future__ import annotations

from datetime import datetime, timezone

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command(
    "tell",
    help="Leave a message for someone",
    usage="!tell <nick> <message>",
)
async def cmd_tell(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !tell <nick> <message>")  # type: ignore[attr-defined]
        return

    to_nick = trigger.args[0]
    message = " ".join(trigger.args[1:])
    channel = trigger.channel or trigger.nick

    if to_nick.lower() == trigger.nick.lower():
        await bot.reply(trigger, "You can't send a tell to yourself.")  # type: ignore[attr-defined]
        return
    if to_nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        await bot.reply(trigger, "You can't send a tell to me!")  # type: ignore[attr-defined]
        return

    from pybot.core.database import Tell, get_session

    async with get_session() as session:
        session.add(
            Tell(
                from_nick=trigger.nick,
                to_nick=to_nick,
                channel=channel,
                message=message[:512],
                created_at=datetime.now(tz=timezone.utc),
            )
        )

    await bot.reply(trigger, f"I'll tell {to_nick} that when I see them.")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Deliver pending tells when the recipient speaks or joins
# ---------------------------------------------------------------------------


@plugin.event("PRIVMSG")
async def _check_tells_on_speak(bot: object, trigger: Trigger) -> None:
    await _deliver_tells(bot, trigger.nick, trigger.channel or trigger.nick)


@plugin.event("JOIN")
async def _check_tells_on_join(bot: object, trigger: Trigger) -> None:
    if trigger.nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        return
    channel = trigger.message.params[0] if trigger.message.params else ""
    await _deliver_tells(bot, trigger.nick, channel)


async def _deliver_tells(bot: object, nick: str, channel: str) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from pybot.core.database import Tell, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Tell).where(
                Tell.to_nick == nick, Tell.delivered == False  # noqa: E712
            )
        )
        tells = result.scalars().all()

        if not tells:
            return

        now = datetime.now(tz=timezone.utc)
        for tell in tells:

            try:
                from pybot.plugins.seen import _format_age as fmt_age
            except ImportError:
                fmt_age = lambda s: f"{s}s"  # noqa: E731

            age = fmt_age(int((now - tell.created_at.replace(tzinfo=timezone.utc)).total_seconds()))
            await bot.say(  # type: ignore[attr-defined]
                channel,
                f"{nick}: [{tell.from_nick} {age} ago]: {tell.message}",
            )
            tell.delivered = True
            tell.delivered_at = now
