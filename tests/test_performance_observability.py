from __future__ import annotations

import pytest

import pybot.core.bot as bot_module
from pybot.core.bot import PyraBot
from pybot.core.config import BotConfig
from pybot.core.irc import IRCMessage


@pytest.mark.asyncio
async def test_run_plugin_handler_logs_warning_when_slow(
    minimal_config_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)
    bot.slow_handler_warn_seconds = 1.0

    warnings: list[str] = []
    monkeypatch.setattr(bot_module.logger, "warning", lambda msg: warnings.append(str(msg)))

    ticks = iter([0.0, 1.25])
    monkeypatch.setattr(bot, "_monotonic", lambda: next(ticks))

    async def handler(_bot: PyraBot, _trigger: object) -> None:
        return None

    msg = IRCMessage.parse(":alice!u@h PRIVMSG #test :hello")
    await bot._run_plugin_handler(handler, msg, trigger=object())

    assert warnings
    assert "Slow handler:" in warnings[0]
    assert "plugin handler" in warnings[0]


@pytest.mark.asyncio
async def test_run_plugin_handler_no_warning_when_fast(
    minimal_config_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)
    bot.slow_handler_warn_seconds = 1.0

    warnings: list[str] = []
    monkeypatch.setattr(bot_module.logger, "warning", lambda msg: warnings.append(str(msg)))

    ticks = iter([10.0, 10.2])
    monkeypatch.setattr(bot, "_monotonic", lambda: next(ticks))

    async def handler(_bot: PyraBot, _trigger: object) -> None:
        return None

    msg = IRCMessage.parse(":alice!u@h PRIVMSG #test :hello")
    await bot._run_plugin_handler(handler, msg, trigger=object())

    assert warnings == []
