"""
SQLAlchemy 2.x async database layer.

Usage:
    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == "foo"))
        user = result.scalar_one_or_none()

Never store a session on the bot object or module-level globals.
Sessions are cheap and should be short-lived.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Protocol

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Engine / session factory (initialised by init_db())
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class _ModelWithTable(Protocol):
    __table__: Table


async def init_db(url: str, echo: bool = False) -> None:
    """Create the async engine and run schema creation."""
    global _engine, _session_factory

    _engine = create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create all tables (idempotent — Alembic handles migrations in production)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_plugin_tables(*model_types: type[_ModelWithTable]) -> None:
    """Create tables for plugin-defined models if they don't exist.

    Plugins call this from their setup() to auto-create tables on first load.
    Uses checkfirst=True so it is safe to call on every startup.

    Example::

        class MyTable(Base):
            __tablename__ = "myplugin_data"
            ...

        async def setup(bot):
            from pybot.core.database import ensure_plugin_tables
            await ensure_plugin_tables(MyTable)
    """
    if _engine is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    tables = [t.__table__ for t in model_types]
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables, checkfirst=True)


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for a DB session with auto-commit / rollback."""
    if _session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class User(Base):
    """A known IRC user (identified by hostmask and/or NickServ account)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nick: Mapped[str] = mapped_column(String(64), index=True)
    hostmask: Mapped[str] = mapped_column(String(256))  # may contain wildcards
    account: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    global_flags: Mapped[str] = mapped_column(String(32), default="")
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_where: Mapped[str | None] = mapped_column(String(128), nullable=True)

    flags: Mapped[list["UserFlag"]] = relationship(
        "UserFlag", back_populates="user", cascade="all, delete-orphan"
    )
    tells: Mapped[list["Tell"]] = relationship(
        "Tell",
        foreign_keys="Tell.to_nick",
        primaryjoin="User.nick == Tell.to_nick",
        viewonly=True,
    )
    notes: Mapped[list["Note"]] = relationship(
        "Note", back_populates="user", cascade="all, delete-orphan"
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        "Reminder", back_populates="user", cascade="all, delete-orphan"
    )
    hosts: Mapped[list["UserHost"]] = relationship(
        "UserHost", back_populates="user", cascade="all, delete-orphan"
    )

    def has_global_flag(self, flag: str) -> bool:
        return flag in self.global_flags

    @property
    def all_masks(self) -> list[str]:
        """Primary hostmask plus extra hosts (requires hosts to be loaded)."""
        return [m for m in [self.hostmask, *(h.mask for h in self.hosts)] if m]


class UserHost(Base):
    """An extra hostmask that identifies a user (Eggdrop's +host)."""

    __tablename__ = "user_hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    mask: Mapped[str] = mapped_column(String(256))

    user: Mapped["User"] = relationship("User", back_populates="hosts")


class AuditEntry(Base):
    """Who changed what: flags, users, hosts, bans, ignores, plugins, settings."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), index=True)
    source: Mapped[str] = mapped_column(String(16))  # irc | web | partyline | system
    actor: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(256), default="")
    details: Mapped[str] = mapped_column(Text, default="")


class UserFlag(Base):
    """Per-channel or global flag for a user."""

    __tablename__ = "user_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    channel: Mapped[str | None] = mapped_column(String(128), nullable=True)  # None = global
    flag: Mapped[str] = mapped_column(String(4))  # single char flag
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="flags")


class Channel(Base):
    """A channel the bot knows about."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    modes: Mapped[str] = mapped_column(String(64), default="")
    topic: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    settings: Mapped[list["ChannelSetting"]] = relationship(
        "ChannelSetting", back_populates="channel", cascade="all, delete-orphan"
    )
    bans: Mapped[list["Ban"]] = relationship(
        "Ban", back_populates="channel_obj", cascade="all, delete-orphan"
    )


class ChannelSetting(Base):
    """Key-value settings for a channel (e.g. greet_msg, antispam)."""

    __tablename__ = "channel_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)

    channel: Mapped["Channel"] = relationship("Channel", back_populates="settings")


class Ban(Base):
    """A channel or global ban entry."""

    __tablename__ = "bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("channels.id"), nullable=True, index=True
    )
    hostmask: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text, default="")
    set_by: Mapped[str] = mapped_column(String(64))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    channel_obj: Mapped["Channel | None"] = relationship("Channel", back_populates="bans")


class Ignore(Base):
    """A global ignore entry (bot ignores all input from matching hostmask)."""

    __tablename__ = "ignores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostmask: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text, default="")
    set_by: Mapped[str] = mapped_column(String(64))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SeenEntry(Base):
    """Records the last time a nick was seen in a channel."""

    __tablename__ = "seen_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nick: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))  # said, joined, parted, quit, kicked
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), index=True
    )


class Tell(Base):
    """A tell message — delivered when the target next speaks."""

    __tablename__ = "tells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_nick: Mapped[str] = mapped_column(String(64))
    to_nick: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Note(Base):
    """An admin note about a user."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    author_nick: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    tags: Mapped[str] = mapped_column(String(256), default="")

    user: Mapped["User"] = relationship("User", back_populates="notes")


class PluginSetting(Base):
    """Key-value settings for plugins (optionally per-channel)."""

    __tablename__ = "plugin_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plugin: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)


class Log(Base):
    """IRC event log."""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(128), index=True)
    nick: Mapped[str] = mapped_column(String(64))
    hostmask: Mapped[str] = mapped_column(String(256), default="")
    event_type: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), index=True
    )


class Reminder(Base):
    """A timed reminder for a user."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    nick: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="reminders")


class ScheduledTask(Base):
    """Persisted cron-style scheduled task."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plugin: Mapped[str] = mapped_column(String(64))
    function_name: Mapped[str] = mapped_column(String(128))
    cron_expr: Mapped[str] = mapped_column(String(64))
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class Karma(Base):
    """Karma score for an IRC nick."""

    __tablename__ = "karma"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nick: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    given_up: Mapped[int] = mapped_column(Integer, default=0)
    given_down: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


async def get_or_create_user_by_nick(
    session: AsyncSession, nick: str, hostmask: str = ""
) -> "User":
    from sqlalchemy import select

    result = await session.execute(select(User).where(User.nick == nick))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(nick=nick, hostmask=hostmask or f"{nick}!*@*")
        session.add(user)
        await session.flush()
    return user


async def get_or_create_channel(session: AsyncSession, name: str) -> Channel:
    from sqlalchemy import select

    result = await session.execute(select(Channel).where(Channel.name == name.lower()))
    ch = result.scalar_one_or_none()
    if ch is None:
        ch = Channel(name=name.lower())
        session.add(ch)
        await session.flush()
    return ch


async def get_channel_setting(
    session: AsyncSession, channel_name: str, key: str, default: str = ""
) -> str:
    from sqlalchemy import select

    result = await session.execute(
        select(ChannelSetting)
        .join(Channel)
        .where(Channel.name == channel_name.lower(), ChannelSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def set_channel_setting(
    session: AsyncSession, channel_name: str, key: str, value: str
) -> None:
    from sqlalchemy import select

    ch = await get_or_create_channel(session, channel_name)
    result = await session.execute(
        select(ChannelSetting).where(ChannelSetting.channel_id == ch.id, ChannelSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        session.add(ChannelSetting(channel_id=ch.id, key=key, value=value))


async def prune_logs(session: AsyncSession, keep_days: int) -> int:
    """Delete message log entries older than keep_days; returns how many were removed."""
    from datetime import timedelta, timezone

    from sqlalchemy import delete

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
    # Pure SQL delete: SQLite hands back naive datetimes, so in-Python syncing can't compare them
    result = await session.execute(
        delete(Log).where(Log.logged_at < cutoff).execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


_TRUE = ("true", "1", "yes", "on")


def setting_enabled(value: str) -> bool:
    return value.strip().lower() in _TRUE


async def remember_channel(session: AsyncSession, channel_name: str, key: str = "") -> None:
    """Mark a channel to be joined on every connect (stored as channel settings)."""
    await set_channel_setting(session, channel_name, "autojoin", "true")
    await set_channel_setting(session, channel_name, "join_key", key)


async def forget_channel(session: AsyncSession, channel_name: str) -> None:
    await set_channel_setting(session, channel_name, "autojoin", "false")


async def remembered_channels(session: AsyncSession) -> list[tuple[str, str]]:
    """(name, key) of channels marked with remember_channel."""
    from sqlalchemy import select

    result = await session.execute(
        select(Channel.name, ChannelSetting.key, ChannelSetting.value)
        .join(ChannelSetting)
        .where(ChannelSetting.key.in_(("autojoin", "join_key")))
        .order_by(Channel.name)
    )
    settings: dict[str, dict[str, str]] = {}
    for name, key, value in result.all():
        settings.setdefault(name, {})[key] = value
    return [
        (name, values.get("join_key", ""))
        for name, values in settings.items()
        if setting_enabled(values.get("autojoin", ""))
    ]


async def add_ban(
    session: AsyncSession,
    channel_name: str | None,
    hostmask: str,
    reason: str,
    set_by: str,
    expires_at: datetime | None = None,
) -> Ban:
    """Store a ban (channel_name None = global). The bot lifts it when it expires."""
    from datetime import timezone

    channel_id = (await get_or_create_channel(session, channel_name)).id if channel_name else None
    ban = Ban(
        channel_id=channel_id,
        hostmask=hostmask,
        reason=reason,
        set_by=set_by,
        set_at=datetime.now(tz=timezone.utc),
        expires_at=expires_at,
        active=True,
    )
    session.add(ban)
    return ban


async def get_plugin_setting(
    session: AsyncSession,
    plugin: str,
    key: str,
    channel: str | None = None,
    default: str = "",
) -> str:
    from sqlalchemy import select

    result = await session.execute(
        select(PluginSetting).where(
            PluginSetting.plugin == plugin,
            PluginSetting.key == key,
            PluginSetting.channel == channel,
        )
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def set_plugin_setting(
    session: AsyncSession,
    plugin: str,
    key: str,
    value: str,
    channel: str | None = None,
) -> None:
    from sqlalchemy import select

    result = await session.execute(
        select(PluginSetting).where(
            PluginSetting.plugin == plugin,
            PluginSetting.key == key,
            PluginSetting.channel == channel,
        )
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        session.add(PluginSetting(plugin=plugin, key=key, value=value, channel=channel))
