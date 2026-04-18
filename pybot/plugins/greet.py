"""
Greet plugin — welcome new users on join with a configurable message.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Passive: fires on JOIN events — no commands.
Configure the greeting via ChannelSetting key "greet_message".
Supports {nick} and {channel} placeholders. 5-minute cooldown per nick.
"""

from __future__ import annotations

import time

from pybot import plugin
from pybot.plugin import Trigger

# Track recent greets to prevent duplicate welcomes: nick!channel -> timestamp
_greeted: dict[str, float] = {}
_GREET_COOLDOWN = 300  # 5 minutes


@plugin.event("JOIN")
async def _on_join(bot: object, trigger: Trigger) -> None:
    # Don't greet ourselves
    if trigger.nick.lower() == bot.nick.lower():  # type: ignore[attr-defined]
        return

    channel = trigger.message.params[0] if trigger.message.params else ""
    if not channel:
        return

    from pybot.core.database import get_channel_setting, get_session

    async with get_session() as session:
        greet_on = await get_channel_setting(session, channel, "greet", "false")
        if greet_on.lower() not in ("true", "1", "yes", "on"):
            return
        msg_template = await get_channel_setting(
            session, channel, "greet_msg", "Welcome to {channel}, {nick}!"
        )

    # Cooldown check (avoid repeat greets on rejoin)
    key = f"{trigger.nick.lower()}!{channel.lower()}"
    last = _greeted.get(key, 0)
    if time.monotonic() - last < _GREET_COOLDOWN:
        return
    _greeted[key] = time.monotonic()

    message = msg_template.format(
        nick=trigger.nick,
        channel=channel,
        network=bot.config.primary_server.host,  # type: ignore[attr-defined]
    )
    await bot.say(channel, message)  # type: ignore[attr-defined]


@plugin.command(
    "greet",
    privilege="o",
    help="Configure channel greeting",
    usage="!greet set <message> | !greet on | !greet off",
)
async def cmd_greet(bot: object, trigger: Trigger) -> None:
    if not trigger.channel:
        await bot.reply(trigger, "This command must be used in a channel.")  # type: ignore[attr-defined]
        return

    if not trigger.args:
        await bot.reply(trigger, "Usage: !greet set <message> | !greet on | !greet off")  # type: ignore[attr-defined]
        return

    from pybot.core.database import get_session, set_channel_setting

    sub = trigger.args[0].lower()

    if sub == "set":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !greet set <message> (use {nick} and {channel})")  # type: ignore[attr-defined]
            return
        msg = " ".join(trigger.args[1:])
        async with get_session() as session:
            await set_channel_setting(session, trigger.channel, "greet_msg", msg)
            await set_channel_setting(session, trigger.channel, "greet", "true")
        await bot.reply(trigger, f"Greet message set: {msg}")  # type: ignore[attr-defined]

    elif sub == "on":
        async with get_session() as session:
            await set_channel_setting(session, trigger.channel, "greet", "true")
        await bot.reply(trigger, "Greet enabled.")  # type: ignore[attr-defined]

    elif sub == "off":
        async with get_session() as session:
            await set_channel_setting(session, trigger.channel, "greet", "false")
        await bot.reply(trigger, "Greet disabled.")  # type: ignore[attr-defined]

    else:
        await bot.reply(trigger, "Usage: !greet set <message> | !greet on | !greet off")  # type: ignore[attr-defined]
