"""Tests for the selfauth extra plugin: identities instead of bare nicks."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from pybot.core import database
from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import close_db, get_session, init_db
from pybot.core.irc import IRCMessage

parse = IRCMessage.parse
PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins_extra" / "selfauth.py"


def _load_plugin() -> Any:
    import sys

    spec = importlib.util.spec_from_file_location("selfauth_under_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["selfauth_under_test"] = module  # as the loader does (SQLAlchemy needs it)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
async def env(minimal_config_dict: dict) -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    cfg = dict(minimal_config_dict)
    cfg["plugins"] = {**cfg["plugins"], "vars": {"selfauth": {"authorized": ["mgracct"]}}}
    bot = PyraBot(BotConfig.model_validate(cfg))
    state = bot.channels["#chan"] = ChannelState(name="#chan")
    state.add_nick("TestBot").modes = {"o"}
    state.add_nick("dave", "d", "dave.home", account="daveacct")
    state.add_nick("erin", "e", "erin.home")  # no services account
    bot.out: list[str] = []  # type: ignore[misc]

    async def record(target: str, text: str) -> None:
        bot.out.append(f"{target}: {text}")  # type: ignore[attr-defined]

    async def reply(trigger: Any, text: str) -> None:
        bot.out.append(f"reply: {text}")  # type: ignore[attr-defined]

    async def whois(nick: str) -> dict[str, str]:
        return {}

    bot.notice = record  # type: ignore[method-assign]
    bot.reply = reply  # type: ignore[method-assign]
    bot.whois = whois  # type: ignore[method-assign]
    plugin = _load_plugin()
    await plugin.setup(bot)
    yield bot, plugin
    await close_db()


async def _trigger(bot: PyraBot, prefix: str, text: str, tags: str = "") -> Any:
    msg = parse(f"{tags}:{prefix} PRIVMSG #chan :{text}")
    return await bot._build_trigger(msg, text.split()[1:], None)


def _modes(bot: PyraBot) -> list[str]:
    lines = []
    while not bot.irc._flood_queue.empty():
        lines.append(bot.irc._flood_queue.get_nowait())
    return lines


async def test_entries_store_identity_and_nick_spoofers_get_nothing(env: Any) -> None:
    bot, plugin = env
    manager = await _trigger(bot, "boss!b@x", "!self +op dave", tags="@account=mgracct ")
    await plugin.cmd_self(bot, manager)
    await plugin.cmd_self(bot, replace(manager, args=["+op", "erin"]))
    assert bot.out[-2:] == [
        "reply: dave [account daveacct] added to ops list.",
        "reply: erin [*!e@erin.home] added to ops list.",
    ]

    # While they're away, someone else takes the nicks "dave" (no account) and "erin"
    state = bot.channels["#chan"]
    real = {n: state.get_nick(n) for n in ("dave", "erin")}
    for nick in real:
        state.remove_nick(nick)
        state.add_nick(nick, "x", "evil")
    await plugin.cmd_ops(bot, await _trigger(bot, "dave!x@evil", "!ops"))
    await plugin.cmd_ops(bot, await _trigger(bot, "erin!x@evil", "!ops"))
    assert _modes(bot) == []
    for nick, member in real.items():
        state.nicks[nick] = member

    # The real people
    await plugin.cmd_ops(bot, await _trigger(bot, "dave!d@dave.home", "!ops"))
    await plugin.cmd_ops(bot, await _trigger(bot, "erin2!e@erin.home", "!ops"))
    assert _modes(bot) == ["MODE #chan +o dave", "MODE #chan +o erin2"]


async def test_managers_by_account_or_mask_not_nick(env: Any) -> None:
    bot, plugin = env
    impostor = await _trigger(bot, "mgracct!x@evil", "!self +op dave")  # nick, no account
    await plugin.cmd_self(bot, impostor)
    assert bot.out[-1] == "reply: You are not authorized to manage this list."

    bot.config.plugins.vars["selfauth"]["authorized"] = ["*!trusted@home.example", "nick!*@*"]
    by_mask = await _trigger(bot, "anyone!trusted@home.example", "!self +hop dave")
    await plugin.cmd_self(bot, by_mask)
    assert bot.out[-1] == "reply: dave [account daveacct] added to hops list."
    nick_only = await _trigger(bot, "nick!who@ever", "!self +hop erin")
    await plugin.cmd_self(bot, nick_only)
    assert bot.out[-1] == "reply: You are not authorized to manage this list."


async def test_legacy_nick_only_entries_no_longer_grant(env: Any) -> None:
    bot, plugin = env
    async with get_session() as session:
        await session.execute(
            text(
                "INSERT INTO selfauth (nick, channel, role, added_by) "
                "VALUES ('dave', '#chan', 'op', 'old')"
            )
        )
    state = bot.channels["#chan"]
    state.remove_nick("dave")
    state.add_nick("dave", "x", "evil")  # whoever uses the nick now
    await plugin.cmd_ops(bot, await _trigger(bot, "dave!x@evil", "!ops"))
    assert _modes(bot) == []
    assert "ask a manager to re-add you" in bot.out[-1]


async def test_refuses_nick_only_masks_and_unknown_people(env: Any) -> None:
    bot, plugin = env
    manager = await _trigger(bot, "boss!b@x", "!self +op x", tags="@account=mgracct ")
    await plugin.cmd_self(bot, replace(manager, args=["+op", "dave!*@*"]))
    await plugin.cmd_self(bot, replace(manager, args=["+op", "ghost"]))
    assert "would match anyone" in bot.out[-2]
    assert bot.out[-1].startswith("reply: Can't identify ghost")


async def test_needs_ops_to_grant(env: Any) -> None:
    bot, plugin = env
    bot.channels["#chan"].get_nick("TestBot").modes = set()
    manager = await _trigger(bot, "boss!b@x", "!self +op dave", tags="@account=mgracct ")
    await plugin.cmd_self(bot, manager)
    await plugin.cmd_ops(bot, await _trigger(bot, "dave!d@dave.home", "!ops"))
    assert _modes(bot) == []
    assert bot.out[-1] == "dave: I need ops in #chan to do that."


async def test_old_table_is_upgraded(minimal_config_dict: dict) -> None:
    await init_db("sqlite+aiosqlite:///:memory:")
    try:
        assert database._engine is not None
        async with database._engine.begin() as conn:  # the v2 table layout
            await conn.execute(text("DROP TABLE IF EXISTS selfauth"))
            await conn.execute(
                text(
                    "CREATE TABLE selfauth (id INTEGER PRIMARY KEY, nick VARCHAR(64), "
                    "channel VARCHAR(128), role VARCHAR(16), added_by VARCHAR(64))"
                )
            )
        plugin = _load_plugin()
        await plugin.setup(PyraBot(BotConfig.model_validate(minimal_config_dict)))
        await plugin.setup(None)  # a reload must not fail either
        async with database._engine.begin() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(selfauth)"))}
        assert {"account", "mask"} <= columns
    finally:
        await close_db()
