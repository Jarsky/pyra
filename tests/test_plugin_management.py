"""Plugin management: remembered load/unload, per-channel switches, new files, conflicts,
command cooldowns and rate limits, and typed plugin settings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from pybot.core.bot import PyraBot
from pybot.core.config import BotConfig, save_plugin_vars
from pybot.core.database import close_db, init_db
from pybot.core.irc import IRCMessage
from pybot.core.plugin_loader import PluginLoader, PluginLoadError
from pybot.core.plugin_schema import MASK, form_fields, masked, merge_settings, parse_form
from pybot.plugin import CommandHandler, get_registry

parse = IRCMessage.parse


@pytest.fixture
async def db() -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    yield
    await close_db()


def _bot_stub(enabled: Any = "all", disabled: list[str] | None = None) -> MagicMock:
    bot = MagicMock()
    bot.config.plugins.enabled = enabled
    bot.config.plugins.disabled = disabled or []
    bot.scheduler = None
    return bot


def _write(directory: Path, name: str, command: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(
        f"from pybot import plugin\n@plugin.command('{command}')\nasync def f(b, t): pass\n"
    )
    return path


async def _unload_all(loader: PluginLoader) -> None:
    if loader._watch_task:
        loader._watch_task.cancel()
    for name in list(loader.get_loaded_plugins()):
        await loader.unload(name)


# ---------------------------------------------------------------------------
# Remembered load/unload
# ---------------------------------------------------------------------------


async def test_unload_and_load_are_remembered_across_restarts(db: None, tmp_path: Path) -> None:
    _write(tmp_path, "alpha", "alpha")
    _write(tmp_path, "beta", "beta")
    first = PluginLoader(_bot_stub(disabled=["beta"]))
    await first.load_all([tmp_path])
    assert first.is_loaded("alpha") and not first.is_loaded("beta")

    await first.disable("alpha")
    await first.enable("beta")  # overrides plugins.disabled in config
    await _unload_all(first)

    restarted = PluginLoader(_bot_stub(disabled=["beta"]))
    await restarted.load_all([tmp_path])
    assert not restarted.is_loaded("alpha") and restarted.is_loaded("beta")
    await _unload_all(restarted)


async def test_admin_plugin_cannot_be_disabled(db: None) -> None:
    loader = PluginLoader(_bot_stub())
    with pytest.raises(PluginLoadError, match="can't be unloaded"):
        await loader.disable("admin")


# ---------------------------------------------------------------------------
# New files and conflicts
# ---------------------------------------------------------------------------


async def test_new_plugin_files_are_picked_up(tmp_path: Path) -> None:
    loader = PluginLoader(_bot_stub(enabled=["gamma"]))
    await loader.load_all([tmp_path])
    _write(tmp_path, "gamma", "gamma")
    _write(tmp_path, "delta", "delta")  # not in plugins.enabled: registered but not loaded
    await loader.rescan()
    assert loader.is_loaded("gamma")
    assert not loader.is_loaded("delta") and "delta" in loader.get_available_plugins()
    await _unload_all(loader)


async def test_duplicate_commands_and_replaced_plugins_are_reported(tmp_path: Path) -> None:
    core, extra = tmp_path / "core", tmp_path / "extra"
    _write(core, "one", "same")
    _write(core, "dup", "dupcmd")
    _write(extra, "two", "same")
    _write(extra, "dup", "dupcmd2")
    loader = PluginLoader(_bot_stub())
    await loader.load_all([core, extra])
    warnings = loader.get_warnings()
    assert warnings["one"] == ["!same is also provided by two"]
    assert warnings["two"] == ["!same is also provided by one"]
    assert "replaces the plugin of the same name" in warnings["dup"][0]
    await _unload_all(loader)


# ---------------------------------------------------------------------------
# Dispatch: per-channel switches, cooldowns, rate limits
# ---------------------------------------------------------------------------


@pytest.fixture
async def bot(db: None, minimal_config_dict: dict) -> Any:
    b = PyraBot(BotConfig.model_validate(minimal_config_dict))
    b.notices: list[tuple[str, str]] = []  # type: ignore[misc]

    async def notice(target: str, text: str) -> None:
        b.notices.append((target, text))  # type: ignore[attr-defined]

    b.notice = notice  # type: ignore[method-assign]
    yield b


async def _settle() -> None:
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=5)


def _register(name: str, plugin_name: str, calls: list[str], **kw: Any) -> None:
    async def handler(_bot: Any, trigger: Any) -> None:
        calls.append(trigger.channel or "pm")

    handler_obj = CommandHandler(name, handler, plugin_name, **kw)
    get_registry().commands.setdefault(name, []).append(handler_obj)
    for alias in handler_obj.aliases:
        get_registry().commands.setdefault(alias, []).append(handler_obj)


async def test_plugin_switched_off_in_one_channel(bot: PyraBot) -> None:
    calls: list[str] = []
    _register("pmtest", "pmplugin", calls)
    try:
        await bot.set_plugin_enabled_in_channel("pmplugin", "#Test", False)
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #test :!pmtest"))
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #other :!pmtest"))
        await _settle()
        assert calls == ["#other"]

        bot.disabled_plugin_channels.clear()
        await bot.load_plugin_channel_states()  # stored in the database
        assert ("pmplugin", "#test") in bot.disabled_plugin_channels
    finally:
        get_registry().clear_plugin("pmplugin")


async def test_command_cooldown_is_per_user_and_shared_by_aliases(bot: PyraBot) -> None:
    calls: list[str] = []
    _register("slow", "cdplugin", calls, aliases=["sl"], cooldown=60)
    try:
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #test :!slow"))
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #test :!sl"))
        await bot._dispatch_to_plugins(parse(":b!v@other PRIVMSG #test :!slow"))
        await _settle()
        assert len(calls) == 2
        assert bot.notices[0][0] == "a" and "cooldown" in bot.notices[0][1]  # type: ignore[attr-defined]
    finally:
        get_registry().clear_plugin("cdplugin")


async def test_command_rate_limit_with_single_warning(bot: PyraBot) -> None:
    calls: list[str] = []
    _register("fast", "rlplugin", calls)
    try:
        for _ in range(8):
            await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #test :!fast"))
        await _settle()
        assert len(calls) == 5  # command_rate_limit default
        assert [n for n in bot.notices if "Slow down" in n[1]] == [  # type: ignore[attr-defined]
            ("a", "Slow down: too many commands. Try again shortly.")
        ]
    finally:
        get_registry().clear_plugin("rlplugin")


async def test_owner_is_exempt_from_limits(bot: PyraBot, minimal_config_dict: dict) -> None:
    calls: list[str] = []
    _register("own", "exempt", calls, cooldown=60)
    bot.config.core.owner_account = "boss"
    try:
        for _ in range(7):
            await bot._dispatch_to_plugins(parse("@account=boss :x!y@z PRIVMSG #test :!own"))
        await _settle()
        assert len(calls) == 7
    finally:
        get_registry().clear_plugin("exempt")


# ---------------------------------------------------------------------------
# Typed plugin settings
# ---------------------------------------------------------------------------

SCHEMA = {
    "api_key": {"type": "str", "secret": True},
    "units": {"type": "choice", "choices": ["metric", "imperial"], "default": "metric"},
    "max": {"type": "int", "default": 5, "min": 1, "max": 10},
    "domains": {"type": "list"},
    "on": {"type": "bool", "default": True},
}


def test_parse_form_coerces_and_keeps_secrets() -> None:
    current = {"api_key": "sekrit", "max": 7, "feeds": {"x": "y"}}
    form = {"api_key": "", "units": "imperial", "max": "", "domains": "a.com\nb.com, c.com"}
    values = merge_settings(current, parse_form(SCHEMA, form, current))
    assert values == {
        "api_key": "sekrit",  # blank secret keeps the saved one
        "units": "imperial",
        "domains": ["a.com", "b.com", "c.com"],
        "on": False,  # unticked checkbox
        "feeds": {"x": "y"},  # not in the schema: untouched
    }  # blank number removed -> default


def test_parse_form_reports_all_invalid_fields() -> None:
    with pytest.raises(ValueError) as err:
        parse_form(SCHEMA, {"units": "kelvin", "max": "50"}, {})
    assert "units must be one of metric, imperial" in str(err.value)
    assert "max must be at most 10" in str(err.value)
    with pytest.raises(ValueError, match="max must be a whole number"):
        parse_form(SCHEMA, {"units": "metric", "max": "lots"}, {})


def test_secrets_are_never_shown() -> None:
    fields = {f["name"]: f for f in form_fields(SCHEMA, {"api_key": "sekrit"})}
    assert fields["api_key"]["value"] == "" and fields["api_key"]["is_set"]
    assert masked({"api_key": "sekrit", "dsn": "mysql://u:p@h", "units": "metric"}, SCHEMA) == {
        "api_key": MASK,
        "dsn": MASK,  # secret by name even without a schema
        "units": "metric",
    }


def test_save_plugin_vars_replaces_keys(tmp_path: Path, minimal_config_dict: dict) -> None:
    path = tmp_path / "config.yaml"
    cfg = dict(minimal_config_dict)
    cfg["plugins"] = {**cfg["plugins"], "vars": {"weather": {"units": "imperial", "old": 1}}}
    path.write_text(yaml.safe_dump(cfg))
    config = BotConfig.model_validate(cfg)

    config = save_plugin_vars(path, config, "weather", {"units": "metric"})
    assert config.plugins.vars["weather"] == {"units": "metric"}
    assert yaml.safe_load(path.read_text())["plugins"]["vars"]["weather"] == {"units": "metric"}


def test_bundled_plugin_schemas_are_valid() -> None:
    import importlib.util

    from pybot.core.plugin_schema import FIELD_TYPES

    root = Path(__file__).resolve().parents[1]
    for path in [*root.glob("pybot/plugins/*.py"), *root.glob("plugins_extra/*.py")]:
        source = path.read_text(encoding="utf-8")
        if "__plugin_config__" not in source:
            continue
        spec = importlib.util.spec_from_file_location(f"schema_check_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        for name, field in module.__plugin_config__.items():
            assert field.get("type", "str") in FIELD_TYPES, (path.name, name)
            assert field.get("type") != "choice" or field.get("choices"), (path.name, name)


async def test_plugin_switch_and_list_commands(bot: PyraBot, tmp_path: Path) -> None:
    from dataclasses import replace

    from pybot.plugins import admin

    _write(tmp_path, "weatherish", "wx")
    _write(tmp_path, "idle", "idle")
    bot.plugin_loader = PluginLoader(_bot_stub(enabled=["weatherish"]))
    await bot.plugin_loader.load_all([tmp_path])
    replies: list[str] = []

    async def reply(_t: Any, text: str) -> None:
        replies.append(text)

    bot.reply = reply  # type: ignore[method-assign]
    base = await bot._build_trigger(parse(":op!o@h PRIVMSG #test :!plugin"), [], None)
    try:
        await admin.cmd_plugin(bot, replace(base, args=["off", "weatherish"]))
        await admin.cmd_plugin(bot, replace(base, args=["off", "admin"]))
        await admin.cmd_plugin(bot, replace(base, args=["off", "nosuch"]))
        await admin.cmd_plugin(bot, replace(base, args=["off", "weatherish", "#elsewhere"]))
        assert replies == [
            "Plugin 'weatherish' is now off in #test.",
            "The admin plugin can't be switched off.",
            "Unknown plugin 'nosuch'.",
            "You need the o flag in #elsewhere.",
        ]
        assert ("weatherish", "#test") in bot.disabled_plugin_channels

        await admin.cmd_plugins(bot, base)
        lines = [text for target, text in bot.notices if target == "op"]  # type: ignore[attr-defined]
        assert lines[:3] == [
            "Loaded (1): weatherish",
            "Not loaded (1): idle",
            "Off in #test: weatherish",
        ]
    finally:
        await _unload_all(bot.plugin_loader)
