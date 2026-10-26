"""
Audit log: a record of administrative changes (who did what, when, from where).

    await audit("bob!b@host", "flag.grant", target="alice", details="+o #chan")

Pass an open session to record inside the same transaction as the change (so a rolled
back change leaves no entry); otherwise a new session is used. Recording never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.database import AuditEntry, get_session


def _entry(actor: str, action: str, target: str, details: str, source: str) -> AuditEntry:
    return AuditEntry(
        at=datetime.now(tz=timezone.utc),
        source=source,
        actor=actor[:256],
        action=action[:64],
        target=target[:256],
        details=details,
    )


async def audit(
    actor: str,
    action: str,
    target: str = "",
    details: str = "",
    source: str = "irc",
    session: AsyncSession | None = None,
) -> None:
    """Record one administrative action (actor: hostmask or web/partyline user name)."""
    entry = _entry(actor or "?", action, target, details, source)
    try:
        if session is not None:
            session.add(entry)
            return
        async with get_session() as new_session:
            new_session.add(entry)
    except Exception as exc:  # the action itself already happened; don't fail it
        logger.warning(f"Could not write audit entry {action} by {actor}: {exc}")


async def recent(limit: int = 20, actor: str = "", action: str = "") -> list[Any]:
    """Newest audit entries first, optionally filtered by actor/action substring."""
    async with get_session() as session:
        query = select(AuditEntry).order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
        if actor:
            query = query.where(AuditEntry.actor.ilike(f"%{actor}%"))
        if action:
            query = query.where(AuditEntry.action.ilike(f"{action}%"))
        result = await session.execute(query.limit(limit))
        return list(result.scalars().all())


def format_entry(entry: Any) -> str:
    stamp = entry.at.replace(tzinfo=timezone.utc) if entry.at.tzinfo is None else entry.at
    parts = [stamp.strftime("%Y-%m-%d %H:%M"), entry.actor, entry.action]
    if entry.target:
        parts.append(entry.target)
    text = " ".join(parts)
    if entry.details:
        text += f" ({entry.details})"
    return text if entry.source == "irc" else f"{text} [{entry.source}]"
