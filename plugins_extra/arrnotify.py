"""
Arr Notify plugin — receive webhooks from *arr apps and announce to IRC.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18


Supported sources: Sonarr, Radarr, Tautulli, Plex, Overseerr, Jellyseerr, Notifiarr

Plugin vars (config.yaml plugins.vars.arrnotify):
  announce_channels:
    - "#media"
    - "#plex"

  # Per-source secrets (for validating incoming webhooks — optional but recommended)
  sonarr_secret: ""
  radarr_secret: ""
  tautulli_secret: ""
  plex_token: ""
  overseerr_secret: ""

  # Enable/disable specific event types per source
  sonarr:
    on_grab: false              # "Grabbed: ShowName S01E01"
    on_download: true           # "Downloaded: ShowName S01E01 (1080p)"
    on_upgrade: true            # "Upgraded: ShowName S01E01 → Bluray-1080p"
    on_series_add: true         # "Added to library: ShowName"
    on_health_issue: false
    on_test: false

  radarr:
    on_grab: false
    on_download: true           # "Downloaded: Movie Title (2024) [1080p]"
    on_upgrade: true
    on_movie_add: true          # "Added to library: Movie Title (2024)"
    on_health_issue: false
    on_test: false

  tautulli:
    on_play: true               # "jarsky started watching ShowName S01E01"
    on_stop: false
    on_pause: false
    on_resume: false
    on_watched: true            # "jarsky finished watching ShowName S01E01"
    on_added: true              # "New on Plex: ShowName S01E01"

  plex:
    on_play: false
    on_stop: false
    on_new: true                # "New on Plex: ShowName S01E01"

  overseerr:
    on_request: true            # "jarsky requested: Movie Title (2024)"
    on_approved: true           # "Approved: Movie Title (2024)"
    on_available: true          # "Now available: Movie Title (2024)"
    on_failed: false
    on_test: false

Webhook URLs to configure in each app:
  Sonarr:     http://your-bot:8080/webhooks/sonarr
  Radarr:     http://your-bot:8080/webhooks/radarr
  Tautulli:   http://your-bot:8080/webhooks/tautulli
  Plex:       http://your-bot:8080/webhooks/plex
  Overseerr:  http://your-bot:8080/webhooks/overseerr
  Jellyseerr: http://your-bot:8080/webhooks/jellyseerr
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "Receive *arr webhooks (Sonarr, Radarr, Plex, Overseerr) and announce to IRC.",
    "url": "https://github.com/Jarsky/pyra",
}

_BOT_REF: object = None


def setup(bot: object) -> None:
    global _BOT_REF
    _BOT_REF = bot
    # Store config in bot memory so the webhook router can access it
    cfg: dict[str, object] = bot.plugin_config("arrnotify")  # type: ignore[attr-defined]
    bot.memory["arrnotify"] = cfg  # type: ignore[attr-defined]


def shutdown(bot: object) -> None:
    bot.memory.pop("arrnotify", None)  # type: ignore[attr-defined]


def get_bot() -> object:
    return _BOT_REF


# ── Formatters ──────────────────────────────────────────────────────────────

def fmt_sonarr(payload: dict) -> str | None:
    event = payload.get("eventType", "")
    series = payload.get("series", {}).get("title", "Unknown")
    episodes = payload.get("episodes", [])
    ep = episodes[0] if episodes else {}
    s = ep.get("seasonNumber", "?")
    e = ep.get("episodeNumber", "?")
    ep_title = ep.get("title", "")
    se = f"S{int(s):02d}E{int(e):02d}" if isinstance(s, int) and isinstance(e, int) else f"S{s}E{e}"

    quality = ""
    if "episodeFile" in payload:
        quality = payload["episodeFile"].get("quality", {}).get("quality", {}).get("name", "")
    elif "release" in payload:
        quality = payload["release"].get("quality", "")

    q_str = f" [{quality}]" if quality else ""
    ep_str = f" — {ep_title}" if ep_title else ""

    if event == "Grab":
        return f"⬇ \x02Sonarr\x02: Grabbing {series} {se}{ep_str}{q_str}"
    elif event == "Download":
        verb = "Upgraded" if payload.get("isUpgrade") else "Downloaded"
        return f"✅ \x02Sonarr\x02: {verb} {series} {se}{ep_str}{q_str}"
    elif event == "SeriesAdd":
        return f"📺 \x02Sonarr\x02: Added series — {series}"
    elif event == "SeriesDelete":
        return f"🗑 \x02Sonarr\x02: Deleted series — {series}"
    elif event == "EpisodeFileDelete":
        return f"🗑 \x02Sonarr\x02: Deleted episode — {series} {se}"
    elif event == "HealthIssue":
        msg = payload.get("message", "")
        return f"⚠ \x02Sonarr\x02: Health issue — {msg}"
    elif event == "Test":
        return "🔧 \x02Sonarr\x02: Test notification received"
    return None


def fmt_radarr(payload: dict) -> str | None:
    event = payload.get("eventType", "")
    movie = payload.get("movie", {})
    title = movie.get("title", "Unknown")
    year = movie.get("year", "")
    title_year = f"{title} ({year})" if year else title

    quality = ""
    if "movieFile" in payload:
        quality = payload["movieFile"].get("quality", {}).get("quality", {}).get("name", "")
    elif "release" in payload:
        quality = payload["release"].get("quality", "")
    q_str = f" [{quality}]" if quality else ""

    if event == "Grab":
        return f"⬇ \x02Radarr\x02: Grabbing {title_year}{q_str}"
    elif event == "Download":
        verb = "Upgraded" if payload.get("isUpgrade") else "Downloaded"
        return f"✅ \x02Radarr\x02: {verb} {title_year}{q_str}"
    elif event == "MovieAdded":
        return f"🎬 \x02Radarr\x02: Added to library — {title_year}"
    elif event == "MovieDelete":
        return f"🗑 \x02Radarr\x02: Deleted — {title_year}"
    elif event == "HealthIssue":
        msg = payload.get("message", "")
        return f"⚠ \x02Radarr\x02: Health issue — {msg}"
    elif event == "Test":
        return "🔧 \x02Radarr\x02: Test notification received"
    return None


def fmt_tautulli(payload: dict) -> str | None:
    action = payload.get("action", payload.get("event", ""))
    user = payload.get("user", payload.get("username", "someone"))
    title = payload.get("title", "Unknown")
    media_type = payload.get("media_type", "")
    grandparent = payload.get("grandparent_title", "")
    parent_idx = payload.get("parent_media_index", "")
    media_idx = payload.get("media_index", "")

    # Format episode as ShowName S01E01
    if media_type == "episode" and grandparent:
        se = ""
        if parent_idx and media_idx:
            try:
                se = f" S{int(parent_idx):02d}E{int(media_idx):02d}"
            except (ValueError, TypeError):
                se = f" {parent_idx}x{media_idx}"
        display = f"{grandparent}{se}: {title}"
    elif media_type == "movie":
        year = payload.get("year", "")
        display = f"{title} ({year})" if year else title
    else:
        display = title

    if action in ("play", "watched"):
        verb = "started watching" if action == "play" else "finished watching"
        return f"▶ \x02Tautulli\x02: {user} {verb} \x02{display}\x02"
    elif action == "added":
        return f"🆕 \x02Plex\x02: New — \x02{display}\x02"
    elif action == "pause":
        return f"⏸ \x02Tautulli\x02: {user} paused \x02{display}\x02"
    elif action == "resume":
        return f"▶ \x02Tautulli\x02: {user} resumed \x02{display}\x02"
    return None


def fmt_plex(payload: dict) -> str | None:
    event = payload.get("event", "")
    metadata = payload.get("Metadata", {})
    account = payload.get("Account", {})
    user = account.get("title", "someone")

    media_type = metadata.get("type", "")
    title = metadata.get("title", "Unknown")
    show = metadata.get("grandparentTitle", "")
    season = metadata.get("parentIndex", "")
    episode = metadata.get("index", "")

    if media_type == "episode" and show:
        se = ""
        if season and episode:
            try:
                se = f" S{int(season):02d}E{int(episode):02d}"
            except (ValueError, TypeError):
                pass
        display = f"{show}{se}: {title}"
    else:
        display = title

    if event == "media.play":
        return f"▶ \x02Plex\x02: {user} playing \x02{display}\x02"
    elif event == "media.stop":
        return f"⏹ \x02Plex\x02: {user} stopped \x02{display}\x02"
    elif event == "library.new":
        return f"🆕 \x02Plex\x02: New in library — \x02{display}\x02"
    return None


def fmt_overseerr(payload: dict, source: str = "Overseerr") -> str | None:
    notif_type = payload.get("notification_type", "")
    subject = payload.get("subject", "Unknown")
    requester = payload.get("request", {}).get("requestedBy", {}).get("username", "someone")
    media_type = payload.get("media", {}).get("media_type", "")
    type_str = f" [{media_type}]" if media_type else ""

    if notif_type == "MEDIA_PENDING":
        return f"📋 \x02{source}\x02: {requester} requested{type_str} — \x02{subject}\x02"
    elif notif_type in ("MEDIA_APPROVED", "MEDIA_AUTO_APPROVED"):
        return f"✅ \x02{source}\x02: Approved{type_str} — \x02{subject}\x02"
    elif notif_type == "MEDIA_AVAILABLE":
        return f"🎉 \x02{source}\x02: Now available{type_str} — \x02{subject}\x02"
    elif notif_type == "MEDIA_FAILED":
        return f"❌ \x02{source}\x02: Failed{type_str} — \x02{subject}\x02"
    elif notif_type == "TEST_NOTIFICATION":
        return f"🔧 \x02{source}\x02: Test notification received"
    return None
