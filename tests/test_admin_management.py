"""IRC admin management: several hosts per user, channel masters, audit log, log retention,
compact command output."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from pybot.core.audit import recent
from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import Ignore, Log, User, close_db, get_session, init_db, prune_logs
from pybot.core.irc import IRCMessage
from pybot.core.permissions import (
    add_flag,
    add_host,
    add_owner_bootstrap,
    find_user_by_mask,
    get_flags,
    has_flag,
    remove_host,
)
from pybot.plugins import admin

parse = IRCMessage.parse


@pytest.fixture
async def bot(minimal_config_dict: dict) -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "*!owner@owner.host")
        session.add(User(nick="bob", hostmask="*!bob@bob.home"))
        session.add(User(nick="mia", hostmask="*!mia@mia.home"))
    b = PyraBot(BotConfig.model_validate(minimal_config_dict))
    b.channels["#chan"] = ChannelState(name="#chan")
    b.out: list[str] = []  # type: ignore[misc]

    async def reply(_t: Any, text: str) -> None:
        b.out.append(f"reply: {text}")  # type: ignore[attr-defined]

    async def notice(target: str, text: str) -> None:
        b.out.append(f"{target}: {text}")  # type: ignore[attr-defined]

    b.reply = reply  # type: ignore[method-assign]
    b.notice = notice  # type: ignore[method-assign]
    yield b
    await close_db()


async def _trigger(bot: PyraBot, prefix: str, *args: str, channel: str = "#chan") -> Any:
    msg = parse(f":{prefix} PRIVMSG {channel} :!cmd {' '.join(args)}")
    return await bot._build_trigger(msg, list(args), None)


OWNER = "Owner!owner@owner.host"


# ---------------------------------------------------------------------------
# Several hostmasks per user
# ---------------------------------------------------------------------------


async def test_extra_hosts_identify_the_user(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!bob@bob.home", "v")
        bob = await find_user_by_mask(session, "*!bob@bob.home")
        assert await add_host(session, {"n"}, bob, "*!bob@laptop.example", OWNER)
        assert not await add_host(session, {"n"}, bob, "*!BOB@laptop.example", OWNER)  # duplicate

    async with get_session() as session:
        assert "v" in await get_flags(session, "bob!bob@laptop.example")
        bob = await find_user_by_mask(session, "*!bob@laptop.example")  # by extra host too
        assert bob is not None and bob.nick == "bob"
        assert await remove_host(session, {"n"}, bob, "*!bob@laptop.example", OWNER)

    async with get_session() as session:
        assert "v" not in await get_flags(session, "bob!bob@laptop.example")


async def test_host_changes_are_protected(bot: PyraBot) -> None:
    async with get_session() as session:
        owner = await find_user_by_mask(session, "*!owner@owner.host")
        with pytest.raises(PermissionError, match="owner's hosts"):
            await add_host(session, {"a"}, owner, "*!evil@evil.example")
        with pytest.raises(PermissionError, match="anyone can take that nick"):
            await add_host(session, {"n"}, owner, "Owner!*@*")
        bob = await find_user_by_mask(session, "*!bob@bob.home")
        with pytest.raises(PermissionError):
            await add_host(session, set(), bob, "*!x@y")  # not an admin
        with pytest.raises(ValueError, match="not a hostmask"):
            await add_host(session, {"n"}, bob, "justanick")


async def test_host_command(bot: PyraBot) -> None:
    trigger = await _trigger(bot, OWNER, "add", "bob", "*!bob@laptop.example")
    await admin.cmd_host(bot, trigger)
    await admin.cmd_host(bot, replace(trigger, args=["list", "bob"]))
    await admin.cmd_host(bot, replace(trigger, args=["del", "bob", "*!bob@bob.home"]))
    assert bot.out == [  # type: ignore[attr-defined]
        "reply: Added *!bob@laptop.example. bob hosts: *!bob@bob.home, *!bob@laptop.example",
        "reply: bob hosts: *!bob@bob.home, *!bob@laptop.example",
        "reply: Removed *!bob@bob.home. bob hosts: *!bob@laptop.example",
    ]


# ---------------------------------------------------------------------------
# Channel masters
# ---------------------------------------------------------------------------


async def test_channel_master_manages_their_channel_only(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!mia@mia.home", "m", channel="#chan")
        await add_flag(session, {"n"}, "*!owner2@x", "a")  # an admin target
    async with get_session() as session:
        assert await has_flag(session, "mia!mia@mia.home", "o", "#chan")  # m implies o and v
        assert not await has_flag(session, "mia!mia@mia.home", "o", "#other")

    master = "mia!mia@mia.home"
    await admin.cmd_flags(bot, await _trigger(bot, master, "bob", "+ov"))  # own channel implied
    await admin.cmd_flags(bot, await _trigger(bot, master, "bob", "#other", "+o"))
    await admin.cmd_flags(bot, await _trigger(bot, master, "bob", "#chan", "+m"))
    await admin.cmd_flags(bot, await _trigger(bot, master, "*!owner2@x", "#chan", "+k"))
    out = bot.out  # type: ignore[attr-defined]
    assert out[0] == "reply: bob [*!bob@bob.home] flags on #chan: +ov (global none)"
    assert out[1] == "mia: Permission denied."  # not a master in #other
    assert "channel masters: bkov" in out[2]  # can't hand out m
    assert "admin's flags" in out[3]

    plain = await _trigger(bot, "nobody!n@n", "bob")
    await admin.cmd_flags(bot, plain)
    assert out[-1] == "nobody: Permission denied."


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_admin_actions_are_audited(bot: PyraBot) -> None:
    from pybot.plugins import adminchannel

    owner = await _trigger(bot, OWNER, "bob", "#chan", "+o")
    await admin.cmd_flags(bot, owner)
    await admin.cmd_flags(bot, replace(owner, args=["bob", "#chan", "-o"]))
    await admin.cmd_ignore(bot, replace(owner, args=["*!*@spam.example", "1h"]))
    await adminchannel._store_ban(bot, owner, "#chan", "*!*@bad.example", "spam")
    await admin.cmd_host(bot, replace(owner, args=["add", "bob", "*!bob@phone.example"]))

    entries = await recent(10)
    summary = [(e.action, e.target, e.details) for e in reversed(entries)]
    assert summary[0] == ("flag.grant", "bob", "+o #chan")
    assert summary[1] == ("flag.revoke", "bob", "-o #chan")
    assert summary[2][:2] == ("ignore.add", "*!*@spam.example")
    assert summary[3] == ("ban.add", "*!*@bad.example", "#chan: spam")
    assert summary[4] == ("host.add", "bob", "*!bob@phone.example")
    assert all(e.actor == OWNER and e.source == "irc" for e in entries)


async def test_refused_change_leaves_no_audit_entry(bot: PyraBot) -> None:
    await admin.cmd_flags(bot, await _trigger(bot, OWNER, "bob", "+vn-q"))  # fine...
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!adm@adm.home", "a")
    await admin.cmd_flags(bot, await _trigger(bot, "adm!adm@adm.home", "mia", "+vn"))  # n refused
    actions = [(e.action, e.target) for e in await recent(10)]
    assert ("flag.grant", "mia") not in actions  # the +v was rolled back with the refused +n


async def test_password_audit_has_no_password(bot: PyraBot) -> None:
    trigger = await _trigger(bot, OWNER, "bob", "s3cret-passw0rd", channel="TestBot")
    await admin.cmd_setpass(bot, trigger)
    (entry,) = await recent(1)
    assert entry.action == "user.password" and "s3cret" not in entry.details


async def test_audit_command(bot: PyraBot) -> None:
    owner = await _trigger(bot, OWNER, "bob", "+v")
    await admin.cmd_flags(bot, owner)
    await admin.cmd_audit(bot, replace(owner, args=["5", "flag."]))
    lines = [line for line in bot.out if line.startswith("Owner:")]  # type: ignore[attr-defined]
    assert lines[0] == "Owner: Last 1 admin actions (UTC, newest first):"
    assert lines[1].endswith(f"{OWNER} flag.grant bob (+v)")


# ---------------------------------------------------------------------------
# Message log retention
# ---------------------------------------------------------------------------


async def test_message_log_is_pruned_after_retention(bot: PyraBot) -> None:
    now = datetime.now(tz=timezone.utc)
    async with get_session() as session:
        for days in (1, 29, 31, 400):
            session.add(
                Log(
                    channel="#c",
                    nick="n",
                    event_type="PRIVMSG",
                    logged_at=now - timedelta(days=days),
                )
            )
    assert bot.config.database.log_retention_days == 30  # the default
    assert await bot.prune_message_log() == 2
    async with get_session() as session:
        remaining = (await session.execute(select(Log))).scalars().all()
        assert len(remaining) == 2
        assert await prune_logs(session, 30) == 0

    bot.config.database.log_retention_days = 0  # keep forever
    async with get_session() as session:
        session.add(
            Log(channel="#c", nick="n", event_type="PRIVMSG", logged_at=now - timedelta(days=999))
        )
    assert await bot.prune_message_log() == 0


# ---------------------------------------------------------------------------
# Compact output
# ---------------------------------------------------------------------------


async def test_whois_and_ignores_are_compact(bot: PyraBot) -> None:
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!bob@bob.home", "v")
        await add_flag(session, {"n"}, "*!bob@bob.home", "o", channel="#chan")
        bob = await find_user_by_mask(session, "*!bob@bob.home")
        await add_host(session, {"n"}, bob, "*!bob@laptop.example")
        for i in range(3):
            session.add(Ignore(hostmask=f"*!*@spam{i}.example", set_by="x", active=True))

    owner = await _trigger(bot, OWNER, "bob")
    await admin.cmd_whois(bot, owner)
    await admin.cmd_ignores(bot, owner)
    assert bot.out == [  # type: ignore[attr-defined]
        "Owner: bob: hosts *!bob@bob.home, *!bob@laptop.example",
        "Owner: Flags: global +v | #chan +o",
        "Owner: Ignores (3): *!*@spam0.example; *!*@spam1.example; *!*@spam2.example",
    ]
