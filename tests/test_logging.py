from __future__ import annotations

import pytest

from pybot.core.bot import PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import Log, close_db, get_session, init_db
from pybot.core.irc import IRCMessage


@pytest.fixture(autouse=True)
async def setup_db() -> None:
    await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield
    await close_db()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("line", "expected_message"),
    [
        (":TestBot!u@h PRIVMSG NickServ :IDENTIFY supersecret", "IDENTIFY [REDACTED]"),
        (
            ":TestBot!u@h PRIVMSG AuthServ@services.undernet.org :AUTH user supersecret",
            "AUTH [REDACTED]",
        ),
        (":TestBot!u@h PRIVMSG Q@CServe.quakenet.org :AUTH user supersecret", "AUTH [REDACTED]"),
        (":TestBot!u@h PRIVMSG UserServ :LOGIN user supersecret", "LOGIN [REDACTED]"),
    ],
)
async def test_persist_log_entry_redacts_service_auth_payloads(
    minimal_config_dict: dict,
    line: str,
    expected_message: str,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._persist_log_entry(IRCMessage.parse(line))

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Log).order_by(Log.id.desc()))).scalar_one()

    assert row.message == expected_message


@pytest.mark.asyncio
async def test_persist_log_entry_keeps_non_sensitive_service_message(
    minimal_config_dict: dict,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._persist_log_entry(IRCMessage.parse(":TestBot!u@h PRIVMSG NickServ :STATUS TestBot"))

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Log).order_by(Log.id.desc()))).scalar_one()

    assert row.message == "STATUS TestBot"


@pytest.mark.asyncio
async def test_persist_log_entry_notice_is_persisted(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._persist_log_entry(IRCMessage.parse(":NickServ!s@h NOTICE TestBot :STATUS TestBot 3"))

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Log).order_by(Log.id.desc()))).scalar_one()

    assert row.event_type == "NOTICE"
    assert row.channel == "TestBot"
    assert row.message == "STATUS TestBot 3"


@pytest.mark.asyncio
async def test_persist_log_entry_nick_has_empty_channel(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._persist_log_entry(IRCMessage.parse(":oldnick!u@h NICK :newnick"))

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Log).order_by(Log.id.desc()))).scalar_one()

    assert row.event_type == "NICK"
    assert row.channel == ""
    assert row.message == "newnick"


@pytest.mark.asyncio
async def test_persist_log_entry_invite_uses_channel_param(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._persist_log_entry(IRCMessage.parse(":op!u@h INVITE alice :#chat"))

    async with get_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Log).order_by(Log.id.desc()))).scalar_one()

    assert row.event_type == "INVITE"
    assert row.channel == "#chat"
    assert row.message == "#chat"
