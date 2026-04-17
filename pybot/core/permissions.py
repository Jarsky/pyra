"""
Eggdrop-style flag-based ACL system.

Global flags:
  n = owner        (superuser, full access)
  a = admin        (bot-wide administration)
  o = op           (moderation commands)
  v = voice        (trusted user)
  b = bot          (bot-to-bot links)
  I = ignore       (bot ignores all input)
  X = exempt       (bypass flood/antispam)

Channel flags (per-channel overrides):
  o = chanop       (mod commands in that channel)
  v = voice        (voice commands in that channel)
  k = autokick     (bot kicks on join)
  b = banned       (bot enforces ban on join)

Resolution order:
  1. owner (n) — always full access
  2. globally ignored (I) — always rejected
  3. global flag
  4. channel-specific flag
  5. NickServ account match (if account provided)
"""

from __future__ import annotations

import fnmatch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pybot.core.database import User, UserFlag


# ---------------------------------------------------------------------------
# Hostmask matching
# ---------------------------------------------------------------------------


def match_hostmask(pattern: str, hostmask: str) -> bool:
    """Return True if hostmask matches pattern (fnmatch-style, case-insensitive).

    Examples:
        match_hostmask("*!*@*.example.com", "nick!user@a.b.example.com") -> True
        match_hostmask("nick!*@*", "nick!user@host") -> True
    """
    return fnmatch.fnmatch(hostmask.lower(), pattern.lower())


def _matches_any(patterns: list[str], hostmask: str, account: str | None = None) -> bool:
    """Return True if hostmask (or account) matches any pattern in patterns."""
    for pattern in patterns:
        # Account-based match: pattern like "account:accountname"
        if pattern.startswith("account:") and account:
            if fnmatch.fnmatch(account.lower(), pattern[8:].lower()):
                return True
        elif match_hostmask(pattern, hostmask):
            return True
    return False


# ---------------------------------------------------------------------------
# Flag queries
# ---------------------------------------------------------------------------


async def _get_user_records(
    session: AsyncSession, hostmask: str, account: str | None = None
) -> list[User]:
    """Return all User records whose hostmask pattern matches the given hostmask."""
    result = await session.execute(select(User))
    users = result.scalars().all()
    matched = []
    for user in users:
        if match_hostmask(user.hostmask, hostmask):
            matched.append(user)
        elif account and user.account and user.account.lower() == account.lower():
            matched.append(user)
    return matched


async def get_flags(
    session: AsyncSession,
    hostmask: str,
    channel: str | None = None,
    account: str | None = None,
) -> set[str]:
    """Return the full set of flags a hostmask has, optionally for a channel."""
    users = await _get_user_records(session, hostmask, account)
    flags: set[str] = set()

    for user in users:
        # Global flags from the user record
        flags.update(user.global_flags)

        # Per-flag records (both global and channel-specific)
        result = await session.execute(
            select(UserFlag).where(UserFlag.user_id == user.id)
        )
        for uf in result.scalars().all():
            if uf.channel is None:
                flags.add(uf.flag)
            elif channel and uf.channel.lower() == channel.lower():
                flags.add(uf.flag)

    return flags


async def has_flag(
    session: AsyncSession,
    hostmask: str,
    flag: str,
    channel: str | None = None,
    account: str | None = None,
) -> bool:
    """Return True if the hostmask holds the given flag (global or channel-specific)."""
    flags = await get_flags(session, hostmask, channel, account)

    # owner implies all flags
    if "n" in flags:
        return True

    # ignored users cannot pass any check
    if flag != "I" and "I" in flags:
        return False

    return flag in flags


async def is_ignored(
    session: AsyncSession,
    hostmask: str,
    account: str | None = None,
) -> bool:
    """Return True if this hostmask should be completely ignored."""
    # Check the Ignore table first
    from pybot.core.database import Ignore
    from datetime import datetime, timezone

    result = await session.execute(
        select(Ignore).where(Ignore.active == True)  # noqa: E712
    )
    now = datetime.now(tz=timezone.utc)
    for ignore in result.scalars().all():
        if ignore.expires_at and ignore.expires_at < now:
            continue
        if match_hostmask(ignore.hostmask, hostmask):
            return True

    return await has_flag(session, hostmask, "I", account=account)


# ---------------------------------------------------------------------------
# Flag management
# ---------------------------------------------------------------------------


async def add_flag(
    session: AsyncSession,
    admin_hostmask: str,
    target_hostmask: str,
    flag: str,
    channel: str | None = None,
    account: str | None = None,
) -> None:
    """Grant a flag to a hostmask. Creates a User record if needed."""
    from datetime import datetime, timezone

    # Verify the admin has the right to grant this flag
    admin_flags = await get_flags(session, admin_hostmask)
    if "n" not in admin_flags and "a" not in admin_flags:
        raise PermissionError("Only admins (a) or owners (n) can grant flags")
    if flag == "n" and "n" not in admin_flags:
        raise PermissionError("Only owners (n) can grant the owner flag")

    # Find or create the target user
    users = await _get_user_records(session, target_hostmask, account)
    if users:
        user = users[0]
    else:
        nick = target_hostmask.split("!")[0] if "!" in target_hostmask else target_hostmask
        user = User(
            nick=nick,
            hostmask=target_hostmask,
            account=account,
            global_flags="",
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(user)
        await session.flush()

    # Check for existing flag
    result = await session.execute(
        select(UserFlag).where(
            UserFlag.user_id == user.id,
            UserFlag.flag == flag,
            UserFlag.channel == channel,
        )
    )
    if result.scalar_one_or_none() is None:
        session.add(
            UserFlag(
                user_id=user.id,
                channel=channel,
                flag=flag,
                granted_by=admin_hostmask,
                granted_at=datetime.now(tz=timezone.utc),
            )
        )


async def remove_flag(
    session: AsyncSession,
    admin_hostmask: str,
    target_hostmask: str,
    flag: str,
    channel: str | None = None,
    account: str | None = None,
) -> bool:
    """Remove a flag from a hostmask. Returns True if the flag was removed."""
    admin_flags = await get_flags(session, admin_hostmask)
    if "n" not in admin_flags and "a" not in admin_flags:
        raise PermissionError("Only admins (a) or owners (n) can remove flags")

    users = await _get_user_records(session, target_hostmask, account)
    removed = False
    for user in users:
        result = await session.execute(
            select(UserFlag).where(
                UserFlag.user_id == user.id,
                UserFlag.flag == flag,
                UserFlag.channel == channel,
            )
        )
        uf = result.scalar_one_or_none()
        if uf:
            await session.delete(uf)
            removed = True
    return removed


async def add_owner_bootstrap(session: AsyncSession, nick: str, hostmask: str) -> None:
    """Create the initial owner user with no permission check (setup only)."""
    from datetime import datetime, timezone

    result = await session.execute(select(User).where(User.nick == nick))
    existing = result.scalar_one_or_none()
    if existing is None:
        user = User(
            nick=nick,
            hostmask=hostmask,
            global_flags="n",
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(user)
        await session.flush()
        session.add(
            UserFlag(
                user_id=user.id,
                channel=None,
                flag="n",
                granted_by="setup",
                granted_at=datetime.now(tz=timezone.utc),
            )
        )
