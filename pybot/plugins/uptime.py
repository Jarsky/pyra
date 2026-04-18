"""
Uptime plugin — show bot uptime and stats.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Commands:
  !uptime    Show how long the bot has been running
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "Show how long the bot has been running in a human-readable format.",
    "url": "https://github.com/Jarsky/pyra",
}

from pybot import plugin
from pybot.plugin import Trigger


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


@plugin.command("uptime", help="Show bot uptime and connection info")
async def cmd_uptime(bot: object, trigger: Trigger) -> None:
    uptime = _format_uptime(bot.uptime_seconds)  # type: ignore[attr-defined]
    channels = len(bot.channels)  # type: ignore[attr-defined]
    server = bot.config.primary_server.host  # type: ignore[attr-defined]
    nick = bot.nick  # type: ignore[attr-defined]
    await bot.say(  # type: ignore[attr-defined]
        trigger.target,
        f"\x02{nick}\x02 — uptime: {uptime} | server: {server} | channels: {channels}",
    )


@plugin.interval(300)
async def _log_uptime(bot: object) -> None:
    from loguru import logger

    uptime = _format_uptime(bot.uptime_seconds)  # type: ignore[attr-defined]
    logger.debug(f"Bot uptime: {uptime}")
