from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from pybot.core.irc import IRCMessage
from pybot.plugin import CommandHandler, Trigger
from pybot.plugins import help as help_plugin


class _DummyBot:
    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.config = SimpleNamespace(core=SimpleNamespace(command_prefix="!"))

    async def notice(self, target: str, message: str) -> None:
        self.notices.append((target, message))


@pytest.fixture
def trigger() -> Trigger:
    return Trigger(
        bot=None,  # type: ignore[arg-type]
        message=IRCMessage.parse(":alice!u@h PRIVMSG #test :!help"),
        match=None,
        args=[],
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
async def test_help_unknown_command_notices_user(
    trigger: Trigger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _DummyBot()
    fake_registry = SimpleNamespace(commands={})
    monkeypatch.setattr(help_plugin, "plugin", help_plugin.plugin)
    monkeypatch.setattr("pybot.plugin.get_registry", lambda: fake_registry)

    await help_plugin.cmd_help(bot, replace(trigger, args=["nosuchcmd"]))

    assert bot.notices == [("alice", "No help found for 'nosuchcmd'.")]


@pytest.mark.asyncio
async def test_help_specific_command_shows_usage_and_privilege(
    trigger: Trigger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _DummyBot()
    handler = CommandHandler(
        command="ops",
        func=lambda: None,
        plugin_name="demo",
        privilege="a",
        help_text="List ops",
        usage="!ops",
    )
    fake_registry = SimpleNamespace(commands={"ops": [handler]})
    monkeypatch.setattr("pybot.plugin.get_registry", lambda: fake_registry)

    await help_plugin.cmd_help(bot, replace(trigger, args=["ops"]))

    assert bot.notices == [
        ("alice", "\x02ops\x02"),
        ("alice", "List ops"),
        ("alice", "Usage: !ops"),
        ("alice", "Requires flag: a"),
    ]


@pytest.mark.asyncio
async def test_help_command_list_notices_chunks(
    trigger: Trigger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _DummyBot()
    fake_registry = SimpleNamespace(commands={"help": [], "ops": [], "seen": []})
    monkeypatch.setattr("pybot.plugin.get_registry", lambda: fake_registry)

    await help_plugin.cmd_help(bot, trigger)

    assert bot.notices[0] == ("alice", "Available commands:")
    assert bot.notices[-1] == ("alice", "Use '!help <command>' for details.")
    assert any("!help" in message for _, message in bot.notices)
    assert any("!ops" in message for _, message in bot.notices)
    assert any("!seen" in message for _, message in bot.notices)
