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

Identity on IRC comes from hostmask patterns and services accounts. A pattern that only
pins the nick ("nick!*@*") can be matched by anyone who takes that nick, so owner and
admin flags are refused for such patterns; use a user@host mask or a services account.

Users and ignores are cached in memory and the cache is dropped whenever a transaction
that touched them commits, so permission changes apply immediately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from pybot.core.database import Ignore, User, UserFlag

PRIVILEGED_FLAGS = frozenset("na")

# ---------------------------------------------------------------------------
# Hostmask matching
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1024)
def _mask_regex(pattern: str) -> re.Pattern[str]:
    # IRC masks only have * and ? wildcards; everything else (including [ ] in nicks) is literal
    body = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.compile(body, re.IGNORECASE | re.DOTALL)


def match_hostmask(pattern: str, hostmask: str) -> bool:
    """Return True if hostmask matches pattern (* and ? wildcards, case-insensitive).

    An empty pattern never matches.

    Examples:
        match_hostmask("*!*@*.example.com", "nick!user@a.b.example.com") -> True
        match_hostmask("nick!*@*", "nick!user@host") -> True
    """
    if not pattern:
        return False
    return _mask_regex(pattern).fullmatch(hostmask) is not None


def is_nick_only_mask(mask: str) -> bool:
    """True for patterns whose user and host parts are pure wildcards (e.g. "nick!*@*").

    Such a pattern identifies nobody: anyone can switch to that nick.
    """
    if "!" not in mask or "@" not in mask:
        return True
    user_host = mask.split("!", 1)[1]
    user, _, host = user_host.partition("@")
    return bool(re.fullmatch(r"[*?]*", user)) and bool(re.fullmatch(r"[*?]*", host))


# ---------------------------------------------------------------------------
# In-memory cache of users/flags and ignores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CachedUser:
    hostmask: str
    account: str | None
    global_flags: frozenset[str]
    channel_flags: tuple[tuple[str, str], ...]  # (lowercased channel, flag)


@dataclass(frozen=True)
class _CachedIgnore:
    hostmask: str
    expires_at: datetime | None


_generation = 0
_user_cache: tuple[Any, int, list[_CachedUser]] | None = None
_ignore_cache: tuple[Any, int, list[_CachedIgnore]] | None = None
_TRACKED = (User, UserFlag, Ignore)


def invalidate_cache() -> None:
    global _generation
    _generation += 1


@event.listens_for(Session, "after_flush")
def _note_permission_changes(session: Session, flush_context: Any) -> None:
    if any(isinstance(obj, _TRACKED) for obj in (*session.new, *session.dirty, *session.deleted)):
        session.info["pyra_permissions_dirty"] = True


@event.listens_for(Session, "after_commit")
def _drop_cache_on_commit(session: Session) -> None:
    if session.info.pop("pyra_permissions_dirty", False):
        invalidate_cache()


@event.listens_for(Session, "after_rollback")
def _forget_rolled_back_changes(session: Session) -> None:
    session.info.pop("pyra_permissions_dirty", None)


def _cache_key(session: AsyncSession) -> Any:
    # A new engine (e.g. a fresh test database) must not reuse another database's cache
    return session.bind


async def _cached_users(session: AsyncSession) -> list[_CachedUser]:
    global _user_cache
    key = _cache_key(session)
    if _user_cache and _user_cache[0] is key and _user_cache[1] == _generation:
        return _user_cache[2]
    generation = _generation
    result = await session.execute(select(User).options(selectinload(User.flags)))
    users = [
        _CachedUser(
            hostmask=u.hostmask or "",
            account=u.account.lower() if u.account else None,
            global_flags=frozenset(
                (u.global_flags or "") + "".join(f.flag for f in u.flags if f.channel is None)
            ),
            channel_flags=tuple((f.channel.lower(), f.flag) for f in u.flags if f.channel),
        )
        for u in result.scalars().all()
    ]
    _user_cache = (key, generation, users)
    return users


async def _cached_ignores(session: AsyncSession) -> list[_CachedIgnore]:
    global _ignore_cache
    key = _cache_key(session)
    if _ignore_cache and _ignore_cache[0] is key and _ignore_cache[1] == _generation:
        return _ignore_cache[2]
    generation = _generation
    result = await session.execute(select(Ignore).where(Ignore.active == True))  # noqa: E712
    ignores = [_CachedIgnore(i.hostmask, i.expires_at) for i in result.scalars().all()]
    _ignore_cache = (key, generation, ignores)
    return ignores


# ---------------------------------------------------------------------------
# Flag queries
# ---------------------------------------------------------------------------


async def get_flags(
    session: AsyncSession,
    hostmask: str,
    channel: str | None = None,
    account: str | None = None,
) -> set[str]:
    """Return the full set of flags an IRC identity has, optionally for a channel."""
    global_flags, channel_flags = await get_identity_flags(session, hostmask, channel, account)
    return global_flags | channel_flags


async def get_identity_flags(
    session: AsyncSession,
    hostmask: str,
    channel: str | None = None,
    account: str | None = None,
) -> tuple[set[str], set[str]]:
    """(global flags, flags for this channel) of an IRC identity, kept apart.

    Needed where a letter means different things: global 'b' is "bot", channel 'b' is
    "banned in this channel".
    """
    account_key = account.lower() if account else None
    channel_key = channel.lower() if channel else None
    global_flags: set[str] = set()
    channel_flags: set[str] = set()
    for user in await _cached_users(session):
        by_mask = match_hostmask(user.hostmask, hostmask)
        by_account = account_key is not None and user.account == account_key
        if not (by_mask or by_account):
            continue
        global_flags.update(user.global_flags)
        if channel_key:
            channel_flags.update(flag for ch, flag in user.channel_flags if ch == channel_key)
    return global_flags, channel_flags


def flags_allow(flags: set[str], flag: str) -> bool:
    """Apply the resolution rules (owner implies all; ignore denies) to a flag set."""
    if "n" in flags:
        return True
    if flag != "I" and "I" in flags:
        return False
    return flag in flags


async def has_flag(
    session: AsyncSession,
    hostmask: str,
    flag: str,
    channel: str | None = None,
    account: str | None = None,
) -> bool:
    """Return True if the IRC identity holds the given flag (global or channel-specific)."""
    return flags_allow(await get_flags(session, hostmask, channel, account), flag)


async def get_user_flags(session: AsyncSession, user: User, channel: str | None = None) -> set[str]:
    """Flags held by a specific user record (for web/partyline logins, not IRC identity)."""
    result = await session.execute(select(UserFlag).where(UserFlag.user_id == user.id))
    flags = set(user.global_flags or "")
    channel_key = channel.lower() if channel else None
    for uf in result.scalars().all():
        if uf.channel is None or (channel_key and uf.channel.lower() == channel_key):
            flags.add(uf.flag)
    return flags


async def user_has_flag(session: AsyncSession, user: User, flag: str) -> bool:
    return flags_allow(await get_user_flags(session, user), flag)


async def is_ignored(
    session: AsyncSession,
    hostmask: str,
    account: str | None = None,
) -> bool:
    """Return True if this hostmask should be completely ignored (owners never are)."""
    flags = await get_flags(session, hostmask, account=account)
    if "n" in flags:
        return False
    now = datetime.now(tz=timezone.utc)
    for ignore in await _cached_ignores(session):
        expires = ignore.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)  # SQLite drops the timezone
        if expires is not None and expires < now:
            continue
        if match_hostmask(ignore.hostmask, hostmask):
            return True
    return "I" in flags


# ---------------------------------------------------------------------------
# Flag management
# ---------------------------------------------------------------------------


def _same_channel(channel: str | None) -> Any:
    """SQL condition for a flag row's channel; IRC channel names are case-insensitive."""
    if channel is None:
        return UserFlag.channel.is_(None)
    return func.lower(UserFlag.channel) == channel.lower()


async def _check_can_change(
    session: AsyncSession, actor_flags: set[str], user: User | None, flag: str, verb: str
) -> None:
    if "n" not in actor_flags and "a" not in actor_flags:
        raise PermissionError(f"Only admins (a) or owners (n) can {verb} flags")
    if flag == "n" and "n" not in actor_flags:
        raise PermissionError(f"Only owners (n) can {verb} the owner flag")
    if user is not None and "n" not in actor_flags and "n" in await get_user_flags(session, user):
        raise PermissionError("Only owners (n) can change an owner's flags")


async def grant_flag(
    session: AsyncSession,
    actor_flags: set[str],
    user: User,
    flag: str,
    channel: str | None = None,
    granted_by: str = "",
) -> None:
    """Grant a flag to a user record, enforcing who may grant what."""
    await _check_can_change(session, actor_flags, user, flag, "grant")
    if flag in PRIVILEGED_FLAGS and not user.account and is_nick_only_mask(user.hostmask):
        raise PermissionError(
            f"Refusing +{flag} for '{user.hostmask}': anyone can take that nick. "
            "Use a nick!user@host mask or bind a services account."
        )
    result = await session.execute(
        select(UserFlag).where(
            UserFlag.user_id == user.id, UserFlag.flag == flag, _same_channel(channel)
        )
    )
    if result.scalars().first() is None:
        session.add(
            UserFlag(
                user_id=user.id,
                channel=channel,
                flag=flag,
                granted_by=granted_by,
                granted_at=datetime.now(tz=timezone.utc),
            )
        )


async def revoke_flag(
    session: AsyncSession,
    actor_flags: set[str],
    user: User,
    flag: str,
    channel: str | None = None,
) -> bool:
    """Remove a flag from a user record. Returns True if the flag was removed."""
    await _check_can_change(session, actor_flags, user, flag, "remove")
    removed = False
    result = await session.execute(
        select(UserFlag).where(
            UserFlag.user_id == user.id, UserFlag.flag == flag, _same_channel(channel)
        )
    )
    for uf in result.scalars().all():
        await session.delete(uf)
        removed = True
    if channel is None and flag in (user.global_flags or ""):
        user.global_flags = (user.global_flags or "").replace(flag, "")
        removed = True
    return removed


async def find_user_by_mask(session: AsyncSession, mask: str) -> User | None:
    """The user record registered with exactly this hostmask pattern (case-insensitive)."""
    result = await session.execute(select(User))
    for user in result.scalars().all():
        if (user.hostmask or "").lower() == mask.lower():
            return user
    return None


async def find_users_for_identity(
    session: AsyncSession, hostmask: str, account: str | None = None
) -> list[User]:
    """User records an IRC identity matches (by hostmask pattern or services account)."""
    account_key = account.lower() if account else None
    result = await session.execute(select(User))
    return [
        u
        for u in result.scalars().all()
        if match_hostmask(u.hostmask or "", hostmask)
        or (account_key is not None and (u.account or "").lower() == account_key)
    ]


async def add_flag(
    session: AsyncSession,
    actor_flags: set[str],
    target_hostmask: str,
    flag: str,
    channel: str | None = None,
    account: str | None = None,
    granted_by: str = "",
    nick: str | None = None,
) -> None:
    """Grant a flag to the user registered with target_hostmask, creating it if needed.

    A new user is named nick, or the mask's nick part.
    """
    await _check_can_change(session, actor_flags, None, flag, "grant")
    user = await find_user_by_mask(session, target_hostmask)
    if user is None:
        if not nick:
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
    await grant_flag(session, actor_flags, user, flag, channel, granted_by)


async def remove_flag(
    session: AsyncSession,
    actor_flags: set[str],
    target_hostmask: str,
    flag: str,
    channel: str | None = None,
) -> bool:
    """Remove a flag from the user registered with target_hostmask."""
    await _check_can_change(session, actor_flags, None, flag, "remove")
    user = await find_user_by_mask(session, target_hostmask)
    if user is None:
        return False
    return await revoke_flag(session, actor_flags, user, flag, channel)


async def add_owner_bootstrap(
    session: AsyncSession,
    nick: str,
    hostmask: str,
    password_hash: str = "",
    account: str | None = None,
) -> None:
    """Create the initial owner user with no permission check (setup only)."""
    result = await session.execute(select(User).where(User.nick == nick))
    existing = result.scalar_one_or_none()
    if existing is None:
        user = User(
            nick=nick,
            hostmask=hostmask,
            account=account or None,
            password_hash=password_hash,
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
