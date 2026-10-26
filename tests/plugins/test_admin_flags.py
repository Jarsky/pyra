"""Tests for the !flags command (show/change global and channel flags)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from pybot.core.database import User, close_db, get_session, init_db
from pybot.core.irc import IRCMessage
from pybot.core.permissions import add_flag, add_owner_bootstrap, get_flags
from pybot.plugin import Trigger
from pybot.plugins import admin


class _DummyBot:
    def __init__(self, whois: dict[str, str] | None = None) -> None:
        self.replies: list[str] = []
        self.private: list[tuple[str, str]] = []
        self._whois = whois or {}
        self.config = SimpleNamespace(web=SimpleNamespace(host="0.0.0.0", port=8080))  # noqa: S104

    async def reply(self, _trigger: Any, message: str) -> None:
        self.replies.append(message)

    async def whois(self, _nick: str) -> dict[str, str]:
        return self._whois

    async def say(self, target: str, message: str) -> None:
        self.private.append((target, message))

    notice = say


@pytest.fixture(autouse=True)
async def db() -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session() as session:
        await add_owner_bootstrap(session, "Owner", "Owner!o@owner.host")
        session.add(User(nick="bob", hostmask="bob!b@bob.host"))
    yield
    await close_db()


def _trigger(*args: str, owner: bool = False, admin_flag: bool = True) -> Trigger:
    return Trigger(
        bot=None,  # type: ignore[arg-type]
        message=IRCMessage.parse(":caller!c@host PRIVMSG #test :!flags"),
        match=None,
        args=list(args),
        channel="#test",
        nick="caller",
        user="c",
        host="host",
        account=None,
        hostmask="caller!c@host",
        is_pm=False,
        admin=admin_flag or owner,
        owner=owner,
    )


async def _flags(hostmask: str, channel: str | None = None) -> set[str]:
    async with get_session() as session:
        return await get_flags(session, hostmask, channel)


async def test_show_flags() -> None:
    bot = _DummyBot()
    await admin.cmd_flags(bot, _trigger("bob"))
    assert bot.replies == ["bob [bob!b@bob.host] flags (global): none"]


async def test_add_and_remove_global_and_channel_flags() -> None:
    bot = _DummyBot()
    await admin.cmd_flags(bot, _trigger("bob", "+ov"))
    assert await _flags("bob!b@bob.host") == {"o", "v"}

    await admin.cmd_flags(bot, _trigger("bob!b@bob.host", "#Chan", "+ko"))
    await admin.cmd_flags(bot, _trigger("bob", "#chan", "-o"))
    assert bot.replies[-1] == "bob [bob!b@bob.host] flags on #chan: +k (global +ov)"
    assert await _flags("bob!b@bob.host", "#chan") == {"k", "o", "v"}  # global o still applies

    await admin.cmd_flags(bot, _trigger("bob", "-o"))
    assert await _flags("bob!b@bob.host") == {"v"}


async def test_admin_cannot_touch_owner_or_grant_owner() -> None:
    bot = _DummyBot()
    await admin.cmd_flags(bot, _trigger("Owner", "-n"))
    await admin.cmd_flags(bot, _trigger("bob", "+n"))
    assert "Only owners" in bot.replies[0] and "Only owners" in bot.replies[1]
    assert "n" in await _flags("Owner!o@owner.host")
    assert "n" not in await _flags("bob!b@bob.host")


async def test_refused_flag_rolls_back_whole_change() -> None:
    bot = _DummyBot()
    await admin.cmd_flags(bot, _trigger("bob", "+vn"))  # v is fine, n is refused for an admin
    assert await _flags("bob!b@bob.host") == set()


async def test_owner_can_grant_admin_but_not_to_nick_only_mask() -> None:
    async with get_session() as session:
        await add_flag(session, {"n"}, "carol!*@*", "v")
    bot = _DummyBot()
    await admin.cmd_flags(bot, _trigger("bob", "+a", owner=True))
    await admin.cmd_flags(bot, _trigger("carol", "+a", owner=True))
    assert "a" in await _flags("bob!b@bob.host")
    assert "anyone can take that nick" in bot.replies[1]


async def test_bad_usage_and_unknown_or_ambiguous_users() -> None:
    async with get_session() as session:
        session.add(User(nick="bob", hostmask="bob!other@elsewhere"))
    bot = _DummyBot()
    trigger = _trigger("nobody")
    await admin.cmd_flags(bot, trigger)
    await admin.cmd_flags(bot, replace(trigger, args=["bob"]))
    await admin.cmd_flags(bot, replace(trigger, args=["bob!b@bob.host", "ov"]))
    assert bot.replies[0].startswith("No user named 'nobody'")
    assert bot.replies[1].startswith("Several users are named 'bob'")
    assert bot.replies[2].startswith("Usage:")


async def test_adduser_needs_a_name_for_wildcard_nick_masks() -> None:
    bot = _DummyBot()
    await admin.cmd_adduser(bot, _trigger("*!bad@kick.host", "k", owner=True))
    assert "give the user a name" in bot.replies[0]

    await admin.cmd_adduser(bot, _trigger("*!bad@kick.host", "k", "kicky", owner=True))
    assert bot.replies[-1] == "Added user kicky [*!bad@kick.host] with flags: k"
    assert bot.private == []  # no login for users without a/n
    async with get_session() as session:
        from pybot.core.permissions import find_user_by_mask

        user = await find_user_by_mask(session, "*!bad@kick.host")
        assert user is not None and user.nick == "kicky" and not user.password_hash


async def test_adduser_admin_login_goes_to_verified_user_only() -> None:
    verified = _DummyBot(whois={"user": "d", "host": "dave.host"})
    await admin.cmd_adduser(verified, _trigger("dave!d@dave.host", "a", owner=True))
    assert {target for target, _ in verified.private} == {"dave"}

    impostor = _DummyBot(whois={"user": "x", "host": "elsewhere"})
    await admin.cmd_adduser(impostor, _trigger("erin!e@erin.host", "a", owner=True))
    assert {target for target, _ in impostor.private} == {"caller"}  # sent to the admin
