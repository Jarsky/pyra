"""
Public plugin API for Pyra.

Plugin authors import this module:
    from pybot import plugin

    @plugin.command("hello")
    async def hello(bot, trigger):
        await bot.reply(trigger, f"Hello {trigger.nick}!")

This module is the ONLY public interface between plugins and the bot core.
Do NOT import from pybot.core in plugins.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot
    from pybot.core.irc import IRCMessage


# ---------------------------------------------------------------------------
# Handler dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CommandHandler:
    command: str
    func: Callable[..., Any]
    plugin_name: str
    privilege: str | None = None  # flag required: "n", "a", "o", "v"
    channels: list[str] | None = None  # None = all channels
    aliases: list[str] = field(default_factory=list)
    help_text: str = ""
    usage: str = ""
    cooldown: float = 0.0  # seconds before the same user can run it again


@dataclass
class RuleHandler:
    pattern: re.Pattern[str]
    func: Callable[..., Any]
    plugin_name: str
    priority: int = 0


@dataclass
class EventHandler:
    event: str  # IRC command e.g. "JOIN", "PRIVMSG"
    func: Callable[..., Any]
    plugin_name: str
    priority: int = 0


@dataclass
class IntervalHandler:
    seconds: float | None
    cron: str | None
    func: Callable[..., Any]
    plugin_name: str


# ---------------------------------------------------------------------------
# Trigger object — passed to every handler
# ---------------------------------------------------------------------------


@dataclass
class Trigger:
    """Context object passed to every plugin handler."""

    bot: "PyraBot"
    message: "IRCMessage"
    match: re.Match[str] | None
    args: list[str]
    channel: str | None
    nick: str
    user: str
    host: str
    account: str | None
    hostmask: str  # nick!user@host
    is_pm: bool
    admin: bool  # has 'a' or 'n' flag
    owner: bool  # has 'n' flag

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def target(self) -> str:
        return self.channel or self.nick

    async def has_flag(self, flag: str, channel: str | None = None) -> bool:
        from pybot.core.database import get_session
        from pybot.core.permissions import has_flag as _has_flag

        async with get_session() as session:
            return await _has_flag(session, self.hostmask, flag, channel, account=self.account)


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------


class PluginRegistry:
    """Holds all registered plugin handlers across all loaded plugins."""

    def __init__(self) -> None:
        self.commands: dict[str, list[CommandHandler]] = {}
        self.rules: list[RuleHandler] = []
        self.events: dict[str, list[EventHandler]] = {}
        self.intervals: list[IntervalHandler] = []

    def clear_plugin(self, plugin_name: str) -> None:
        """Remove all handlers registered by a specific plugin."""
        for cmd in list(self.commands):
            self.commands[cmd] = [h for h in self.commands[cmd] if h.plugin_name != plugin_name]
            if not self.commands[cmd]:
                del self.commands[cmd]
        self.rules = [h for h in self.rules if h.plugin_name != plugin_name]
        for evt in list(self.events):
            self.events[evt] = [h for h in self.events[evt] if h.plugin_name != plugin_name]
            if not self.events[evt]:
                del self.events[evt]
        self.intervals = [h for h in self.intervals if h.plugin_name != plugin_name]

    def take_plugin(self, plugin_name: str) -> "PluginHandlers":
        """Remove and return all handlers of a plugin (see restore_plugin)."""
        taken = PluginHandlers(
            commands=[
                (name, h)
                for name, handlers in self.commands.items()
                for h in handlers
                if h.plugin_name == plugin_name
            ],
            rules=[h for h in self.rules if h.plugin_name == plugin_name],
            events=[
                h
                for handlers in self.events.values()
                for h in handlers
                if h.plugin_name == plugin_name
            ],
            intervals=[h for h in self.intervals if h.plugin_name == plugin_name],
        )
        self.clear_plugin(plugin_name)
        return taken

    def restore_plugin(self, handlers: "PluginHandlers") -> None:
        """Put back handlers previously removed with take_plugin."""
        for name, command_handler in handlers.commands:
            self.commands.setdefault(name, []).append(command_handler)
        self.rules.extend(handlers.rules)
        self.rules.sort(key=lambda h: h.priority, reverse=True)
        for event_handler in handlers.events:
            self.events.setdefault(event_handler.event, []).append(event_handler)
        self.intervals.extend(handlers.intervals)


@dataclass
class PluginHandlers:
    """Handlers belonging to one plugin, detached from the registry."""

    commands: list[tuple[str, CommandHandler]] = field(default_factory=list)
    rules: list[RuleHandler] = field(default_factory=list)
    events: list[EventHandler] = field(default_factory=list)
    intervals: list[IntervalHandler] = field(default_factory=list)


_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    return _registry


# ---------------------------------------------------------------------------
# Public decorators
# ---------------------------------------------------------------------------

# These are set by the plugin loader to the name of the plugin being loaded.
_current_plugin: str = "unknown"


def _set_current_plugin(name: str) -> None:
    global _current_plugin
    _current_plugin = name


def _loading() -> bool:
    """True while the plugin loader executes a plugin module.

    Decorators only register handlers then. Importing a plugin module any other way
    (e.g. one plugin importing a helper from another) must not register its commands a
    second time, under the wrong plugin name, so they would run twice.
    """
    return _current_plugin != "unknown"


# ---------------------------------------------------------------------------
# Shared helpers for plugins (import these rather than another plugin's module)
# ---------------------------------------------------------------------------

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> timedelta:
    """'30s', '10m', '2h', '1d', '1w' -> timedelta. Raises ValueError otherwise."""
    value = text.strip().lower()
    unit = _DURATION_UNITS.get(value[-1:]) if value else None
    if unit and value[:-1].isdigit() and int(value[:-1]) > 0:
        return timedelta(seconds=int(value[:-1]) * unit)
    raise ValueError(f"Invalid duration: {text!r}. Use e.g. 10m, 2h, 1d")


def format_age(seconds: int) -> str:
    """Compact age, e.g. '45s', '12m', '3h 5m', '2d 4h'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def command(
    name: str,
    *,
    privilege: str | None = None,
    channels: list[str] | None = None,
    aliases: list[str] | None = None,
    help: str = "",
    usage: str = "",
    cooldown: float = 0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a command handler. The decorated function receives (bot, trigger).

    cooldown: seconds before the same user may run this command again (owners/admins exempt).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not _loading():
            return func
        handler = CommandHandler(
            command=name,
            func=func,
            plugin_name=_current_plugin,
            privilege=privilege,
            channels=channels,
            aliases=aliases or [],
            help_text=help or (func.__doc__ or "").strip().split("\n")[0],
            usage=usage,
            cooldown=float(cooldown),
        )
        _registry.commands.setdefault(name, []).append(handler)
        for alias in handler.aliases:
            _registry.commands.setdefault(alias, []).append(handler)
        return func

    return decorator


def rule(
    pattern: str,
    *,
    priority: int = 0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a regex rule handler triggered on any matching message."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not _loading():
            return func
        _registry.rules.append(
            RuleHandler(
                pattern=re.compile(pattern),
                func=func,
                plugin_name=_current_plugin,
                priority=priority,
            )
        )
        _registry.rules.sort(key=lambda h: h.priority, reverse=True)
        return func

    return decorator


def event(
    irc_command: str,
    *,
    priority: int = 0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a handler for a raw IRC event (e.g. 'JOIN', 'PRIVMSG')."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not _loading():
            return func
        _registry.events.setdefault(irc_command.upper(), []).append(
            EventHandler(
                event=irc_command.upper(),
                func=func,
                plugin_name=_current_plugin,
                priority=priority,
            )
        )
        return func

    return decorator


def interval(
    schedule: float | str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a periodic task.

    `schedule` can be either:
    - float/int seconds
    - 5-field cron expression string (min hour dom month dow)
    """

    if isinstance(schedule, str):
        seconds: float | None = None
        cron: str | None = schedule.strip()
    else:
        seconds = float(schedule)
        cron = None

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not _loading():
            return func
        _registry.intervals.append(
            IntervalHandler(
                seconds=seconds,
                cron=cron,
                func=func,
                plugin_name=_current_plugin,
            )
        )
        return func

    return decorator
