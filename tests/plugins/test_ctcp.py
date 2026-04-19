from __future__ import annotations

from types import SimpleNamespace

import pytest

from pybot.core.irc import IRCMessage
from pybot.plugins import ctcp


class _DummyBot:
    def __init__(self, config: dict | None = None) -> None:
        self._cfg = config or {}
        self.notices: list[tuple[str, str]] = []

    def plugin_config(self, name: str) -> dict:
        if name != "ctcp":
            return {}
        return self._cfg

    async def notice(self, target: str, text: str) -> None:
        self.notices.append((target, text))


@pytest.mark.asyncio
async def test_ctcp_version_replies() -> None:
    bot = _DummyBot({"enabled": True, "version_reply": "Pyra Test"})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h PRIVMSG TestBot :\x01VERSION\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == [("alice", "\x01VERSION Pyra Test\x01")]


@pytest.mark.asyncio
async def test_malformed_ctcp_privmsg_does_not_reply() -> None:
    bot = _DummyBot({"enabled": True})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h PRIVMSG TestBot :\x01PING 12345"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == []


@pytest.mark.asyncio
async def test_unknown_ctcp_is_ignored() -> None:
    bot = _DummyBot({"enabled": True})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h PRIVMSG TestBot :\x01FOOBAR hi\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == []


@pytest.mark.asyncio
async def test_ctcp_notice_is_ignored_to_prevent_loops() -> None:
    bot = _DummyBot({"enabled": True})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h NOTICE TestBot :\x01VERSION\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == []


@pytest.mark.asyncio
async def test_ping_payload_is_sanitized_and_bounded() -> None:
    bot = _DummyBot({"enabled": True})
    payload = "x" * 400 + "\r\n"
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(f":alice!u@h PRIVMSG TestBot :\x01PING {payload}\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert len(bot.notices) == 1
    target, text = bot.notices[0]
    assert target == "alice"
    assert text.startswith("\x01PING ")
    assert text.endswith("\x01")
    assert "\r" not in text
    assert "\n" not in text
    assert len(text) <= len("\x01PING \x01") + 160


@pytest.mark.asyncio
async def test_ping_with_tab_separator_still_replies() -> None:
    bot = _DummyBot({"enabled": True})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h PRIVMSG TestBot :\x01PING\t9876\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == [("alice", "\x01PING 9876\x01")]


@pytest.mark.asyncio
async def test_dcc_disabled_notice_uses_plain_reply() -> None:
    bot = _DummyBot({"enabled": True, "allow_dcc": False, "dcc_reply": "DCC disabled\ntry web"})
    trigger = SimpleNamespace(
        nick="alice",
        message=IRCMessage.parse(":alice!u@h PRIVMSG TestBot :\x01DCC CHAT chat 1 2\x01"),
    )

    await ctcp.on_privmsg_ctcp(bot, trigger)

    assert bot.notices == [("alice", "DCC disabled try web")]
