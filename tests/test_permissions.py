"""Tests for the flag-based ACL permission system."""

from __future__ import annotations

import pytest

from pybot.core.database import close_db, get_session, init_db
from pybot.core.permissions import (
    add_flag,
    add_owner_bootstrap,
    get_flags,
    has_flag,
    is_ignored,
    is_nick_only_mask,
    match_hostmask,
    remove_flag,
)

OWNER = {"n"}
ADMIN = {"a"}


@pytest.fixture(autouse=True)
async def setup_db() -> None:
    await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_db()


# ---------------------------------------------------------------------------
# Hostmask matching
# ---------------------------------------------------------------------------


def test_exact_match() -> None:
    assert match_hostmask("nick!user@host", "nick!user@host")


def test_wildcard_host() -> None:
    assert match_hostmask("*!*@*.example.com", "foo!bar@a.b.example.com")


def test_wildcard_no_match() -> None:
    assert not match_hostmask("*!*@*.example.com", "foo!bar@other.net")


def test_wildcard_nick() -> None:
    assert match_hostmask("*!user@host", "anynick!user@host")


def test_case_insensitive() -> None:
    assert match_hostmask("NICK!USER@HOST", "nick!user@host")


def test_full_wildcard() -> None:
    assert match_hostmask("*!*@*", "anyone!anything@anywhere")


# ---------------------------------------------------------------------------
# Flag queries
# ---------------------------------------------------------------------------


async def test_owner_has_all_flags() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        assert await has_flag(session, "Owner!u@h.com", "n")
        # owner implies all flags
        assert await has_flag(session, "Owner!u@h.com", "a")
        assert await has_flag(session, "Owner!u@h.com", "o")
        assert await has_flag(session, "Owner!u@h.com", "v")


async def test_no_flags_by_default() -> None:
    async with get_session() as session:
        assert not await has_flag(session, "stranger!u@host", "a")
        assert not await has_flag(session, "stranger!u@host", "o")


async def test_admin_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "admin!u@h.com", "a")

    async with get_session() as session:
        assert await has_flag(session, "admin!u@h.com", "a")
        assert not await has_flag(session, "admin!u@h.com", "n")


async def test_channel_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "op!u@h.com", "o", channel="#test")

    async with get_session() as session:
        assert await has_flag(session, "op!u@h.com", "o", channel="#test")
        assert not await has_flag(session, "op!u@h.com", "o", channel="#other")
        assert not await has_flag(session, "op!u@h.com", "o")  # not global


async def test_remove_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "user!u@h.com", "v")

    async with get_session() as session:
        assert await has_flag(session, "user!u@h.com", "v")
        await remove_flag(session, OWNER, "user!u@h.com", "v")

    async with get_session() as session:
        assert not await has_flag(session, "user!u@h.com", "v")


async def test_ignored_user_denied() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "spammer!u@h.com", "I")

    async with get_session() as session:
        assert await is_ignored(session, "spammer!u@h.com")
        # Ignored user cannot pass any other flag check
        assert not await has_flag(session, "spammer!u@h.com", "v")


async def test_wildcard_hostmask_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        # Grant voice to all users from *.trusted.net
        await add_flag(session, OWNER, "*!*@*.trusted.net", "v")

    async with get_session() as session:
        assert await has_flag(session, "anyone!x@mail.trusted.net", "v")
        assert not await has_flag(session, "other!x@untrusted.net", "v")


async def test_non_admin_cannot_grant() -> None:
    async with get_session() as session:
        with pytest.raises(PermissionError):
            await add_flag(session, set(), "target!u@h.com", "v")


async def test_non_admin_cannot_grant_owner() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "admin!u@h.com", "a")

    async with get_session() as session:
        with pytest.raises(PermissionError, match="owner"):
            # admin trying to grant 'n' — not allowed
            await add_flag(session, ADMIN, "target!u@h.com", "n")


async def test_get_flags_returns_set() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, OWNER, "user!u@h.com", "v")
        await add_flag(session, OWNER, "user!u@h.com", "o", channel="#test")

    async with get_session() as session:
        flags_global = await get_flags(session, "user!u@h.com")
        assert "v" in flags_global
        assert "o" not in flags_global  # o is channel-specific

        flags_chan = await get_flags(session, "user!u@h.com", channel="#test")
        assert "v" in flags_chan
        assert "o" in flags_chan


# ---------------------------------------------------------------------------
# Identity safety and owner protection
# ---------------------------------------------------------------------------


def test_empty_pattern_never_matches() -> None:
    assert not match_hostmask("", "")
    assert not match_hostmask("", "nick!user@host")


def test_brackets_in_nick_are_literal() -> None:
    assert match_hostmask("[Bot]!*@*", "[Bot]!u@h")
    assert not match_hostmask("[Bot]!*@*", "B!u@h")


def test_nick_only_mask_detection() -> None:
    assert is_nick_only_mask("nick!*@*")
    assert is_nick_only_mask("nick")
    assert not is_nick_only_mask("nick!*@host.example")
    assert not is_nick_only_mask("*!ident@*")


async def test_owner_with_empty_hostmask_matches_only_by_account() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "", account="OwnerAcct")

    async with get_session() as session:
        assert not await has_flag(session, "Owner!evil@attacker", "n")
        assert await has_flag(session, "Whatever!u@h", "n", account="owneracct")


async def test_privileged_flag_refused_for_nick_only_mask() -> None:
    async with get_session() as session:
        with pytest.raises(PermissionError, match="anyone can take that nick"):
            await add_flag(session, OWNER, "someone!*@*", "a")
        await add_flag(session, OWNER, "someone!*@*", "v")  # harmless flags are fine


async def test_admin_cannot_strip_owner() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        with pytest.raises(PermissionError, match="owner"):
            await remove_flag(session, ADMIN, "Owner!u@h.com", "n")
        with pytest.raises(PermissionError, match="owner"):
            await add_flag(session, ADMIN, "Owner!u@h.com", "I")

    async with get_session() as session:
        assert await has_flag(session, "Owner!u@h.com", "n")


async def test_owner_can_remove_global_owner_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")
    async with get_session() as session:
        assert await remove_flag(session, OWNER, "Owner!u@h.com", "n")
    async with get_session() as session:
        assert not await has_flag(session, "Owner!u@h.com", "n")


async def test_add_flag_targets_exact_mask_not_pattern_match() -> None:
    async with get_session() as session:
        await add_flag(session, OWNER, "*!*@*.example.com", "v")
        await add_flag(session, OWNER, "bob!b@host.example.com", "o")

    async with get_session() as session:
        flags = await get_flags(session, "carol!c@other.example.com")
        assert "v" in flags and "o" not in flags  # 'o' went to bob's own record


async def test_cache_sees_changes_immediately() -> None:
    async with get_session() as session:
        assert not await has_flag(session, "x!y@z", "v")
    async with get_session() as session:
        await add_flag(session, OWNER, "x!y@z", "v")
    async with get_session() as session:
        assert await has_flag(session, "x!y@z", "v")
    async with get_session() as session:
        await remove_flag(session, OWNER, "x!y@z", "v")
    async with get_session() as session:
        assert not await has_flag(session, "x!y@z", "v")


async def test_ignore_table_and_expiry() -> None:
    from datetime import datetime, timedelta, timezone

    from pybot.core.database import Ignore

    async with get_session() as session:
        session.add(Ignore(hostmask="*!*@spam.net", set_by="t", active=True))
        session.add(
            Ignore(
                hostmask="*!*@old.net",
                set_by="t",
                active=True,
                expires_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
            )
        )
        await add_owner_bootstrap(session, "Owner", "Owner!u@spam.net")

    async with get_session() as session:
        assert await is_ignored(session, "bot!x@spam.net")
        assert not await is_ignored(session, "bot!x@old.net")  # expired
        assert not await is_ignored(session, "Owner!u@spam.net")  # owners are never ignored
