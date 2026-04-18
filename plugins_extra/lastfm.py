"""
Last.fm plugin — show now-playing and recent tracks.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18


Plugin vars (config.yaml plugins.vars.lastfm):
  api_key: "your_lastfm_api_key"   (get free key at https://www.last.fm/api)

Commands:
  !np [nick]            Show now-playing (uses saved username or IRC nick)
  !lastfm set <user>    Save your Last.fm username
  !recent [nick]        Show last 5 tracks
  !compat <nick>        Compare listening taste with another user
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "Show now-playing and recent Last.fm tracks with taste comparison.",
    "url": "https://github.com/Jarsky/pyra",
}

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_API = "https://ws.audioscrobbler.com/2.0/"


async def _api(api_key: str, method: str, **params: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_API, params={
                "method": method,
                "api_key": api_key,
                "format": "json",
                **params,
            })
            return resp.json()
    except Exception:
        return {}


async def _get_lfm_user(bot: object, nick: str) -> str | None:
    """Return saved Last.fm username for nick, or None."""
    from pybot.core.database import get_plugin_setting, get_session
    async with get_session() as session:
        saved = await get_plugin_setting(session, "lastfm", "username", channel=nick)
    return saved or None


@plugin.command(
    "np", aliases=["nowplaying", "lastfm"], help="Show now-playing track", usage="!np [nick]"
)
async def cmd_np(bot: object, trigger: Trigger) -> None:
    cfg: dict[str, object] = bot.plugin_config("lastfm")  # type: ignore[attr-defined]
    api_key = str(cfg.get("api_key", ""))
    if not api_key:
        await bot.reply(trigger, "No Last.fm API key — set plugins.vars.lastfm.api_key")  # type: ignore[attr-defined]
        return

    lookup_nick = trigger.args[0] if trigger.args else trigger.nick
    lfm_user = await _get_lfm_user(bot, lookup_nick)
    if not lfm_user:
        if lookup_nick == trigger.nick:
            await bot.reply(trigger, "No Last.fm username set. Use: !lastfm set <username>")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"{lookup_nick} hasn't set a Last.fm username.")  # type: ignore[attr-defined]
        return

    data = await _api(api_key, "user.getrecenttracks", user=lfm_user, limit="1")
    tracks = data.get("recenttracks", {}).get("track", [])
    if not tracks:
        await bot.say(trigger.target, f"{lookup_nick} has no recent tracks on Last.fm.")  # type: ignore[attr-defined]
        return

    track = tracks[0] if isinstance(tracks, list) else tracks
    artist = track.get("artist", {}).get("#text", "?")
    title = track.get("name", "?")
    album = track.get("album", {}).get("#text", "")
    now_playing = track.get("@attr", {}).get("nowplaying") == "true"

    album_str = f" [{album}]" if album else ""
    verb = "is listening to" if now_playing else "last played"
    msg = f"\x02{lookup_nick}\x02 {verb}: \x02{title}\x02 by {artist}{album_str}"
    await bot.say(trigger.target, msg)  # type: ignore[attr-defined]


@plugin.command(
    "lastfm_set", aliases=["setlastfm"], help="Save your Last.fm username",
    usage="!lastfm set <username>",
)
async def cmd_lastfm_set(bot: object, trigger: Trigger) -> None:
    # Also handle "!lastfm set <user>" via the np command alias — catch the sub-command here
    if not trigger.args:
        await bot.reply(trigger, "Usage: !lastfm set <username>")  # type: ignore[attr-defined]
        return
    username = trigger.args[0]
    from pybot.core.database import get_session, set_plugin_setting
    async with get_session() as session:
        await set_plugin_setting(session, "lastfm", "username", username, channel=trigger.nick)
    await bot.reply(trigger, f"Last.fm username saved: {username}")  # type: ignore[attr-defined]


@plugin.command("recent", help="Show last 5 scrobbled tracks", usage="!recent [nick]")
async def cmd_recent(bot: object, trigger: Trigger) -> None:
    cfg: dict[str, object] = bot.plugin_config("lastfm")  # type: ignore[attr-defined]
    api_key = str(cfg.get("api_key", ""))
    if not api_key:
        await bot.reply(trigger, "No Last.fm API key configured.")  # type: ignore[attr-defined]
        return

    lookup_nick = trigger.args[0] if trigger.args else trigger.nick
    lfm_user = await _get_lfm_user(bot, lookup_nick)
    if not lfm_user:
        await bot.reply(trigger, f"No Last.fm username for {lookup_nick}.")  # type: ignore[attr-defined]
        return

    data = await _api(api_key, "user.getrecenttracks", user=lfm_user, limit="5")
    tracks = data.get("recenttracks", {}).get("track", [])
    if not tracks:
        await bot.say(trigger.target, f"{lookup_nick} has no recent tracks.")  # type: ignore[attr-defined]
        return

    await bot.say(trigger.target, f"\x02{lookup_nick}'s recent tracks\x02:")  # type: ignore[attr-defined]
    for i, t in enumerate(tracks[:5], 1):
        artist = t.get("artist", {}).get("#text", "?")
        title = t.get("name", "?")
        now = t.get("@attr", {}).get("nowplaying") == "true"
        prefix = "▶" if now else f"{i}."
        await bot.say(trigger.target, f"  {prefix} {title} — {artist}")  # type: ignore[attr-defined]


@plugin.command("compat", help="Compare Last.fm taste with another user", usage="!compat <nick>")
async def cmd_compat(bot: object, trigger: Trigger) -> None:
    cfg: dict[str, object] = bot.plugin_config("lastfm")  # type: ignore[attr-defined]
    api_key = str(cfg.get("api_key", ""))
    if not api_key:
        await bot.reply(trigger, "No Last.fm API key configured.")  # type: ignore[attr-defined]
        return

    if not trigger.args:
        await bot.reply(trigger, "Usage: !compat <nick>")  # type: ignore[attr-defined]
        return

    user1 = await _get_lfm_user(bot, trigger.nick)
    user2 = await _get_lfm_user(bot, trigger.args[0])

    if not user1:
        await bot.reply(trigger, "Set your Last.fm username first: !lastfm set <user>")  # type: ignore[attr-defined]
        return
    if not user2:
        await bot.reply(trigger, f"{trigger.args[0]} hasn't set a Last.fm username.")  # type: ignore[attr-defined]
        return

    data = await _api(
        api_key, "tasteometer.compare",
        type1="user", value1=user1, type2="user", value2=user2,
    )
    score_raw = data.get("comparison", {}).get("result", {}).get("score", None)
    if score_raw is None:
        await bot.say(trigger.target, "Could not fetch compatibility data.")  # type: ignore[attr-defined]
        return

    score = float(score_raw)
    pct = int(score * 100)
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    await bot.say(  # type: ignore[attr-defined]
        trigger.target,
        f"\x02{trigger.nick}\x02 ↔ \x02{trigger.args[0]}\x02 compatibility: {pct}% [{bar}]"
    )
