"""Flood protection, channel mode policing (chanprotect), keepnick, partyline user commands."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import Ban, Ignore, User, close_db, get_session, init_db
from pybot.core.database import set_channel_setting as _set
from pybot.core.irc import IRCMessage
from pybot.core.partyline import execute_partyline_command
from pybot.core.permissions import add_flag, add_owner_bootstrap, get_flags
from pybot.plugins import chanprotect

parse = IRCMessage.parse


@pytest.fixture
async def bot(minimal_config_dict: dict) -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    chanprotect._settings_cache.clear()
    chanprotect._events.clear()
    b = PyraBot(BotConfig.model_validate(minimal_config_dict))
    state = b.channels["#chan"] = ChannelState(name="#chan")
    state.add_nick("TestBot").modes = {"o"}
    yield b
    for task in chanprotect._limit_tasks.values():
        task.cancel()
    chanprotect._limit_tasks.clear()
    await close_db()


def sent(b: PyraBot) -> list[str]:
    lines = []
    while not b.irc._flood_queue.empty():
        lines.append(b.irc._flood_queue.get_nowait())
    return lines


async def setting(key: str, value: str) -> None:
    async with get_session() as session:
        await _set(session, "#chan", key, value)
    chanprotect._settings_cache.clear()


async def event(b: PyraBot, line: str, handler: Any) -> None:
    msg = parse(line)
    for internal in b._internal_handlers.get(msg.command, []):
        await internal(msg)
    await handler(b, await b._build_trigger(msg, [], None))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_rate_and_chanmode() -> None:
    assert chanprotect.parse_rate("5:60") == (5, 60.0)
    assert chanprotect.parse_rate("0") is None and chanprotect.parse_rate("0:10") is None
    assert chanprotect.parse_chanmode("+ntk sekrit -i") == (
        {"n": None, "t": None, "k": "sekrit"},
        {"i"},
    )
    assert chanprotect.parse_chanmode("+nt+l") == (
        {"n": None, "t": None},
        set(),
    )  # +l needs a number


# ---------------------------------------------------------------------------
# Floods
# ---------------------------------------------------------------------------


async def test_join_flood_kickbans_with_timed_ban(bot: PyraBot) -> None:
    for i in range(5):
        await event(bot, f":spam{i}!x@flood.host JOIN #chan", chanprotect._on_join)
    assert sent(bot)[-2:] == ["MODE #chan +b *!*@flood.host", "KICK #chan spam4 :Join flood"]
    async with get_session() as session:
        ban = (await session.execute(select(Ban))).scalar_one()
    assert ban.hostmask == "*!*@flood.host" and ban.expires_at is not None


async def test_flood_exemptions_and_no_ops(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!friend@friend.host", "v")
    for i in range(6):
        await event(bot, f":f{i}!friend@friend.host JOIN #chan", chanprotect._on_join)
    assert sent(bot) == []

    bot.channels["#chan"].get_nick("TestBot").modes = set()  # type: ignore[union-attr]
    for i in range(6):
        await event(bot, f":s{i}!x@flood.host JOIN #chan", chanprotect._on_join)
    assert sent(bot) == []


async def test_nick_flood(bot: PyraBot) -> None:
    bot.channels["#chan"].add_nick("n0", "x", "nicky.host")
    for i in range(5):
        await event(bot, f":n{i}!x@nicky.host NICK :n{i + 1}", chanprotect._on_nick)
    assert sent(bot)[-1] == "KICK #chan n5 :Nick flood"


async def test_ctcp_floods(bot: PyraBot) -> None:
    for _ in range(3):
        await event(bot, ":c!x@ctcp.host PRIVMSG #chan :\x01VERSION\x01", chanprotect._on_privmsg)
    assert sent(bot)[-1] == "KICK #chan c :Ctcp flood"

    for _ in range(5):  # CTCPs to the bot itself: temporary ignore
        await event(bot, ":p!x@pm.host PRIVMSG TestBot :\x01PING 1\x01", chanprotect._on_privmsg)
    async with get_session() as session:
        ignore = (await session.execute(select(Ignore))).scalar_one()
    assert ignore.hostmask == "*!*@pm.host" and ignore.expires_at is not None


async def test_flood_setting_can_be_disabled(bot: PyraBot) -> None:
    await setting("flood_join", "0")
    for i in range(8):
        await event(bot, f":s{i}!x@flood.host JOIN #chan", chanprotect._on_join)
    assert sent(bot) == []


# ---------------------------------------------------------------------------
# Auto limit
# ---------------------------------------------------------------------------


async def test_autolimit(bot: PyraBot) -> None:
    state = bot.channels["#chan"]
    for nick in ("a", "b", "c"):
        state.add_nick(nick)
    await chanprotect.apply_limit(bot, "#chan")
    assert sent(bot) == []  # off by default

    await setting("autolimit", "3")
    await chanprotect.apply_limit(bot, "#chan")
    assert sent(bot) == ["MODE #chan +l 7"]  # 4 users + 3
    state.limit = 7
    state.add_nick("d")
    await chanprotect.apply_limit(bot, "#chan")
    assert sent(bot) == []  # drift of 1: left alone
    state.add_nick("e")
    await chanprotect.apply_limit(bot, "#chan")
    assert sent(bot) == ["MODE #chan +l 9"]


# ---------------------------------------------------------------------------
# chanmode, bitch, protectops
# ---------------------------------------------------------------------------


async def test_chanmode_reverts_and_is_applied_on_op(bot: PyraBot) -> None:
    await setting("chanmode", "+nt-i")
    await event(bot, ":op!o@h MODE #chan -t+i", chanprotect._on_mode)
    assert sent(bot) == ["MODE #chan +t-i"]

    bot.channels["#chan"].get_nick("TestBot").modes = set()  # type: ignore[union-attr]
    bot.channels["#chan"].modes = "i"
    await event(bot, ":ChanServ!s@services MODE #chan +o TestBot", chanprotect._on_mode)
    assert sorted(sent(bot)[0].split()[2]) == sorted("+nt-i")


async def test_bitch_mode(bot: PyraBot) -> None:
    state = bot.channels["#chan"]
    state.add_nick("rando", "r", "rando.host")
    state.add_nick("trusted", "t", "trusted.host")
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!t@trusted.host", "o", channel="#chan")
    await setting("bitch", "true")
    await event(bot, ":op!o@h MODE #chan +oo rando trusted", chanprotect._on_mode)
    assert sent(bot) == ["MODE #chan -o rando"]


async def test_protectops(bot: PyraBot) -> None:
    state = bot.channels["#chan"]
    state.add_nick("trusted", "t", "trusted.host").modes = {"o"}
    state.add_nick("boss", "b", "boss.host")
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!t@trusted.host", "o", channel="#chan")
        await add_flag(session, {"n"}, "*!b@boss.host", "m", channel="#chan")
    await setting("protectops", "true")
    await event(bot, ":rando!r@rando.host MODE #chan -o trusted", chanprotect._on_mode)
    assert sent(bot) == ["MODE #chan +o trusted"]
    await event(bot, ":boss!b@boss.host MODE #chan -o trusted", chanprotect._on_mode)
    assert sent(bot) == []  # a master may deop


# ---------------------------------------------------------------------------
# Keepnick
# ---------------------------------------------------------------------------


async def test_keepnick(bot: PyraBot) -> None:
    bot._current_nick = "TestBot_"
    await bot._handle_ison(parse(":s 303 TestBot_ :TestBot"))
    assert sent(bot) == []  # still taken
    await bot._handle_ison(parse(":s 303 TestBot_ :"))
    assert sent(bot) == ["NICK TestBot"]
    await bot._handle_monitor_offline(parse(":s 731 TestBot_ :TestBot"))
    await bot._handle_quit(parse(":TestBot!x@y QUIT :bye"))
    assert sent(bot) == ["NICK TestBot", "NICK TestBot"]

    bot.config.core.keepnick = False
    await bot._handle_ison(parse(":s 303 TestBot_ :"))
    assert sent(bot) == []


async def test_nick_in_use_after_registration_keeps_current_nick(bot: PyraBot) -> None:
    raw: list[str] = []

    async def send_raw(line: str) -> None:
        raw.append(line)

    bot.irc.send_raw = send_raw  # type: ignore[method-assign]
    bot.irc._registered = True
    await bot.irc._on_433(parse(":s 433 TestBot_ TestBot :Nickname is already in use"))
    assert raw == []


# ---------------------------------------------------------------------------
# Partyline user commands
# ---------------------------------------------------------------------------


async def _party(bot: PyraBot, line: str, owner: bool = True) -> list[str]:
    out: list[str] = []

    async def send(text: str) -> None:
        out.append(text.rstrip("\n"))

    await execute_partyline_command(
        bot=bot,
        actor="Boss",
        line=line,
        send=send,
        is_owner=owner,
        admin_count=lambda: 1,
        channel_names=lambda: ["#chan"],
        line_ending="\n",
    )
    return out


async def test_partyline_user_commands(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Boss", "*!boss@boss.home")

    assert await _party(bot, ".+user carol *!carol@home.example") == [
        "Added user carol [*!carol@home.example] (no flags; use .chattr)"
    ]
    assert await _party(bot, ".chattr carol +ov #chan") == [
        "carol flags on #chan (incl. global): +ov"
    ]
    assert await _party(bot, ".+host carol *!carol@phone.example") == [
        "Added *!carol@phone.example. carol hosts: *!carol@home.example, *!carol@phone.example"
    ]
    async with get_session() as session:
        assert "o" in await get_flags(session, "c!carol@phone.example", "#chan")

    assert (await _party(bot, ".match *phone*"))[-1] == "1 match(es)"
    assert (await _party(bot, ".whois carol"))[0].startswith("carol: hosts *!carol@home.example")
    assert await _party(bot, ".chattr Boss -n", owner=False) == [
        "Only owners (n) can remove the owner flag"
    ]
    assert await _party(bot, ".-user carol", owner=False) == ["Only owners can delete users"]
    assert await _party(bot, ".-user carol") == ["Deleted user carol"]
    async with get_session() as session:
        assert (await session.execute(select(User).where(User.nick == "carol"))).first() is None

    from pybot.core.audit import recent

    sources = {e.source for e in await recent(20)}
    assert sources == {"partyline"}


async def test_partyline_chanset_chaninfo_status(bot: PyraBot) -> None:
    assert await _party(bot, ".chanset #chan +bitch") == ["#chan: bitch = 'true'"]
    assert await _party(bot, ".chanset #chan flood_join 3:30") == ["#chan: flood_join = '3:30'"]
    info = (await _party(bot, ".chaninfo #chan"))[0]
    assert (
        "bitch='true';" in info and "flood_join='3:30';" in info and "protectops='false'*" in info
    )
    status = await _party(bot, ".status")
    assert status[0].startswith("Pyra: nick TestBot on irc.example.com:6697")
    assert status[1] == "Channels: #chan (1, op)"
    help_text = "\n".join(await _party(bot, ".help"))
    assert ".chattr" in help_text and ".status" in help_text
