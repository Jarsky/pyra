"""Channel admin commands — op/deop/voice/kick/ban/topic etc."""

from __future__ import annotations

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command("op", privilege="o", help="Op a nick", usage="!op [nick]")
async def cmd_op(bot: object, trigger: Trigger) -> None:
    nick = trigger.args[0] if trigger.args else trigger.nick
    channel = trigger.channel
    if not channel:
        return
    await bot.op(channel, nick)  # type: ignore[attr-defined]


@plugin.command("deop", privilege="o", help="Deop a nick", usage="!deop [nick]")
async def cmd_deop(bot: object, trigger: Trigger) -> None:
    nick = trigger.args[0] if trigger.args else trigger.nick
    channel = trigger.channel
    if not channel:
        return
    await bot.deop(channel, nick)  # type: ignore[attr-defined]


@plugin.command("voice", privilege="o", help="Voice a nick", usage="!voice [nick]")
async def cmd_voice(bot: object, trigger: Trigger) -> None:
    nick = trigger.args[0] if trigger.args else trigger.nick
    channel = trigger.channel
    if not channel:
        return
    await bot.voice(channel, nick)  # type: ignore[attr-defined]


@plugin.command("devoice", privilege="o", help="Devoice a nick", usage="!devoice [nick]")
async def cmd_devoice(bot: object, trigger: Trigger) -> None:
    nick = trigger.args[0] if trigger.args else trigger.nick
    channel = trigger.channel
    if not channel:
        return
    await bot.devoice(channel, nick)  # type: ignore[attr-defined]


@plugin.command(
    "kick",
    privilege="o",
    help="Kick a nick from the channel",
    usage="!kick <nick> [reason]",
)
async def cmd_kick(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !kick <nick> [reason]")  # type: ignore[attr-defined]
        return
    channel = trigger.channel
    if not channel:
        return
    nick = trigger.args[0]
    reason = " ".join(trigger.args[1:]) if len(trigger.args) > 1 else "Requested"
    await bot.kick(channel, nick, reason)  # type: ignore[attr-defined]


@plugin.command(
    "ban",
    privilege="o",
    help="Ban a nick or hostmask",
    usage="!ban <nick|mask> [reason]",
)
async def cmd_ban(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !ban <nick|mask> [reason]")  # type: ignore[attr-defined]
        return
    channel = trigger.channel
    if not channel:
        return

    target = trigger.args[0]
    reason = " ".join(trigger.args[1:]) if len(trigger.args) > 1 else "Banned"
    mask = await _resolve_ban_mask(bot, channel, target)

    await bot.ban(channel, mask)  # type: ignore[attr-defined]
    await _store_ban(bot, trigger, channel, mask, reason)
    await bot.reply(trigger, f"Banned {mask}.")  # type: ignore[attr-defined]


@plugin.command(
    "unban",
    privilege="o",
    help="Remove a ban",
    usage="!unban <nick|mask>",
)
async def cmd_unban(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !unban <nick|mask>")  # type: ignore[attr-defined]
        return
    channel = trigger.channel
    if not channel:
        return

    target = trigger.args[0]
    mask = await _resolve_ban_mask(bot, channel, target)
    await bot.unban(channel, mask)  # type: ignore[attr-defined]

    from sqlalchemy import select

    from pybot.core.database import Ban, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Ban).where(Ban.hostmask == mask, Ban.active == True)  # noqa: E712
        )
        for ban in result.scalars().all():
            ban.active = False

    await bot.reply(trigger, f"Unbanned {mask}.")  # type: ignore[attr-defined]


@plugin.command(
    "kickban",
    aliases=["kb"],
    privilege="o",
    help="Kick and ban a nick",
    usage="!kb <nick> [reason]",
)
async def cmd_kickban(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !kb <nick> [reason]")  # type: ignore[attr-defined]
        return
    channel = trigger.channel
    if not channel:
        return
    nick = trigger.args[0]
    reason = " ".join(trigger.args[1:]) if len(trigger.args) > 1 else "Kickbanned"
    mask = await _resolve_ban_mask(bot, channel, nick)
    await bot.ban(channel, mask)  # type: ignore[attr-defined]
    await bot.kick(channel, nick, reason)  # type: ignore[attr-defined]
    await _store_ban(bot, trigger, channel, mask, reason)


@plugin.command(
    "tempban",
    privilege="o",
    help="Temporarily ban a nick",
    usage="!tempban <nick> <duration> [reason]",
)
async def cmd_tempban(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !tempban <nick> <duration> [reason]  (e.g. 10m, 2h)")  # type: ignore[attr-defined]
        return
    channel = trigger.channel
    if not channel:
        return

    nick = trigger.args[0]
    try:
        from pybot.plugins.admin import _parse_duration

        expires = _parse_duration(trigger.args[1])
    except ValueError as exc:
        await bot.reply(trigger, str(exc))  # type: ignore[attr-defined]
        return

    reason = " ".join(trigger.args[2:]) if len(trigger.args) > 2 else "Temp ban"
    mask = await _resolve_ban_mask(bot, channel, nick)
    await bot.ban(channel, mask)  # type: ignore[attr-defined]
    await bot.kick(channel, nick, f"{reason} (temp ban)")  # type: ignore[attr-defined]
    await _store_ban(bot, trigger, channel, mask, reason, expires_at=expires)

    import asyncio
    from datetime import datetime, timezone

    delay = (expires - datetime.now(tz=timezone.utc)).total_seconds()
    asyncio.create_task(_auto_unban(bot, channel, mask, delay))
    await bot.reply(trigger, f"Temp-banned {mask} until {expires.strftime('%H:%M UTC')}.")  # type: ignore[attr-defined]


async def _auto_unban(bot: object, channel: str, mask: str, delay: float) -> None:
    import asyncio

    await asyncio.sleep(max(delay, 0))
    await bot.unban(channel, mask)  # type: ignore[attr-defined]

    from sqlalchemy import select

    from pybot.core.database import Ban, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Ban).where(Ban.hostmask == mask, Ban.active == True)  # noqa: E712
        )
        for ban in result.scalars().all():
            ban.active = False


@plugin.command(
    "quiet",
    privilege="o",
    help="Mute a nick (+q or +m override)",
    usage="!quiet <nick>",
)
async def cmd_quiet(bot: object, trigger: Trigger) -> None:
    if not trigger.args or not trigger.channel:
        return
    nick = trigger.args[0]
    mask = await _resolve_ban_mask(bot, trigger.channel, nick)
    await bot.mode(trigger.channel, "+q", mask)  # type: ignore[attr-defined]


@plugin.command("unquiet", privilege="o", help="Unmute a nick", usage="!unquiet <nick>")
async def cmd_unquiet(bot: object, trigger: Trigger) -> None:
    if not trigger.args or not trigger.channel:
        return
    nick = trigger.args[0]
    mask = await _resolve_ban_mask(bot, trigger.channel, nick)
    await bot.mode(trigger.channel, "-q", mask)  # type: ignore[attr-defined]


@plugin.command(
    "topic",
    privilege="o",
    help="Set the channel topic",
    usage="!topic <text>",
)
async def cmd_topic(bot: object, trigger: Trigger) -> None:
    if not trigger.args or not trigger.channel:
        return
    text = " ".join(trigger.args)
    await bot.topic(trigger.channel, text)  # type: ignore[attr-defined]


@plugin.command(
    "mode",
    privilege="o",
    help="Set a channel mode",
    usage="!mode <modestring> [args...]",
)
async def cmd_mode(bot: object, trigger: Trigger) -> None:
    if not trigger.args or not trigger.channel:
        return
    await bot.mode(trigger.channel, *trigger.args)  # type: ignore[attr-defined]


@plugin.command(
    "invite",
    privilege="o",
    help="Invite a nick to a channel",
    usage="!invite <nick> [#channel]",
)
async def cmd_invite(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !invite <nick> [#channel]")  # type: ignore[attr-defined]
        return
    nick = trigger.args[0]
    channel = trigger.args[1] if len(trigger.args) > 1 else trigger.channel
    if not channel:
        return
    await bot.raw(f"INVITE {nick} {channel}")  # type: ignore[attr-defined]


@plugin.command("bans", privilege="o", help="List active bans in this channel")
async def cmd_bans(bot: object, trigger: Trigger) -> None:
    if not trigger.channel:
        return

    from sqlalchemy import select

    from pybot.core.database import Ban, Channel, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Ban)
            .join(Channel)
            .where(
                Channel.name == trigger.channel.lower(),
                Ban.active == True,  # noqa: E712
            )
        )
        bans = result.scalars().all()

    if not bans:
        await bot.reply(trigger, "No active bans in this channel.")  # type: ignore[attr-defined]
        return

    await bot.notice(trigger.nick, f"Active bans in {trigger.channel}:")  # type: ignore[attr-defined]
    for ban in bans:
        line = f"  {ban.hostmask} — by {ban.set_by}"
        if ban.reason:
            line += f": {ban.reason}"
        if ban.expires_at:
            line += f" (expires {ban.expires_at.strftime('%Y-%m-%d %H:%M UTC')})"
        await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]


@plugin.command(
    "chanset",
    privilege="o",
    help="Set a per-channel bot setting",
    usage="!chanset <key> <value>",
)
async def cmd_chanset(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2 or not trigger.channel:
        await bot.reply(trigger, "Usage: !chanset <key> <value>")  # type: ignore[attr-defined]
        return
    from pybot.core.database import get_session, set_channel_setting

    key = trigger.args[0]
    value = " ".join(trigger.args[1:])
    async with get_session() as session:
        await set_channel_setting(session, trigger.channel, key, value)
    await bot.reply(trigger, f"Set {key} = {value} for {trigger.channel}")  # type: ignore[attr-defined]


@plugin.command(
    "changet",
    privilege="o",
    help="Get a per-channel bot setting",
    usage="!changet <key>",
)
async def cmd_changet(bot: object, trigger: Trigger) -> None:
    if not trigger.args or not trigger.channel:
        await bot.reply(trigger, "Usage: !changet <key>")  # type: ignore[attr-defined]
        return
    from pybot.core.database import get_channel_setting, get_session

    key = trigger.args[0]
    async with get_session() as session:
        value = await get_channel_setting(session, trigger.channel, key)
    if value:
        await bot.reply(trigger, f"{key} = {value}")  # type: ignore[attr-defined]
    else:
        await bot.reply(trigger, f"No setting '{key}' found for this channel.")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_ban_mask(bot: object, channel: str, target: str) -> str:
    """Convert a nick to a *!user@host ban mask, or return as-is if it looks like a mask."""
    if any(c in target for c in ("*", "!", "@")):
        return target
    ch = bot.get_channel(channel)  # type: ignore[attr-defined]
    if ch:
        ns = ch.get_nick(target)
        if ns and ns.user and ns.host:
            return f"*!{ns.user}@{ns.host}"
    return f"*!*@{target}"


async def _store_ban(
    bot: object,
    trigger: Trigger,
    channel: str,
    mask: str,
    reason: str,
    expires_at: object = None,
) -> None:
    from datetime import datetime, timezone

    from pybot.core.database import Ban, get_or_create_channel, get_session

    async with get_session() as session:
        ch = await get_or_create_channel(session, channel)
        session.add(
            Ban(
                channel_id=ch.id,
                hostmask=mask,
                reason=reason,
                set_by=trigger.hostmask,
                set_at=datetime.now(tz=timezone.utc),
                expires_at=expires_at,
                active=True,
            )
        )
