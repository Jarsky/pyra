"""
Search plugin — DuckDuckGo instant answers, Wikipedia summaries, and definitions.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Commands:
  !search <query>    DuckDuckGo instant answer
  !ddg <query>       Alias for !search
  !wiki <topic>      Wikipedia summary
  !define <word>     Dictionary definition

Plugin vars (config.yaml plugins.vars.search):
  max_results: int   (default: 3, controls summary length)
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "DuckDuckGo instant answers, Wikipedia summaries, and dictionary definitions.",
    "url": "https://github.com/Jarsky/pyra",
}

from typing import Any

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_DDG_URL = "https://api.duckduckgo.com/"
_WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"


@plugin.command(
    "search",
    aliases=["ddg", "g"],
    help="DuckDuckGo instant answer",
    usage="!search <query>",
)
async def cmd_search(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !search <query>")  # type: ignore[attr-defined]
        return
    query = " ".join(trigger.args)
    result = await _ddg_search(query)
    await bot.say(trigger.target, result)  # type: ignore[attr-defined]


@plugin.command("wiki", help="Wikipedia summary", usage="!wiki <query>")
async def cmd_wiki(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !wiki <query>")  # type: ignore[attr-defined]
        return
    query = "_".join(trigger.args)
    result = await _wiki_summary(query)
    await bot.say(trigger.target, result)  # type: ignore[attr-defined]


@plugin.command("define", help="Dictionary definition", usage="!define <word>")
async def cmd_define(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !define <word>")  # type: ignore[attr-defined]
        return
    word = trigger.args[0]
    result = await _ddg_search(f"define {word}")
    await bot.say(trigger.target, result)  # type: ignore[attr-defined]


async def _ddg_search(query: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                _DDG_URL,
                params={
                    "q": query,
                    "format": "json",
                    "no_redirect": "1",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
                headers={"User-Agent": "PyraBot/1.0"},
            )
            data = resp.json()
    except Exception as exc:
        return f"Search error: {exc!r}"  # type: ignore[no-any-return]

    abstract = data.get("AbstractText", "").strip()
    if abstract:
        return abstract[:350] + ("..." if len(abstract) > 350 else "")

    answer = data.get("Answer", "").strip()
    if answer:
        return answer[:350]  # type: ignore[no-any-return]

    related = data.get("RelatedTopics", [])
    if related:
        first = related[0]
        if isinstance(first, dict):
            text = first.get("Text", "").strip()
            url = first.get("FirstURL", "")
            if text:
                return f"{text[:280]} — {url}" if url else text[:300]

    return f"No results for: {query}"


async def _wiki_summary(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{_WIKI_API}{query}",
                headers={"User-Agent": "PyraBot/1.0"},
            )
            if resp.status_code == 404:
                return f"Wikipedia: No article found for '{query}'"
            data = resp.json()
    except Exception as exc:
        return f"Wikipedia error: {exc}"

    extract = data.get("extract", "").strip()
    title = data.get("title", query)
    url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

    if not extract:
        return f"Wikipedia: No summary for '{title}'"

    summary = extract[:300] + ("..." if len(extract) > 300 else "")
    return f"\x02{title}\x02: {summary} {url}".strip()
