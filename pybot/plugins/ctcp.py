"""
CTCP compatibility plugin - common CTCP replies and safe DCC handling policy.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-19

Commands:
  !ctcpstatus           Show current CTCP/DCC runtime settings

Passive behavior:
  - Replies to CTCP VERSION, PING, TIME, CLIENTINFO, and SOURCE
  - Ignores CTCP ACTION (normal /me traffic)
  - Handles CTCP DCC by policy (deny by default with helpful notice)

Plugin vars (config.yaml plugins.vars.ctcp):
  enabled: true
  version_reply: "Pyra IRC Bot"
  source_url: "https://github.com/Jarsky/pyra"
  allow_dcc: false
  dcc_reply: "DCC is disabled on this bot. Use partyline/web UI."
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from pybot import plugin
from pybot.plugin import Trigger

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-19",
    "description": "CTCP replies (VERSION/PING/TIME/etc.) with explicit DCC policy handling.",
    "url": "https://github.com/Jarsky/pyra",
}


def _cfg(bot: object) -> dict[str, Any]:
    cfg = bot.plugin_config("ctcp")  # type: ignore[attr-defined]
    if isinstance(cfg, dict):
        return cast(dict[str, Any], cfg)
    return {}


def _enabled(bot: object) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _version_reply(bot: object) -> str:
    val = _cfg(bot).get("version_reply", "Pyra IRC Bot")
    return str(val)


def _source_url(bot: object) -> str:
    val = _cfg(bot).get("source_url", "https://github.com/Jarsky/pyra")
    return str(val)


def _allow_dcc(bot: object) -> bool:
    return bool(_cfg(bot).get("allow_dcc", False))


def _dcc_reply(bot: object) -> str:
    val = _cfg(bot).get("dcc_reply", "DCC is disabled on this bot. Use partyline/web UI.")
    return str(val)


def _safe_ctcp_text(value: str, max_len: int = 160) -> str:
    """Normalize control characters and cap payload size to avoid noisy replies."""
    cleaned = value.replace("\r", " ").replace("\n", " ").replace("\x00", "").strip()
    return cleaned[:max_len]


@plugin.command("ctcpstatus", privilege="a", help="Show CTCP/DCC settings", usage="!ctcpstatus")
async def cmd_ctcpstatus(bot: object, trigger: Trigger) -> None:
    await bot.reply(  # type: ignore[attr-defined]
        trigger,
        f"CTCP enabled={_enabled(bot)}; allow_dcc={_allow_dcc(bot)}; source={_source_url(bot)}",
    )


@plugin.event("PRIVMSG")
async def on_privmsg_ctcp(bot: object, trigger: Trigger) -> None:
    if not _enabled(bot):
        return

    msg = trigger.message
    # Never respond to inbound CTCP NOTICE frames to avoid reply loops.
    if msg.command != "PRIVMSG":
        return

    ctcp = msg.ctcp_command
    if not ctcp:
        return

    ctcp = ctcp.upper()

    # /me ACTION is normal chat behavior; do not emit CTCP replies.
    if ctcp == "ACTION":
        return

    if ctcp == "VERSION":
        version = _safe_ctcp_text(_version_reply(bot), max_len=120)
        await bot.notice(trigger.nick, f"\x01VERSION {version}\x01")  # type: ignore[attr-defined]
        return

    if ctcp == "PING":
        payload = _safe_ctcp_text(msg.ctcp_text, max_len=160)
        if payload:
            await bot.notice(trigger.nick, f"\x01PING {payload}\x01")  # type: ignore[attr-defined]
        else:
            await bot.notice(trigger.nick, "\x01PING\x01")  # type: ignore[attr-defined]
        return

    if ctcp == "TIME":
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        await bot.notice(trigger.nick, f"\x01TIME {now}\x01")  # type: ignore[attr-defined]
        return

    if ctcp == "CLIENTINFO":
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick,
            "\x01CLIENTINFO ACTION VERSION PING TIME SOURCE CLIENTINFO DCC\x01",
        )
        return

    if ctcp == "SOURCE":
        source_url = _safe_ctcp_text(_source_url(bot), max_len=200)
        await bot.notice(trigger.nick, f"\x01SOURCE {source_url}\x01")  # type: ignore[attr-defined]
        return

    if ctcp == "DCC":
        # Safe default: explicit deny unless admin enables DCC policy in config.
        if _allow_dcc(bot):
            await bot.notice(  # type: ignore[attr-defined]
                trigger.nick,
                "\x01ERRMSG DCC DCC policy enabled; direct DCC "
                "chat/file transport is not implemented\x01",
            )
        else:
            await bot.notice(trigger.nick, _safe_ctcp_text(_dcc_reply(bot), max_len=220))  # type: ignore[attr-defined]
        return
