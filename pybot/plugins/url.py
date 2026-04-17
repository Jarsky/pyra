"""URL plugin — auto-fetch page titles for URLs posted in chat."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

import httpx

from pybot import plugin
from pybot.plugin import Trigger

# Track recently announced URLs per channel to avoid spam
_recent: dict[str, str] = defaultdict(str)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Domains to skip
_SKIP_DOMAINS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
}


@plugin.rule(r"https?://\S+")
async def url_handler(bot: object, trigger: Trigger) -> None:
    if not trigger.channel:
        return

    # Check per-channel setting
    from pybot.core.database import get_channel_setting, get_session

    async with get_session() as session:
        enabled = await get_channel_setting(session, trigger.channel, "url_titles", "true")
    if enabled.lower() not in ("true", "1", "yes", "on"):
        return

    urls = _URL_RE.findall(trigger.text)
    for url in urls[:3]:  # max 3 per message
        parsed = urlparse(url)
        if parsed.hostname in _SKIP_DOMAINS:
            continue

        # Dedup: skip if same URL announced in this channel recently
        if _recent.get(trigger.channel) == url:
            continue

        title = await _fetch_title(url)
        if title:
            _recent[trigger.channel] = url
            await bot.say(trigger.channel, f"[ {title} ]")  # type: ignore[attr-defined]


@plugin.command("title", help="Fetch the title of a URL", usage="!title <url>")
async def cmd_title(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !title <url>")  # type: ignore[attr-defined]
        return
    url = trigger.args[0]
    title = await _fetch_title(url)
    if title:
        await bot.say(trigger.target, f"[ {title} ]")  # type: ignore[attr-defined]
    else:
        await bot.reply(trigger, "Could not fetch title.")  # type: ignore[attr-defined]


async def _fetch_title(url: str) -> str | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PyraBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None

            # Read up to 64KB
            body = resp.text[:65536]

        return _extract_title(url, body)
    except Exception:
        return None


def _extract_title(url: str, html: str) -> str | None:
    host = urlparse(url).hostname or ""

    # YouTube
    if "youtube.com" in host or "youtu.be" in host:
        return _extract_youtube_title(html)

    # Generic <title> tag
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = _clean_text(m.group(1))
        return title[:200] if title else None
    return None


def _extract_youtube_title(html: str) -> str | None:
    patterns = [
        r'"title":\{"runs":\[\{"text":"([^"]+)"',
        r'"title":"([^"]+)","lengthSeconds"',
        r'<meta name="title" content="([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return f"YouTube: {_clean_text(m.group(1))}"
    return None


def _clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    # Decode common HTML entities
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&nbsp;": " ",
    }
    for entity, char in replacements.items():
        s = s.replace(entity, char)
    return s
