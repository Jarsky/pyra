"""
Headlines plugin — Fetch and display news from RSS feeds.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Supports multiple news sources (CNN, RNZ, The Guardian, etc.).

Commands:
  !news [feed]       Show latest headlines from default or named feed
  !headlines [feed]  Alias for !news
  !feeds             List available feed names

Plugin vars (config.yaml plugins.vars.headlines):
  feeds:
    cnn: "http://rss.cnn.com/rss/edition_world.rss"
    rnz: "https://www.rnz.co.nz/rss/world.xml"
    # ... add more feeds
  default_feed: "cnn"  # Used if no feed name specified
  cache_seconds: 3600  # How long to cache RSS feed results
  max_headlines: 5     # Max headlines to display per request
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_DEFAULT_FEEDS = {
    "cnn": "http://rss.cnn.com/rss/edition_world.rss",
    "rnz": "https://www.rnz.co.nz/rss/world.xml",
    "stuff": "https://www.stuff.co.nz/rss",
    "guardian": "https://www.theguardian.com/uk-news/rss",
    "cbc": "https://www.cbc.ca/cmlink/rss-canada",
    "9news": "https://www.9news.com.au/national/rss",
}


def _get_feed_config(bot: object) -> dict[str, str]:
    """Get feed URLs from config, or use defaults."""
    cfg: dict[str, object] = bot.plugin_config("headlines")  # type: ignore[attr-defined]
    feeds = cfg.get("feeds", {})
    if isinstance(feeds, dict):
        return {**_DEFAULT_FEEDS, **feeds}
    return _DEFAULT_FEEDS


def _get_default_feed(bot: object) -> str:
    """Get default feed name from config."""
    cfg: dict[str, object] = bot.plugin_config("headlines")  # type: ignore[attr-defined]
    default = cfg.get("default_feed", "cnn")
    return str(default) if isinstance(default, str) else "cnn"


def _get_cache_seconds(bot: object) -> int:
    """Get cache duration in seconds from config."""
    cfg: dict[str, object] = bot.plugin_config("headlines")  # type: ignore[attr-defined]
    cache = cfg.get("cache_seconds", 3600)
    return int(cache) if isinstance(cache, int) else 3600


def _get_max_headlines(bot: object) -> int:
    """Get max headlines to display from config."""
    cfg: dict[str, object] = bot.plugin_config("headlines")  # type: ignore[attr-defined]
    max_h = cfg.get("max_headlines", 5)
    return int(max_h) if isinstance(max_h, int) else 5


async def _fetch_rss(url: str, timeout: float = 10.0) -> list[tuple[str, str]] | None:
    """
    Fetch RSS feed and return list of (title, link) tuples.
    Returns None on error.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "PyraBot/1.0 (IRC bot)"},
            )
            if resp.status_code != 200:
                return None

        root = ET.fromstring(resp.content)  # noqa: S314
        items: list[tuple[str, str]] = []

        for item_elem in root.findall(".//item"):
            title_elem = item_elem.find("title")
            link_elem = item_elem.find("link")

            title = title_elem.text if title_elem is not None else "No title"
            link = link_elem.text if link_elem is not None else "No link"

            items.append((str(title), str(link)))

        return items
    except Exception as e:
        # Log but don't crash
        print(f"Error fetching RSS from {url}: {e}")
        return None


async def _get_cached_headlines(
    session: object, feed_name: str, cache_seconds: int
) -> list[tuple[str, str]] | None:
    """
    Get cached headlines from database if they exist and are fresh.
    Returns None if cache miss or expired.
    """
    from pybot.core.database import get_plugin_setting

    try:
        cache_key = f"feed_cache:{feed_name}"
        timestamp_key = f"feed_cache_time:{feed_name}"

        cached = await get_plugin_setting(session, "headlines", cache_key, channel=None)  # type: ignore[arg-type]
        timestamp_str = await get_plugin_setting(
            session, "headlines", timestamp_key, channel=None  # type: ignore[arg-type]
        )

        if not cached or not timestamp_str:
            return None

        # Check if cache is still fresh
        cache_time = datetime.fromisoformat(timestamp_str)
        if datetime.now() - cache_time > timedelta(seconds=cache_seconds):
            return None

        # Parse cached data (simple JSON-like format)
        lines = cached.split("\n")
        items = []
        for line in lines:
            if "|" in line:
                title, link = line.split("|", 1)
                items.append((title, link))
        return items if items else None
    except Exception:
        return None


async def _set_cached_headlines(
    session: object, feed_name: str, items: list[tuple[str, str]]
) -> None:
    """Cache headlines in the database."""
    from pybot.core.database import set_plugin_setting

    try:
        cache_key = f"feed_cache:{feed_name}"
        timestamp_key = f"feed_cache_time:{feed_name}"

        # Serialize items
        cached_data = "\n".join(f"{title}|{link}" for title, link in items)

        await set_plugin_setting(session, "headlines", cache_key, cached_data, channel=None)  # type: ignore[arg-type]
        await set_plugin_setting(
            session,  # type: ignore[arg-type]
            "headlines",
            timestamp_key,
            datetime.now().isoformat(),
            channel=None,
        )
    except Exception as e:
        print(f"Error caching headlines: {e}")


@plugin.command(
    "headlines",
    aliases=["news"],
    help="Fetch and display news headlines",
    usage="!headlines [feed_name | list | set <feed>]",
)
async def cmd_headlines(bot: object, trigger: Trigger) -> None:
    from pybot.core.database import get_plugin_setting, get_session, set_plugin_setting

    feeds = _get_feed_config(bot)
    max_h = _get_max_headlines(bot)
    cache_seconds = _get_cache_seconds(bot)

    # Parse arguments
    feed_name: str | None = None
    if trigger.args:
        subcommand = trigger.args[0].lower()

        if subcommand == "list":
            # List all available feeds
            feed_list = ", ".join(sorted(feeds.keys()))
            await bot.say(  # type: ignore[attr-defined]
                trigger.target, f"\x0303Available feeds: {feed_list}"
            )
            return

        elif subcommand == "set" and len(trigger.args) > 1:
            # User wants to set their preferred feed
            feed_to_set = trigger.args[1].lower()
            if feed_to_set in feeds:
                async with get_session() as session:
                    await set_plugin_setting(
                        session,
                        "headlines",
                        "preferred_feed",
                        feed_to_set,
                        channel=trigger.nick,
                    )
                await bot.reply(  # type: ignore[attr-defined]
                    trigger, f"Preferred feed set to: {feed_to_set}"
                )
                return
            else:
                await bot.reply(  # type: ignore[attr-defined]
                    trigger, f"Unknown feed: {feed_to_set}. Type !headlines list"
                )
                return

        elif subcommand in feeds:
            # User specified a feed name
            feed_name = subcommand
        elif subcommand not in feeds:
            # Invalid feed name
            await bot.reply(  # type: ignore[attr-defined]
                trigger, f"Unknown feed: {subcommand}. Type !headlines list"
            )
            return

    # If no feed specified, use user's preferred or default
    if not feed_name:
        async with get_session() as session:
            preferred = await get_plugin_setting(
                session, "headlines", "preferred_feed", channel=trigger.nick
            )
        feed_name = preferred if preferred and preferred in feeds else _get_default_feed(bot)

    # Fetch headlines (with caching)
    async with get_session() as session:
        items = await _get_cached_headlines(session, feed_name, cache_seconds)

        if not items:
            # Cache miss, fetch fresh
            feed_url = feeds[feed_name]
            items = await _fetch_rss(feed_url)

            if not items:
                await bot.say(  # type: ignore[attr-defined]
                    trigger.target, f"\x0304Error fetching {feed_name} feed"
                )
                return

            # Cache for next time
            await _set_cached_headlines(session, feed_name, items)

    # Display headlines
    if not items:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304No headlines found for {feed_name}"
        )
        return

    await bot.say(  # type: ignore[attr-defined]
        trigger.target, f"\x0303\x02{feed_name.upper()} Headlines:\x02"
    )

    for i, (title, link) in enumerate(items[:max_h], 1):
        # Truncate title if too long
        if len(title) > 100:
            title = title[:97] + "..."
        await bot.say(trigger.target, f"\x0307{i}.\x03 {title} — {link}")  # type: ignore[attr-defined]
