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
    match_hostmask,
    remove_flag,
)


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
        await add_flag(session, "Owner!u@h.com", "admin!u@h.com", "a")

    async with get_session() as session:
        assert await has_flag(session, "admin!u@h.com", "a")
        assert not await has_flag(session, "admin!u@h.com", "n")


async def test_channel_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, "Owner!u@h.com", "op!u@h.com", "o", channel="#test")

    async with get_session() as session:
        assert await has_flag(session, "op!u@h.com", "o", channel="#test")
        assert not await has_flag(session, "op!u@h.com", "o", channel="#other")
        assert not await has_flag(session, "op!u@h.com", "o")  # not global


async def test_remove_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, "Owner!u@h.com", "user!u@h.com", "v")

    async with get_session() as session:
        assert await has_flag(session, "user!u@h.com", "v")
        await remove_flag(session, "Owner!u@h.com", "user!u@h.com", "v")

    async with get_session() as session:
        assert not await has_flag(session, "user!u@h.com", "v")


async def test_ignored_user_denied() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, "Owner!u@h.com", "spammer!u@h.com", "I")

    async with get_session() as session:
        assert await is_ignored(session, "spammer!u@h.com")
        # Ignored user cannot pass any other flag check
        assert not await has_flag(session, "spammer!u@h.com", "v")


async def test_wildcard_hostmask_flag() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        # Grant voice to all users from *.trusted.net
        await add_flag(session, "Owner!u@h.com", "*!*@*.trusted.net", "v")

    async with get_session() as session:
        assert await has_flag(session, "anyone!x@mail.trusted.net", "v")
        assert not await has_flag(session, "other!x@untrusted.net", "v")


async def test_non_admin_cannot_grant() -> None:
    async with get_session() as session:
        with pytest.raises(PermissionError):
            await add_flag(session, "noone!u@h.com", "target!u@h.com", "v")


async def test_non_admin_cannot_grant_owner() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, "Owner!u@h.com", "admin!u@h.com", "a")

    async with get_session() as session:
        with pytest.raises(PermissionError, match="owner"):
            # admin trying to grant 'n' — not allowed
            await add_flag(session, "admin!u@h.com", "target!u@h.com", "n")


async def test_get_flags_returns_set() -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!u@h.com")

    async with get_session() as session:
        await add_flag(session, "Owner!u@h.com", "user!u@h.com", "v")
        await add_flag(session, "Owner!u@h.com", "user!u@h.com", "o", channel="#test")

    async with get_session() as session:
        flags_global = await get_flags(session, "user!u@h.com")
        assert "v" in flags_global
        assert "o" not in flags_global  # o is channel-specific

        flags_chan = await get_flags(session, "user!u@h.com", channel="#test")
        assert "v" in flags_chan
        assert "o" in flags_chan
