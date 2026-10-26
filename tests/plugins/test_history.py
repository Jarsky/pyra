"""Tests for the !history plugin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import Log, close_db, get_session, init_db
from pybot.core.irc import IRCMessage
from pybot.plugins import history

parse = IRCMessage.parse


@pytest.fixture
async def bot(minimal_config_dict: dict) -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    b = PyraBot(BotConfig.model_validate(minimal_config_dict))
    b.channels["#test"] = ChannelState(name="#Test")
    b.channels["#test"].add_nick("alice", "a", "home")
    b.out: list[tuple[str, str, str]] = []  # type: ignore[misc]

    async def say(target: str, text: str) -> None:
        b.out.append(("say", target, text))  # type: ignore[attr-defined]

    async def notice(target: str, text: str) -> None:
        b.out.append(("notice", target, text))  # type: ignore[attr-defined]

    b.say = say  # type: ignore[method-assign]
    b.notice = notice  # type: ignore[method-assign]
    yield b
    await close_db()


async def _log(lines: list[tuple[str, str]], channel: str = "#Test") -> None:
    start = datetime.now(tz=timezone.utc) - timedelta(minutes=len(lines))
    async with get_session() as session:
        for i, (nick, text) in enumerate(lines):
            session.add(
                Log(
                    channel=channel,
                    nick=nick,
                    hostmask=f"{nick}!u@h",
                    event_type="PRIVMSG",
                    message=text,
                    logged_at=start + timedelta(minutes=i),
                )
            )
        session.add(Log(channel=channel, nick="bob", event_type="JOIN", logged_at=start))


async def _run(bot: PyraBot, line: str) -> None:
    msg = parse(line)
    trigger = await bot._build_trigger(msg, msg.text.split()[1:], None)
    await history.cmd_history(bot, trigger)


async def test_history_sends_recent_lines_oldest_first(bot: PyraBot) -> None:
    await _log([("bob", "hello"), ("carol", "\x01ACTION waves\x01"), ("bob", "how's things?")])
    await _log([("alice", "!history")])  # the request itself is logged before plugins run
    await _run(bot, ":alice!a@home PRIVMSG #test :!history")

    sent = [text for kind, target, text in bot.out if target == "alice"]  # type: ignore[attr-defined]
    assert sent[0] == "Last 3 messages in #test (times UTC):"
    assert sent[1].endswith("<bob> hello")
    assert sent[2].endswith("* carol waves")
    assert sent[3].endswith("<bob> how's things?")
    assert sent[4] == "End of #test history."
    assert all(kind == "say" for kind, _, _ in bot.out)  # type: ignore[attr-defined]


async def test_history_line_count_and_channel_argument(bot: PyraBot) -> None:
    await _log([("bob", f"line {i}") for i in range(10)])
    await _run(bot, ":alice!a@home PRIVMSG PyraBot :!history #TEST 3")
    sent = [text for _, _, text in bot.out]  # type: ignore[attr-defined]
    assert sent[0] == "Last 3 messages in #TEST (times UTC):"
    assert [s.split("> ")[1] for s in sent[1:4]] == ["line 7", "line 8", "line 9"]


async def test_history_only_for_people_in_the_channel(bot: PyraBot) -> None:
    await _log([("bob", "secret plans")])
    await _run(bot, ":mallory!m@evil PRIVMSG PyraBot :!history #test")
    assert bot.out == [  # type: ignore[attr-defined]
        ("notice", "mallory", "You need to be in #test to see its history.")
    ]


async def test_history_usage_and_empty_channel(bot: PyraBot) -> None:
    await _run(bot, ":alice!a@home PRIVMSG PyraBot :!history")
    await _run(bot, ":alice!a@home PRIVMSG #test :!history lots")
    await _run(bot, ":alice!a@home PRIVMSG #test :!history")
    assert [text for _, _, text in bot.out] == [  # type: ignore[attr-defined]
        "Usage: !history [#channel] [lines]",
        "Usage: !history [#channel] [lines]",
        "No messages logged in #test yet.",
    ]


async def test_history_respects_max_lines_setting(bot: PyraBot) -> None:
    bot.config.plugins.vars["history"] = {"max_lines": 5}
    await _log([("bob", f"line {i}") for i in range(20)])
    await _run(bot, ":alice!a@home PRIVMSG #test :!history 100")
    assert bot.out[0][2] == "Last 5 messages in #test (times UTC):"  # type: ignore[attr-defined]


def test_history_has_a_cooldown() -> None:
    import importlib.util
    from pathlib import Path

    from pybot import plugin

    path = Path(history.__file__)
    spec = importlib.util.spec_from_file_location("history_cooldown_check", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    plugin._set_current_plugin("history_cooldown_check")
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        (handler,) = plugin.get_registry().commands["history"]
        assert handler.cooldown == 60
    finally:
        plugin._set_current_plugin("unknown")
        plugin.get_registry().clear_plugin("history_cooldown_check")
