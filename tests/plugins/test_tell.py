from __future__ import annotations

from dataclasses import replace

import pytest

from pybot.core.database import Tell, close_db, get_session, init_db
from pybot.core.irc import IRCMessage
from pybot.plugin import Trigger
from pybot.plugins import tell


class _DummyBot:
    def __init__(self, nick: str = "TestBot") -> None:
        self.nick = nick
        self.replies: list[str] = []
        self.said: list[tuple[str, str]] = []

    async def reply(self, _trigger: Trigger, message: str) -> None:
        self.replies.append(message)

    async def say(self, target: str, message: str) -> None:
        self.said.append((target, message))


@pytest.fixture(autouse=True)
async def setup_db() -> None:
    await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_db()


@pytest.fixture
def trigger() -> Trigger:
    return Trigger(
        bot=None,  # type: ignore[arg-type]
        message=IRCMessage.parse(":alice!u@h PRIVMSG #test :!tell bob hello there"),
        match=None,
        args=["bob", "hello", "there"],
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
async def test_tell_rejects_self_target(trigger: Trigger) -> None:
    bot = _DummyBot()
    self_trigger = replace(trigger, args=["alice", "hello"])

    await tell.cmd_tell(bot, self_trigger)

    assert bot.replies == ["You can't send a tell to yourself."]


@pytest.mark.asyncio
async def test_tell_rejects_bot_target(trigger: Trigger) -> None:
    bot = _DummyBot(nick="TestBot")
    bot_trigger = replace(trigger, args=["TestBot", "hello"])

    await tell.cmd_tell(bot, bot_trigger)

    assert bot.replies == ["You can't send a tell to me!"]


@pytest.mark.asyncio
async def test_tell_persists_message_and_confirms(trigger: Trigger) -> None:
    bot = _DummyBot()

    await tell.cmd_tell(bot, trigger)

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Tell).order_by(Tell.id.desc()))).scalar_one()

    assert row.from_nick == "alice"
    assert row.to_nick == "bob"
    assert row.channel == "#test"
    assert row.message == "hello there"
    assert bot.replies == ["I'll tell bob that when I see them."]


@pytest.mark.asyncio
async def test_deliver_tells_marks_messages_delivered(trigger: Trigger) -> None:
    bot = _DummyBot()
    await tell.cmd_tell(bot, trigger)

    await tell._deliver_tells(bot, "bob", "#test")

    assert len(bot.said) == 1
    target, message = bot.said[0]
    assert target == "#test"
    assert "bob:" in message
    assert "hello there" in message

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Tell).order_by(Tell.id.desc()))).scalar_one()

    assert row.delivered is True
    assert row.delivered_at is not None
