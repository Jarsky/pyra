"""
Choose plugin — random choice from a list, and Magic 8-Ball.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Commands:
  !choose <a>|<b>|<c>    Pick a random option from pipe-separated list
  !8ball <question>       Ask the Magic 8-Ball a yes/no question
"""

from __future__ import annotations

import random

from pybot import plugin
from pybot.plugin import Trigger

_8BALL_RESPONSES = [
    # Positive
    "It is certain.",
    "It is decidedly so.",
    "Without a doubt.",
    "Yes, definitely.",
    "You may rely on it.",
    "As I see it, yes.",
    "Most likely.",
    "Outlook good.",
    "Yes.",
    "Signs point to yes.",
    # Neutral
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    # Negative
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
]


@plugin.command(
    "choose",
    aliases=["pick"],
    help="Choose randomly from a list",
    usage="!choose <a> | <b> | <c>",
)
async def cmd_choose(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !choose <a> | <b> | <c>")  # type: ignore[attr-defined]
        return

    text = " ".join(trigger.args)
    # Support both | and , as separators
    if "|" in text:
        choices = [c.strip() for c in text.split("|") if c.strip()]
    else:
        choices = [c.strip() for c in text.split(",") if c.strip()]

    if len(choices) < 2:
        await bot.reply(trigger, "Please provide at least 2 choices separated by | or ,")  # type: ignore[attr-defined]
        return

    choice = random.choice(choices)  # noqa: S311
    await bot.say(trigger.target, f"{trigger.nick}: I choose: \x02{choice}\x02")  # type: ignore[attr-defined]


@plugin.command(
    "8ball",
    help="Ask the Magic 8-Ball a question",
    usage="!8ball <question>",
)
async def cmd_8ball(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Ask me a yes/no question!")  # type: ignore[attr-defined]
        return
    response = random.choice(_8BALL_RESPONSES)  # noqa: S311
    await bot.say(trigger.target, f"\U0001f3b1 {trigger.nick}: {response}")  # type: ignore[attr-defined]
