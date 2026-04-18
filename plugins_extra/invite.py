"""
Invite plugin — invite users to a channel by validating credentials against
an external MySQL database (e.g. shared with a web portal).

Author:  Jarsky
Version: 2.0.0
Date:    2026-04-18
Note:    Python rewrite of invite-mysql.tcl; uses SQLAlchemy async + aiomysql


PM command:
  /msg <bot> !invite <username> <irc-key>

Plugin vars (config.yaml plugins.vars.invite):
  dsn: "mysql+aiomysql://user:pass@host:3306/dbname"
  table: "users"
  user_col: "username"
  key_col: "irckey"
  channel: "#mychan"

Requires aiomysql:
  pip install aiomysql
  or: pip install "pyra[mysql]"

The DSN uses SQLAlchemy async format so no raw MySQL driver code is needed.
"""

from __future__ import annotations

from pybot import plugin
from pybot.plugin import Trigger

_engine: object = None


async def setup(bot: object) -> None:
    global _engine
    cfg: dict[str, object] = bot.plugin_config("invite")  # type: ignore[attr-defined]
    dsn = str(cfg.get("dsn", ""))
    if not dsn:
        return
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        _engine = create_async_engine(dsn, pool_pre_ping=True, pool_size=2, max_overflow=2)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("invite: failed to connect to MySQL: %s", exc)


async def shutdown(bot: object) -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()  # type: ignore[attr-defined]
        _engine = None


@plugin.command(
    "invite",
    help="Request channel invite by verifying credentials against the invite database",
    usage="!invite <username> <irc-key>  (send as /msg to the bot)",
)
async def cmd_invite(bot: object, trigger: Trigger) -> None:
    cfg: dict[str, object] = bot.plugin_config("invite")  # type: ignore[attr-defined]

    if not cfg.get("dsn"):
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick, "Invite system is not configured."
        )
        return

    if len(trigger.args) < 2:
        await bot.notice(trigger.nick, "Usage: !invite <username> <irc-key>")  # type: ignore[attr-defined]
        return

    username = trigger.args[0]
    irc_key = " ".join(trigger.args[1:])
    channel = str(cfg.get("channel", ""))
    table = str(cfg.get("table", "users"))
    user_col = str(cfg.get("user_col", "username"))
    key_col = str(cfg.get("key_col", "irckey"))

    if not channel:
        await bot.notice(trigger.nick, "No invite channel configured.")  # type: ignore[attr-defined]
        return

    if _engine is None:
        await bot.notice(trigger.nick, "Invite database is unavailable.")  # type: ignore[attr-defined]
        return

    try:
        from sqlalchemy import text

        async with _engine.connect() as conn:  # type: ignore[attr-defined]
            # table/column names come from admin config, not user input; value is parameterised
            sql = f"SELECT `{key_col}` FROM `{table}` WHERE `{user_col}` = :user"  # noqa: S608
            result = await conn.execute(text(sql), {"user": username})
            row = result.fetchone()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("invite: DB error: %s", exc)
        await bot.notice(trigger.nick, "Database error — please try again later.")  # type: ignore[attr-defined]
        return

    if row is None:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick, f"Username '{username}' not found."
        )
        return

    stored_key = str(row[0])
    if irc_key != stored_key:
        await bot.notice(trigger.nick, "Incorrect IRC key.")  # type: ignore[attr-defined]
        return

    await bot.notice(trigger.nick, f"Welcome! Inviting you to {channel}.")  # type: ignore[attr-defined]
    await bot.invite(trigger.nick, channel)  # type: ignore[attr-defined]
