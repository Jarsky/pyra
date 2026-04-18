"""
TimeBot plugin — User timezone and current time lookup.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Database-backed per-user timezone storage.

Commands:
  !time                   Show your current time (uses saved timezone)
  !time set <timezone>    Save your timezone (e.g. Pacific/Auckland)
  !time <nick>            Show another user's current time
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "Per-user timezone storage and time lookup. Supports any IANA timezone.",
    "url": "https://github.com/Jarsky/pyra",
}

from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

from pybot import plugin
from pybot.plugin import Trigger


def _is_valid_timezone(tz_name: str) -> bool:
    """Check if timezone name is valid."""
    return tz_name in available_timezones()


async def _get_user_time(timezone_name: str) -> str | None:
    """Get current time in a specific timezone."""
    try:
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        return now.strftime("%I:%M %p %Z")
    except Exception:
        return None


@plugin.command(
    "time",
    help="Check or set your timezone",
    usage="!time [set <timezone>] or !time",
)
async def cmd_time(bot: object, trigger: Trigger) -> None:
    from pybot.core.database import get_plugin_setting, get_session, set_plugin_setting

    if not trigger.args:
        # Show user's current time
        async with get_session() as session:
            stored_tz = await get_plugin_setting(
                session, "timebot", "timezone", channel=trigger.nick  # type: ignore[arg-type]
            )

        if not stored_tz:
            await bot.reply(  # type: ignore[attr-defined]
                trigger, "Timezone not set. Use: !time set <timezone>"
            )
            return

        current_time = await _get_user_time(stored_tz)
        if not current_time:
            await bot.reply(trigger, f"Invalid timezone: {stored_tz}")  # type: ignore[attr-defined]
            return

        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0307{trigger.nick}'s time:\x03 {current_time} ({stored_tz})"
        )
        return

    # Handle subcommands
    if trigger.args[0].lower() == "set":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !time set <timezone>")  # type: ignore[attr-defined]
            return

        timezone = trigger.args[1]
        if not _is_valid_timezone(timezone):
            # Try to find close match
            matches = [tz for tz in available_timezones() if timezone.lower() in tz.lower()]
            if matches:
                await bot.say(
                    trigger.target,
                    f"\x0304Invalid timezone. Did you mean: {', '.join(matches[:5])}?",
                )  # type: ignore[attr-defined]
            else:
                await bot.reply(  # type: ignore[attr-defined]
                    trigger, f"Invalid timezone: {timezone}. Use 'UTC', 'America/New_York', etc."
                )
            return

        async with get_session() as session:
            await set_plugin_setting(
                session, "timebot", "timezone", timezone, channel=trigger.nick  # type: ignore[arg-type]
            )

        await bot.reply(trigger, f"Timezone set to: {timezone}")  # type: ignore[attr-defined]
        return

    # Show another user's time (if they have set timezone)
    target_nick = trigger.args[0]
    async with get_session() as session:
        stored_tz = await get_plugin_setting(
            session, "timebot", "timezone", channel=target_nick  # type: ignore[arg-type]
        )

    if not stored_tz:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304{target_nick} has not set a timezone"
        )
        return

    current_time = await _get_user_time(stored_tz)
    if not current_time:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error getting time for {target_nick}"
        )
        return

    await bot.say(  # type: ignore[attr-defined]
        trigger.target, f"\x0307{target_nick}'s time:\x03 {current_time} ({stored_tz})"
    )
