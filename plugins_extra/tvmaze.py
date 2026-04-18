"""
TVMaze plugin — TV show information via TVMaze API (no key required).

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Provides commands to look up TV shows and episode information.

Commands:
  !tv <show>     Show show details with last and next episode
  !next <show>   Show next upcoming episode info
  !last <show>   Show most recently aired episode info
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_TVMAZE_API = "http://api.tvmaze.com/singlesearch/shows"


def _format_episode(season: int, number: int) -> str:
    """Format season and episode as S##E## (e.g., S01E05)."""
    return f"S{season:02d}E{number:02d}"


async def _lookup_show(show_name: str, timeout: float = 8.0) -> dict[str, object] | None:
    """Fetch show data from TVMaze API."""
    try:
        params = {
            "q": show_name,
            "embed[]": ["nextepisode", "previousepisode"],
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_TVMAZE_API, params=params)
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception:
        return None


@plugin.command(
    "tv",
    aliases=["t"],
    help="Look up TV show info with next/last episode",
    usage="!tv <show name>",
)
async def cmd_tv(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !tv <show name>")  # type: ignore[attr-defined]
        return

    show_name = " ".join(trigger.args)

    # Clean up scene format: "Show.Name.S01E01.720p.HDTV" → "Show Name"
    show_name_clean = show_name
    import re

    show_name_clean = re.sub(r"\.|\s+", " ", show_name_clean)
    show_name_clean = re.sub(r"s\d{2}e\d{2}.*", "", show_name_clean, flags=re.IGNORECASE).strip()

    data = await _lookup_show(show_name_clean)
    if not data or "name" not in data:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error: Show '{show_name}' not found"
        )
        return

    name = data.get("name", "Unknown")
    status = data.get("status", "Unknown")
    genres = ", ".join(data.get("genres", [])) or "Unknown"

    # Get network/channel
    if data.get("webChannel"):
        network = data["webChannel"].get("name", "Unknown")
    elif data.get("network"):
        network = data["network"].get("name", "Unknown")
    else:
        network = "Unknown"

    url = data.get("url", "")

    # Get episode info
    embedded = data.get("_embedded", {})
    next_ep = embedded.get("nextepisode")
    prev_ep = embedded.get("previousepisode")

    # Format output
    await bot.say(
        trigger.target,
        f"\x0307Show:\x03 {name} \x0311|\x03 \x0307Status:\x03 {status} "
        f"\x0311|\x03 \x0307Genre:\x03 {genres}",
    )  # type: ignore[attr-defined]
    await bot.say(
        trigger.target,
        f"\x0307Network:\x03 {network} \x0311|\x03 \x0307URL:\x03 {url}",
    )  # type: ignore[attr-defined]

    if prev_ep and next_ep:
        prev_str = _format_episode(prev_ep["season"], prev_ep["number"])
        next_str = _format_episode(next_ep["season"], next_ep["number"])
        prev_date = prev_ep.get("airdate", "Unknown")
        next_date = next_ep.get("airdate", "Unknown")
        next_time = _format_air_time(next_ep.get("airstamp"))
        await bot.say(
            trigger.target,
            f"\x0307Last Episode:\x03 {prev_str} ({prev_date}) \x0311|\x03 "
            f"\x0307Next Episode:\x03 {next_str} ({next_date} at {next_time})",
        )  # type: ignore[attr-defined]
    elif prev_ep:
        prev_str = _format_episode(prev_ep["season"], prev_ep["number"])
        prev_date = prev_ep.get("airdate", "Unknown")
        await bot.say(
            trigger.target, f"\x0307Last Episode:\x03 {prev_str} ({prev_date})"
        )  # type: ignore[attr-defined]
    elif next_ep:
        next_str = _format_episode(next_ep["season"], next_ep["number"])
        next_date = next_ep.get("airdate", "Unknown")
        next_time = _format_air_time(next_ep.get("airstamp"))
        await bot.say(
            trigger.target,
            f"\x0307Next Episode:\x03 {next_str} ({next_date} at {next_time})",
        )  # type: ignore[attr-defined]


@plugin.command(
    "next",
    aliases=["n"],
    help="Show next episode info",
    usage="!next <show name>",
)
async def cmd_next(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !next <show name>")  # type: ignore[attr-defined]
        return

    show_name = " ".join(trigger.args)
    data = await _lookup_show(show_name)

    if not data or "name" not in data:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error: Show '{show_name}' not found"
        )
        return

    next_ep = data.get("_embedded", {}).get("nextepisode")
    if not next_ep:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304No upcoming episode for {data['name']}"
        )
        return

    name = data["name"]
    ep_str = _format_episode(next_ep["season"], next_ep["number"])
    ep_name = next_ep.get("name", "Unknown")
    air_date = next_ep.get("airdate", "Unknown")
    air_time = _format_air_time(next_ep.get("airstamp"))
    runtime = next_ep.get("runtime", "?")

    await bot.say(
        trigger.target,
        f"\x0307{name}\x03 — {ep_str}: {ep_name} \x0311|\x03 {air_date} at {air_time}",
    )  # type: ignore[attr-defined]
    if runtime:
        await bot.say(trigger.target, f"\x0307Runtime:\x03 {runtime} minutes")  # type: ignore[attr-defined]


@plugin.command(
    "last",
    aliases=["l"],
    help="Show last episode info",
    usage="!last <show name>",
)
async def cmd_last(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !last <show name>")  # type: ignore[attr-defined]
        return

    show_name = " ".join(trigger.args)
    data = await _lookup_show(show_name)

    if not data or "name" not in data:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error: Show '{show_name}' not found"
        )
        return

    prev_ep = data.get("_embedded", {}).get("previousepisode")
    if not prev_ep:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304No previous episode for {data['name']}"
        )
        return

    name = data["name"]
    ep_str = _format_episode(prev_ep["season"], prev_ep["number"])
    ep_name = prev_ep.get("name", "Unknown")
    air_date = prev_ep.get("airdate", "Unknown")
    runtime = prev_ep.get("runtime", "?")

    await bot.say(
        trigger.target,
        f"\x0307{name}\x03 — {ep_str}: {ep_name} \x0311|\x03 {air_date}",
    )  # type: ignore[attr-defined]
    if runtime:
        await bot.say(trigger.target, f"\x0307Runtime:\x03 {runtime} minutes")  # type: ignore[attr-defined]


def _format_air_time(airstamp: str | None) -> str:
    """Convert ISO timestamp to user-friendly time in NYC timezone."""
    if not airstamp:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(airstamp.replace("Z", "+00:00"))
        nyc_tz = ZoneInfo("America/New_York")
        nyc_dt = dt.astimezone(nyc_tz)
        return nyc_dt.strftime("%I:%M %p %Z")
    except Exception:
        return airstamp
