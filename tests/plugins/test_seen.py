from __future__ import annotations

from dataclasses import replace

import pytest

from pybot.core.database import SeenEntry, close_db, get_session, init_db
from pybot.core.irc import IRCMessage
from pybot.plugin import Trigger
from pybot.plugins import seen


class _DummyBot:
    def __init__(self, nick: str = "TestBot") -> None:
        self.nick = nick
        self.said: list[tuple[str, str]] = []
        self.replies: list[str] = []

    async def say(self, target: str, message: str) -> None:
        self.said.append((target, message))

    async def reply(self, _trigger: Trigger, message: str) -> None:
        self.replies.append(message)


@pytest.fixture(autouse=True)
async def setup_db() -> None:
    await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_db()


@pytest.fixture
def trigger() -> Trigger:
    return Trigger(
        bot=None,  # type: ignore[arg-type]
        message=IRCMessage.parse(":alice!u@h PRIVMSG #test :!seen bob"),
        match=None,
        args=["bob"],
        channel="#test",
        nick="alice",
        user="u",
        host="h",
        account=None,
        hostmask="alice!u@h",
        is_pm=False,
        admin=False,
        owner=False,
    )


@pytest.mark.asyncio
async def test_seen_usage_when_no_args(trigger: Trigger) -> None:
    bot = _DummyBot()

    await seen.cmd_seen(bot, replace(trigger, args=[]))

    assert bot.replies == ["Usage: !seen <nick>"]


@pytest.mark.asyncio
async def test_seen_thats_me_and_thats_you(trigger: Trigger) -> None:
    bot = _DummyBot(nick="TestBot")

    await seen.cmd_seen(bot, replace(trigger, args=["TestBot"]))
    await seen.cmd_seen(bot, replace(trigger, args=["alice"]))

    assert bot.said == [
        ("#test", "That's me!"),
        ("#test", "alice: That's you!"),
    ]


@pytest.mark.asyncio
async def test_seen_reports_missing_nick(trigger: Trigger) -> None:
    bot = _DummyBot()

    await seen.cmd_seen(bot, trigger)

    assert bot.said == [("#test", "I haven't seen bob.")]


@pytest.mark.asyncio
async def test_seen_privmsg_event_records_entry(trigger: Trigger) -> None:
    await seen._on_privmsg(
        object(),
        replace(
            trigger,
            args=[],
            message=IRCMessage.parse(":alice!u@h PRIVMSG #test :hello there"),
        ),
    )

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(SeenEntry).order_by(SeenEntry.id.desc()))).scalar_one()

    assert row.nick == "alice"
    assert row.channel == "#test"
    assert row.action == "said"
    assert row.message == "hello there"


@pytest.mark.asyncio
async def test_seen_join_event_ignores_bot_and_records_users() -> None:
    bot = _DummyBot(nick="TestBot")

    bot_trigger = Trigger(
        bot=None,  # type: ignore[arg-type]
        message=IRCMessage.parse(":TestBot!u@h JOIN #test"),
        match=None,
        args=[],
        channel="#test",
        nick="TestBot",
        user="u",
        host="h",
        account=None,
        hostmask="TestBot!u@h",
        is_pm=False,
        admin=False,
        owner=False,
    )
    user_trigger = replace(
        bot_trigger,
        message=IRCMessage.parse(":bob!u@h JOIN #test"),
        nick="bob",
        hostmask="bob!u@h",
    )

    await seen._on_join(bot, bot_trigger)
    await seen._on_join(bot, user_trigger)

    async with get_session() as session:
        from sqlalchemy import select

        rows = (
            (await session.execute(select(SeenEntry).order_by(SeenEntry.id.asc()))).scalars().all()
        )

    assert len(rows) == 1
    assert rows[0].nick == "bob"
    assert rows[0].action == "joined"
    assert rows[0].channel == "#test"
