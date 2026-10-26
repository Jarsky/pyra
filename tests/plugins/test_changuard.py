"""Tests for channel enforcement (changuard), ban expiry, remembered channels and mode tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import (
    Ban,
    add_ban,
    close_db,
    get_session,
    remembered_channels,
    set_channel_setting,
)
from pybot.core.database import init_db as init_database
from pybot.core.irc import IRCMessage
from pybot.core.permissions import add_flag, add_owner_bootstrap
from pybot.plugins import changuard

parse = IRCMessage.parse


@pytest.fixture
async def bot(minimal_config_dict: dict) -> Any:
    await init_database("sqlite+aiosqlite:///:memory:")
    b = PyraBot(BotConfig.model_validate(minimal_config_dict))
    b.channels["#test"] = ChannelState(name="#test")
    b.channels["#test"].add_nick("TestBot").modes = {"o"}
    yield b
    await close_db()


def sent(b: PyraBot) -> list[str]:
    lines = []
    while not b.irc._flood_queue.empty():
        lines.append(b.irc._flood_queue.get_nowait())
    return lines


async def join(b: PyraBot, prefix: str, tags: str = "") -> None:
    """Run the core JOIN handling, then changuard's handler, for one user."""
    msg = parse(f"{tags}:{prefix} JOIN #test")
    await b._handle_join(msg)
    await changuard._on_join(b, await b._build_trigger(msg, [], None))


async def grant(mask: str, flags: str, channel: str | None = None) -> None:
    async with get_session() as session:
        for flag in flags:
            await add_flag(session, {"n"}, mask, flag, channel=channel)


async def setting(key: str, value: str) -> None:
    async with get_session() as session:
        await set_channel_setting(session, "#test", key, value)


# ---------------------------------------------------------------------------
# Join enforcement
# ---------------------------------------------------------------------------


async def test_stored_ban_is_enforced_on_join(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_ban(session, "#test", "*!*@bad.host", "spamming", "op!o@h")
    await join(bot, "spam!u@bad.host")
    assert sent(bot) == ["MODE #test +b *!*@bad.host", "KICK #test spam :spamming"]


async def test_global_ban_applies_in_every_channel(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_ban(session, None, "*!*@bad.host", "global", "op!o@h")
    await join(bot, "spam!u@bad.host")
    assert sent(bot)[-1] == "KICK #test spam :global"


async def test_expired_or_inactive_bans_are_not_enforced(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_ban(
            session,
            "#test",
            "*!*@bad.host",
            "old",
            "op",
            datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        )
        (await add_ban(session, "#test", "*!*@bad.host", "lifted", "op")).active = False
    await join(bot, "spam!u@bad.host")
    assert sent(bot) == []


async def test_nothing_happens_without_ops(bot: PyraBot) -> None:
    bot.channels["#test"].get_nick("TestBot").modes = set()  # type: ignore[union-attr]
    async with get_session() as session:
        await add_ban(session, "#test", "*!*@bad.host", "x", "op")
    await join(bot, "spam!u@bad.host")
    assert sent(bot) == []


async def test_autokick_flag_and_channel_ban_flag(bot: PyraBot) -> None:
    await grant("kicked!*@k.host", "k")
    await grant("chanbanned!*@b.host", "b", channel="#test")
    await grant("peerbot!*@bot.host", "b")  # global b means "bot", not banned
    await join(bot, "kicked!u@k.host")
    await join(bot, "chanbanned!u@b.host")
    await join(bot, "peerbot!u@bot.host")
    assert sent(bot) == [
        "MODE #test +b *!*@k.host",
        "KICK #test kicked :Autokick",
        "MODE #test +b *!*@b.host",
        "KICK #test chanbanned :Banned",
    ]


async def test_owners_and_ops_are_exempt(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "*!owner@bad.host")
        await add_ban(session, "#test", "*!*@bad.host", "x", "op")
    await grant("*!owner@bad.host", "k")
    await grant("helper!*@bad.host", "o", channel="#test")
    await join(bot, "Owner!owner@bad.host")
    await join(bot, "helper!h@bad.host")
    assert sent(bot) == []


async def test_autoop_and_autovoice(bot: PyraBot) -> None:
    await grant("alice!*@a.host", "o", channel="#test")
    await grant("bob!*@b.host", "v")
    await join(bot, "alice!a@a.host")
    await join(bot, "bob!b@b.host")
    assert sent(bot) == []  # both settings are off by default

    await setting("autoop", "true")
    await setting("autovoice", "true")
    await join(bot, "alice!a@a.host")
    await join(bot, "bob!b@b.host")
    assert sent(bot) == ["MODE #test +o alice", "MODE #test +v bob"]


async def test_autoop_by_services_account(bot: PyraBot) -> None:
    async with get_session() as session:
        from pybot.core.database import User

        session.add(User(nick="carol", hostmask="", account="carolacct"))
    async with get_session() as session:
        from pybot.core.permissions import find_users_for_identity, grant_flag

        (user,) = await find_users_for_identity(session, "x!y@z", "carolacct")
        await grant_flag(session, {"n"}, user, "o", "#test")
    await setting("autoop", "true")
    await join(bot, "carol!c@anywhere", tags="@account=carolacct ")
    assert sent(bot) == ["MODE #test +o carol"]


async def test_enforce_bans_setting_disables_kicks(bot: PyraBot) -> None:
    await grant("kicked!*@k.host", "k")
    await setting("enforce_bans", "false")
    await join(bot, "kicked!u@k.host")
    assert sent(bot) == []


async def test_sweep_when_bot_gets_opped_batches_modes(bot: PyraBot) -> None:
    state = bot.channels["#test"]
    state.get_nick("TestBot").modes = set()  # type: ignore[union-attr]
    for i in range(4):
        state.add_nick(f"v{i}", "u", f"v{i}.host")
        await grant(f"v{i}!*@v{i}.host", "v")
    state.add_nick("kicked", "u", "k.host")
    await grant("kicked!*@k.host", "k")
    await setting("autovoice", "true")

    msg = parse(":ChanServ!s@services MODE #test +o TestBot")
    await bot._handle_mode(msg)
    await changuard._on_mode(bot, await bot._build_trigger(msg, [], None))
    assert sent(bot) == [
        "MODE #test +b *!*@k.host",
        "KICK #test kicked :Autokick",
        "MODE #test +vvv v0 v1 v2",
        "MODE #test +v v3",
    ]


# ---------------------------------------------------------------------------
# Ban expiry
# ---------------------------------------------------------------------------


async def test_expired_bans_are_lifted_and_deactivated(bot: PyraBot) -> None:
    past = datetime.now(tz=timezone.utc) - timedelta(seconds=5)
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    bot.channels["#test"].bans.add("*!*@global.host")
    async with get_session() as session:
        await add_ban(session, "#test", "*!*@temp.host", "t", "op", past)
        await add_ban(session, "#test", "*!*@later.host", "t", "op", future)
        await add_ban(session, None, "*!*@global.host", "t", "op", past)
        await add_ban(session, "#elsewhere", "*!*@gone.host", "t", "op", past)  # bot not there

    await changuard.expire_bans(bot)

    assert sorted(sent(bot)) == ["MODE #test -b *!*@global.host", "MODE #test -b *!*@temp.host"]
    async with get_session() as session:
        active = {b.hostmask for b in (await session.execute(select(Ban))).scalars() if b.active}
    assert active == {"*!*@later.host"}


async def test_expired_ban_waits_for_ops(bot: PyraBot) -> None:
    bot.channels["#test"].get_nick("TestBot").modes = set()  # type: ignore[union-attr]
    async with get_session() as session:
        await add_ban(session, "#test", "*!*@temp.host", "t", "op", datetime.now(tz=timezone.utc))
    await changuard.expire_bans(bot)
    assert sent(bot) == []
    async with get_session() as session:
        assert (await session.execute(select(Ban))).scalar_one().active  # retried later


async def test_tempban_command_stores_expiry_without_timer(bot: PyraBot) -> None:
    from pybot.plugins import adminchannel

    replies: list[str] = []

    async def reply(_t: Any, text: str) -> None:
        replies.append(text)

    bot.reply = reply  # type: ignore[method-assign]
    bot.channels["#test"].add_nick("spam", "u", "bad.host")
    trigger = await bot._build_trigger(parse(":op!o@h PRIVMSG #test :!tempban spam 10m"), [], None)
    trigger.args = ["spam", "10m", "flooding"]
    await adminchannel.cmd_tempban(bot, trigger)

    async with get_session() as session:
        ban = (await session.execute(select(Ban))).scalar_one()
    assert ban.hostmask == "*!u@bad.host" and ban.reason == "flooding" and ban.expires_at


# ---------------------------------------------------------------------------
# Remembered channels
# ---------------------------------------------------------------------------


async def test_joined_channels_are_rejoined_after_restart(bot: PyraBot) -> None:
    await bot.join_channel("#Saved", "sekrit")
    await bot.join_channel("#test")  # also in config autojoin: not joined twice
    await bot.join_channel("#dropped")
    await bot.part_channel("#dropped", "bye")
    sent(bot)

    async with get_session() as session:
        assert await remembered_channels(session) == [("#saved", "sekrit"), ("#test", "")]
    await bot._join_remembered_channels()
    assert sent(bot) == ["JOIN #saved sekrit"]


async def test_channel_settings_alone_do_not_cause_joins(bot: PyraBot) -> None:
    await setting("greet", "true")  # creates a channel row, but not a remembered channel
    async with get_session() as session:
        assert await remembered_channels(session) == []


# ---------------------------------------------------------------------------
# Nick mode tracking
# ---------------------------------------------------------------------------


async def test_names_and_who_record_modes(bot: PyraBot) -> None:
    await bot.irc._on_005(parse(":s 005 TestBot PREFIX=(qaohv)~&@%+ :are supported"))
    bot.channels["#new"] = ChannelState(name="#new")
    await bot._handle_names(parse(":s 353 TestBot = #new :@+TestBot %half ~boss plain"))
    await bot._handle_end_of_names(parse(":s 366 TestBot #new :End"))
    state = bot.channels["#new"]
    assert state.get_nick("TestBot").modes == {"o", "v"}  # type: ignore[union-attr]
    assert state.get_nick("half").modes == {"h"}  # type: ignore[union-attr]
    assert state.get_nick("plain").modes == set()  # type: ignore[union-attr]
    assert bot.can_set_modes("#new")

    await bot._handle_who_reply(parse(":s 352 TestBot #new u h s plain H@ :0 Real"))
    await bot._handle_who_reply(parse(":s 352 TestBot #new u h s half G* :0 Real"))
    assert state.get_nick("plain").modes == {"o"}  # type: ignore[union-attr]
    assert state.get_nick("half").modes == set()  # type: ignore[union-attr]
