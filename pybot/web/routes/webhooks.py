"""Webhook routes for *arr app notifications."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


async def _announce(bot: object, cfg: dict[str, Any], message: str) -> None:
    channels = cfg.get("announce_channels", [])
    if isinstance(channels, str):
        channels = [channels]
    for ch in channels:
        try:
            await bot.say(ch, message)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("arrnotify: failed to announce to %s: %s", ch, exc)


def _is_enabled(cfg: dict, source: str, event_key: str, default: bool = True) -> bool:
    source_cfg = cfg.get(source, {})
    if not isinstance(source_cfg, dict):
        return default
    return bool(source_cfg.get(event_key, default))


@router.post("/sonarr")
async def webhook_sonarr(request: Request) -> dict:
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})
    payload = await request.json()
    event = payload.get("eventType", "")

    # Map event → config key
    key_map = {
        "Grab": "on_grab",
        "Download": "on_upgrade" if payload.get("isUpgrade") else "on_download",
        "SeriesAdd": "on_series_add",
        "HealthIssue": "on_health_issue",
        "Test": "on_test",
    }
    key = key_map.get(event)
    default_on = event not in ("Grab", "HealthIssue", "Test")
    if key and not _is_enabled(cfg, "sonarr", key, default=default_on):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_sonarr  # type: ignore[import]
        msg = fmt_sonarr(payload)
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}


@router.post("/radarr")
async def webhook_radarr(request: Request) -> dict:
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})
    payload = await request.json()
    event = payload.get("eventType", "")

    key_map = {
        "Grab": "on_grab",
        "Download": "on_upgrade" if payload.get("isUpgrade") else "on_download",
        "MovieAdded": "on_movie_add",
        "HealthIssue": "on_health_issue",
        "Test": "on_test",
    }
    key = key_map.get(event)
    default_on = event not in ("Grab", "HealthIssue", "Test")
    if key and not _is_enabled(cfg, "radarr", key, default=default_on):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_radarr  # type: ignore[import]
        msg = fmt_radarr(payload)
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}


@router.post("/tautulli")
async def webhook_tautulli(request: Request) -> dict:
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})
    payload = await request.json()
    action = payload.get("action", payload.get("event", ""))

    key_map = {
        "play": "on_play",
        "stop": "on_stop",
        "pause": "on_pause",
        "resume": "on_resume",
        "watched": "on_watched",
        "added": "on_added",
    }
    key = key_map.get(action)
    default_on = action in ("play", "watched", "added")
    if key and not _is_enabled(cfg, "tautulli", key, default=default_on):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_tautulli  # type: ignore[import]
        msg = fmt_tautulli(payload)
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}


@router.post("/plex")
async def webhook_plex(request: Request) -> dict:
    """Plex sends multipart/form-data with a 'payload' JSON field."""
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})

    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        import json
        try:
            payload = json.loads(str(form.get("payload", "{}")))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Plex payload") from None
    else:
        payload = await request.json()

    event = payload.get("event", "")
    key_map = {"media.play": "on_play", "media.stop": "on_stop", "library.new": "on_new"}
    key = key_map.get(event)
    if key and not _is_enabled(cfg, "plex", key, default=(event == "library.new")):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_plex  # type: ignore[import]
        msg = fmt_plex(payload)
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}


@router.post("/overseerr")
async def webhook_overseerr(request: Request) -> dict:
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})
    payload = await request.json()
    notif_type = payload.get("notification_type", "")

    key_map = {
        "MEDIA_PENDING": "on_request",
        "MEDIA_APPROVED": "on_approved",
        "MEDIA_AUTO_APPROVED": "on_approved",
        "MEDIA_AVAILABLE": "on_available",
        "MEDIA_FAILED": "on_failed",
        "TEST_NOTIFICATION": "on_test",
    }
    key = key_map.get(notif_type)
    default_on = notif_type not in ("MEDIA_FAILED", "TEST_NOTIFICATION")
    if key and not _is_enabled(cfg, "overseerr", key, default=default_on):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_overseerr  # type: ignore[import]
        msg = fmt_overseerr(payload, source="Overseerr")
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}


@router.post("/jellyseerr")
async def webhook_jellyseerr(request: Request) -> dict:
    bot = request.app.state.bot
    cfg: dict = bot.memory.get("arrnotify", {})
    payload = await request.json()
    notif_type = payload.get("notification_type", "")

    key_map = {
        "MEDIA_PENDING": "on_request",
        "MEDIA_APPROVED": "on_approved",
        "MEDIA_AUTO_APPROVED": "on_approved",
        "MEDIA_AVAILABLE": "on_available",
        "MEDIA_FAILED": "on_failed",
        "TEST_NOTIFICATION": "on_test",
    }
    key = key_map.get(notif_type)
    default_on = notif_type not in ("MEDIA_FAILED", "TEST_NOTIFICATION")
    if key and not _is_enabled(cfg, "overseerr", key, default=default_on):
        return {"status": "ignored"}

    try:
        from arrnotify import fmt_overseerr  # type: ignore[import]
        msg = fmt_overseerr(payload, source="Jellyseerr")
    except ImportError:
        return {"status": "arrnotify plugin not loaded"}

    if msg:
        asyncio.create_task(_announce(bot, cfg, msg))
    return {"status": "ok"}
