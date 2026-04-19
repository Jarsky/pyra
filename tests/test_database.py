"""Tests for database models and session management."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pybot.core.database import (
    Ban,
    SeenEntry,
    Tell,
    User,
    UserFlag,
    close_db,
    get_channel_setting,
    get_or_create_channel,
    get_session,
    init_db,
    set_channel_setting,
)


@pytest.fixture(autouse=True)
async def setup_db() -> None:
    await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_db()


async def test_create_user() -> None:
    async with get_session() as session:
        user = User(
            nick="TestUser",
            hostmask="testuser!~u@host.example.com",
            global_flags="v",
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(user)

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(User).where(User.nick == "TestUser"))
        u = result.scalar_one_or_none()
        assert u is not None
        assert u.global_flags == "v"
        assert u.hostmask == "testuser!~u@host.example.com"


async def test_user_flag_relationship() -> None:
    async with get_session() as session:
        user = User(
            nick="FlagUser",
            hostmask="flag!u@h.com",
            global_flags="",
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(user)
        await session.flush()
        flag = UserFlag(
            user_id=user.id,
            channel=None,
            flag="o",
            granted_by="setup",
            granted_at=datetime.now(tz=timezone.utc),
        )
        session.add(flag)

    async with get_session() as session:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(User).options(selectinload(User.flags)).where(User.nick == "FlagUser")
        )
        u = result.scalar_one()
        assert len(u.flags) == 1
        assert u.flags[0].flag == "o"


async def test_channel_and_settings() -> None:
    async with get_session() as session:
        ch = await get_or_create_channel(session, "#testchan")
        assert ch.name == "#testchan"
        ch_id = ch.id

    async with get_session() as session:
        ch = await get_or_create_channel(session, "#testchan")
        assert ch.id == ch_id  # idempotent

    async with get_session() as session:
        await set_channel_setting(session, "#testchan", "greet", "true")

    async with get_session() as session:
        val = await get_channel_setting(session, "#testchan", "greet")
        assert val == "true"

    async with get_session() as session:
        val = await get_channel_setting(session, "#testchan", "nonexistent", default="default")
        assert val == "default"


async def test_tell_crud() -> None:
    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        tell = Tell(
            from_nick="Alice",
            to_nick="Bob",
            channel="#general",
            message="Hey Bob!",
            created_at=now,
        )
        session.add(tell)

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Tell).where(Tell.to_nick == "Bob", Tell.delivered == False)  # noqa: E712
        )
        tells = result.scalars().all()
        assert len(tells) == 1
        assert tells[0].message == "Hey Bob!"

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(Tell).where(Tell.to_nick == "Bob"))
        tell = result.scalar_one()
        tell.delivered = True
        tell.delivered_at = datetime.now(tz=timezone.utc)

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Tell).where(Tell.to_nick == "Bob", Tell.delivered == False)  # noqa: E712
        )
        assert result.scalar_one_or_none() is None


async def test_seen_entry() -> None:
    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        entry = SeenEntry(
            nick="SomeUser",
            channel="#chat",
            action="said",
            message="hello world",
            seen_at=now,
        )
        session.add(entry)

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(SeenEntry).where(SeenEntry.nick == "SomeUser"))
        e = result.scalar_one()
        assert e.action == "said"
        assert e.message == "hello world"


async def test_ban_crud() -> None:
    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        ch = await get_or_create_channel(session, "#ban-test")
        ban = Ban(
            channel_id=ch.id,
            hostmask="*!*@spam.com",
            reason="Spam",
            set_by="admin!u@h",
            set_at=now,
            active=True,
        )
        session.add(ban)

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Ban).where(Ban.hostmask == "*!*@spam.com", Ban.active == True)  # noqa: E712
        )
        b = result.scalar_one()
        assert b.reason == "Spam"


async def test_session_rollback_on_error() -> None:
    """An exception inside get_session() should roll back automatically."""
    import contextlib

    with contextlib.suppress(ValueError):
        async with get_session() as session:
            user = User(
                nick="RollbackUser",
                hostmask="rb!u@h.com",
                global_flags="",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(user)
            await session.flush()
            raise ValueError("deliberate error")

    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(User).where(User.nick == "RollbackUser"))
        assert result.scalar_one_or_none() is None
