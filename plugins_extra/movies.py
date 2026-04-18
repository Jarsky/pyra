"""
Movies plugin — Movie information via OMDb API.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Provides commands to look up movies with ratings, cast, and plot info.

Commands:
  !movie <title> [year]   Look up movie by title (optionally with year)
  !imdb <title> [year]    Alias for !movie

Plugin vars (config.yaml plugins.vars.movies):
  api_key: "your_omdb_api_key"  (free key at https://www.omdbapi.com)
"""

from __future__ import annotations

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_OMDB_API = "http://www.omdbapi.com"


async def _lookup_movie(api_key: str, title: str, year: str = "") -> dict[str, object] | None:
    """Fetch movie data from OMDb API."""
    if not api_key:
        return None

    try:
        params = {"t": title, "apikey": api_key, "type": "movie"}
        if year:
            params["y"] = year

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_OMDB_API, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("Response") == "False":
                return None
            return data
    except Exception:
        return None


def _get_api_key(bot: object) -> str:
    """Get OMDb API key from config."""
    cfg: dict[str, object] = bot.plugin_config("movies")  # type: ignore[attr-defined]
    return str(cfg.get("api_key", "")) if cfg.get("api_key") else ""


@plugin.command(
    "movie",
    aliases=["imdb"],
    help="Look up movie info from OMDb",
    usage="!movie <title> [year]",
)
async def cmd_movie(bot: object, trigger: Trigger) -> None:
    api_key = _get_api_key(bot)
    if not api_key:
        await bot.reply(  # type: ignore[attr-defined]
            trigger, "OMDb API key not configured in config.yaml"
        )
        return

    if not trigger.args:
        await bot.reply(trigger, "Usage: !movie <title> [year]")  # type: ignore[attr-defined]
        return

    # Extract year if last arg is 4 digits
    year = ""
    args = trigger.args.copy()
    if args and len(args[-1]) == 4 and args[-1].isdigit():
        year = args.pop()

    title = " ".join(args)
    data = await _lookup_movie(api_key, title, year)

    if not data:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error: Movie '{title}' not found"
        )
        return

    # Extract fields
    movie_title = data.get("Title", "Unknown")
    year_release = data.get("Year", "Unknown")
    rated = data.get("Rated", "Unknown")
    runtime = data.get("Runtime", "Unknown")
    genre = data.get("Genre", "Unknown")
    released = data.get("Released", "Unknown")
    country = data.get("Country", "Unknown")
    plot = data.get("Plot", "Unknown")
    cast = data.get("Actors", "Unknown")
    metascore = data.get("Metascore", "N/A")
    imdb_rating = data.get("imdbRating", "N/A")
    imdb_id = data.get("imdbID", "unknown")
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/"

    # Format output with IRC colors
    await bot.say(
        trigger.target,
        f"\x0303\x02{movie_title}\x02\x03 ({year_release}) \x0311|\x03 {rated} "
        f"\x0311|\x03 {runtime} \x0311|\x03 {genre} \x0311|\x03 {released} "
        f"\x0311|\x03 {country}",
    )  # type: ignore[attr-defined]
    await bot.say(trigger.target, f"\x0307Plot:\x03 {plot}")  # type: ignore[attr-defined]
    await bot.say(
        trigger.target,
        f"\x0307Metascore:\x03 {metascore} \x0311|\x03 \x0307IMDB Rating:\x03 {imdb_rating} "
        f"\x0311|\x03 \x0307Cast:\x03 {cast} \x0311|\x03 \x0307URL:\x03 {imdb_url}",
    )  # type: ignore[attr-defined]
