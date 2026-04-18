"""
Main bot class — orchestrates all subsystems and provides the plugin API.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from pybot.core.config import BotConfig
from pybot.core.irc import IRCConnection, IRCMessage

if TYPE_CHECKING:
    from pybot.core.database import AsyncSession
    from pybot.core.partyline import PartylineServer
    from pybot.core.plugin_loader import PluginLoader
    from pybot.core.scheduler import Scheduler


# ---------------------------------------------------------------------------
# In-memory channel and nick state
# ---------------------------------------------------------------------------


@dataclass
class NickState:
    nick: str
    user: str = ""
    host: str = ""
    account: str | None = None
    modes: set[str] = field(default_factory=set)  # o, v, etc.

    @property
    def hostmask(self) -> str:
        if self.user and self.host:
            return f"{self.nick}!{self.user}@{self.host}"
        return self.nick


@dataclass
class ChannelState:
    name: str
    topic: str = ""
    modes: str = ""
    nicks: dict[str, NickState] = field(default_factory=dict)  # lowercased nick key

    def get_nick(self, nick: str) -> NickState | None:
        return self.nicks.get(nick.lower())

    def add_nick(
        self, nick: str, user: str = "", host: str = "", account: str | None = None
    ) -> NickState:
        ns = NickState(nick=nick, user=user, host=host, account=account)
        self.nicks[nick.lower()] = ns
        return ns

    def remove_nick(self, nick: str) -> None:
        self.nicks.pop(nick.lower(), None)

    def rename_nick(self, old: str, new: str) -> None:
        ns = self.nicks.pop(old.lower(), None)
        if ns:
            ns.nick = new
            self.nicks[new.lower()] = ns


# ---------------------------------------------------------------------------
# PyraBot
# ---------------------------------------------------------------------------


class PyraBot:
    """Central bot object exposed to plugins via the Trigger."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.channels: dict[str, ChannelState] = {}  # lowercased channel name
        self.memory: dict[str, Any] = {}  # shared plugin memory
        self.start_time: float = time.monotonic()
        self._current_nick: str = config.core.nick

        # Subsystems — set by run() before any plugin code executes
        self.irc: IRCConnection = IRCConnection(config, self._on_irc_message)
        self.scheduler: "Scheduler | None" = None
        self.plugin_loader: "PluginLoader | None" = None
        self.partyline: "PartylineServer | None" = None

        # Message dispatch: command -> list[Callable]
        self._internal_handlers: dict[str, list[Callable[[IRCMessage], Any]]] = {}
        self._register_internal_handlers()

        # Track names list being built during 353/366
        self._names_buffer: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Public bot API (called by plugins via Trigger or directly)
    # ------------------------------------------------------------------

    async def say(self, target: str, message: str) -> None:
        await self.irc.privmsg(target, message)

    async def reply(self, trigger_or_nick: Any, message: str, channel: str | None = None) -> None:
        """Reply to a nick, prepending their name if in a channel."""
        from pybot.plugin import Trigger

        if isinstance(trigger_or_nick, Trigger):
            t = trigger_or_nick
            if t.is_pm:
                await self.irc.privmsg(t.nick, message)
            else:
                target = t.channel or t.nick
                await self.irc.privmsg(target, f"{t.nick}: {message}")
        else:
            target = channel or trigger_or_nick
            await self.irc.privmsg(target, f"{trigger_or_nick}: {message}")

    async def notice(self, target: str, message: str) -> None:
        await self.irc.notice(target, message)

    async def action(self, target: str, text: str) -> None:
        await self.irc.privmsg(target, f"\x01ACTION {text}\x01")

    async def kick(self, channel: str, nick: str, reason: str = "") -> None:
        await self.irc.kick(channel, nick, reason)

    async def ban(self, channel: str, hostmask: str) -> None:
        await self.irc.mode(channel, "+b", hostmask)

    async def unban(self, channel: str, hostmask: str) -> None:
        await self.irc.mode(channel, "-b", hostmask)

    async def mode(self, target: str, modestring: str, *args: str) -> None:
        await self.irc.mode(target, modestring, *args)

    async def op(self, channel: str, nick: str) -> None:
        await self.irc.mode(channel, "+o", nick)

    async def deop(self, channel: str, nick: str) -> None:
        await self.irc.mode(channel, "-o", nick)

    async def voice(self, channel: str, nick: str) -> None:
        await self.irc.mode(channel, "+v", nick)

    async def devoice(self, channel: str, nick: str) -> None:
        await self.irc.mode(channel, "-v", nick)

    async def topic(self, channel: str, text: str) -> None:
        await self.irc.topic(channel, text)

    async def join(self, channel: str, key: str = "") -> None:
        await self.irc.join(channel, key)

    async def part(self, channel: str, message: str = "") -> None:
        await self.irc.part(channel, message)

    async def quit(self, message: str = "Pyra IRC Bot") -> None:
        await self.irc.quit(message)

    async def raw(self, line: str) -> None:
        await self.irc.send_raw(line)

    async def whois(self, nick: str) -> dict[str, str]:
        return await self.irc.whois(nick)

    def get_channel(self, name: str) -> ChannelState | None:
        return self.channels.get(name.lower())

    def get_nick_in_channel(self, channel: str, nick: str) -> NickState | None:
        ch = self.get_channel(channel)
        return ch.get_nick(nick) if ch else None

    def plugin_config(self, plugin_name: str) -> dict:
        """Return the vars dict for a plugin from config.plugins.vars.

        Plugins use this to read API keys and other per-plugin settings
        without hardcoding them:
            api_key = bot.plugin_config("weather").get("api_key", "")
        """
        return self.config.plugins.vars.get(plugin_name, {})

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def nick(self) -> str:
        return self._current_nick

    # ------------------------------------------------------------------
    # Database session helper
    # ------------------------------------------------------------------

    async def db_session(self) -> "AsyncSession":
        """Return a new DB session. Use as async context manager."""
        from pybot.core.database import get_session

        return get_session()  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # IRC event dispatch
    # ------------------------------------------------------------------

    def _on_irc_message(self, msg: IRCMessage) -> None:
        """Called by IRCConnection for every received message."""
        asyncio.create_task(self._dispatch(msg), name=f"dispatch-{msg.command}")

    async def _dispatch(self, msg: IRCMessage) -> None:
        """Dispatch message to internal handlers then to plugins."""
        # Internal handlers first
        for handler in self._internal_handlers.get(msg.command, []):
            try:
                await handler(msg)
            except Exception as exc:
                logger.error(f"Internal handler error for {msg.command}: {exc}")

        # Partyline broadcast
        if self.partyline:
            await self.partyline.on_irc_message(msg)

        # Plugin dispatch (Phase 3+)
        await self._dispatch_to_plugins(msg)

    async def _dispatch_to_plugins(self, msg: IRCMessage) -> None:
        """Route message to plugin event handlers and command handlers."""
        from pybot.plugin import get_registry

        registry = get_registry()
        core = self.config.core

        # Fire all @plugin.event(cmd) handlers
        for event_handler in registry.events.get(msg.command, []):
            asyncio.create_task(
                self._run_plugin_handler(event_handler.func, msg),
                name=f"event-{event_handler.plugin_name}-{msg.command}",
            )

        # PRIVMSG routing — commands and rules
        if msg.command == "PRIVMSG":
            text = msg.text

            # Ignore our own echoed messages (echo-message cap)
            if msg.nick.lower() == self._current_nick.lower():
                return

            # Check for command prefix
            if text.startswith(core.command_prefix):
                parts = text[len(core.command_prefix) :].split()
                if not parts:
                    return
                cmd_name = parts[0].lower()
                args = parts[1:]

                for command_handler in registry.commands.get(cmd_name, []):
                    trigger = await self._build_trigger(msg, args=args, match=None)
                    if trigger is None:
                        continue
                    # Check privilege
                    if command_handler.privilege and not await self._check_privilege(
                        trigger, command_handler.privilege
                    ):
                        await self.notice(trigger.nick, "Permission denied.")
                        continue
                    asyncio.create_task(
                        self._run_plugin_handler(command_handler.func, msg, trigger=trigger),
                        name=f"cmd-{command_handler.plugin_name}-{cmd_name}",
                    )

            # Rule matching
            for rule_handler in registry.rules:
                m = rule_handler.pattern.search(text)
                if m:
                    trigger = await self._build_trigger(msg, args=[], match=m)
                    if trigger:
                        asyncio.create_task(
                            self._run_plugin_handler(rule_handler.func, msg, trigger=trigger),
                            name=f"rule-{rule_handler.plugin_name}",
                        )

    async def _run_plugin_handler(
        self,
        func: Callable[..., Any],
        msg: IRCMessage,
        trigger: Any | None = None,
    ) -> None:
        try:
            if trigger is not None:
                await func(self, trigger)
            else:
                # Event handler — build trigger for it too
                t = await self._build_trigger(msg, args=[], match=None)
                if t:
                    await func(self, t)
        except Exception as exc:
            logger.exception(f"Plugin handler error in {func.__module__}.{func.__name__}: {exc}")

    async def _build_trigger(
        self,
        msg: IRCMessage,
        args: list[str],
        match: Any | None,
    ) -> Any | None:
        from pybot.plugin import Trigger

        nick = msg.nick
        user = msg.user
        host = msg.host
        hostmask = msg.hostmask or f"{nick}!{user}@{host}"

        # Determine channel
        channel: str | None = None
        is_pm = False
        if msg.params:
            target = msg.params[0]
            if target.startswith(("#", "&", "!", "+")):
                channel = target
            elif target.lower() == self._current_nick.lower():
                is_pm = True

        # Get account from channel state or message tag
        account: str | None = msg.account_tag
        if not account and channel:
            ch = self.get_channel(channel)
            if ch:
                ns = ch.get_nick(nick)
                if ns:
                    account = ns.account

        # Determine admin/owner status
        from pybot.core.database import get_session
        from pybot.core.permissions import has_flag

        admin = False
        owner = False
        try:
            async with get_session() as session:
                owner = await has_flag(session, hostmask, "n")
                admin = owner or await has_flag(session, hostmask, "a")
        except Exception:
            # DB may not be initialised yet
            if nick == self.config.core.owner:
                owner = True
                admin = True

        return Trigger(
            bot=self,
            message=msg,
            match=match,
            args=args,
            channel=channel,
            nick=nick,
            user=user,
            host=host,
            account=account,
            hostmask=hostmask,
            is_pm=is_pm,
            admin=admin,
            owner=owner,
        )

    async def _check_privilege(self, trigger: Any, required_flag: str) -> bool:
        if required_flag == "n":
            return trigger.owner  # type: ignore[no-any-return]
        if required_flag == "a":
            return trigger.admin  # type: ignore[no-any-return]
        return await trigger.has_flag(required_flag, trigger.channel)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Internal IRC event handlers (channel state tracking)
    # ------------------------------------------------------------------

    def _register_internal_handlers(self) -> None:
        self._internal_handlers = {
            "JOIN": [self._handle_join],
            "PART": [self._handle_part],
            "QUIT": [self._handle_quit],
            "NICK": [self._handle_nick],
            "KICK": [self._handle_kick],
            "353": [self._handle_names],
            "366": [self._handle_end_of_names],
            "332": [self._handle_topic],
            "TOPIC": [self._handle_topic_change],
            "MODE": [self._handle_mode],
            "ACCOUNT": [self._handle_account],
            "CHGHOST": [self._handle_chghost],
        }

    async def _handle_join(self, msg: IRCMessage) -> None:
        channel = msg.params[0] if msg.params else msg.text
        nick = msg.nick
        user = msg.user
        host = msg.host

        # extended-join: params may be [channel, account, realname]
        account: str | None = None
        if len(msg.params) >= 2 and msg.params[1] != "*":
            account = msg.params[1]

        if nick.lower() == self._current_nick.lower():
            # We joined a channel
            self.channels[channel.lower()] = ChannelState(name=channel)
            logger.info(f"Joined {channel}")
        else:
            ch = self.channels.get(channel.lower())
            if ch:
                ch.add_nick(nick, user, host, account)

    async def _handle_part(self, msg: IRCMessage) -> None:
        channel = msg.params[0] if msg.params else ""
        nick = msg.nick
        if nick.lower() == self._current_nick.lower():
            self.channels.pop(channel.lower(), None)
        else:
            ch = self.channels.get(channel.lower())
            if ch:
                ch.remove_nick(nick)

    async def _handle_quit(self, msg: IRCMessage) -> None:
        nick = msg.nick
        if nick.lower() == self._current_nick.lower():
            return
        for ch in self.channels.values():
            ch.remove_nick(nick)

    async def _handle_nick(self, msg: IRCMessage) -> None:
        old_nick = msg.nick
        new_nick = msg.text
        if old_nick.lower() == self._current_nick.lower():
            self._current_nick = new_nick
            logger.info(f"Nick changed to {new_nick}")
        for ch in self.channels.values():
            ch.rename_nick(old_nick, new_nick)

    async def _handle_kick(self, msg: IRCMessage) -> None:
        channel = msg.params[0] if msg.params else ""
        kicked = msg.params[1] if len(msg.params) > 1 else ""
        if kicked.lower() == self._current_nick.lower():
            self.channels.pop(channel.lower(), None)
            # Auto-rejoin if configured
            await asyncio.sleep(2)
            await self.join(channel)
        else:
            ch = self.channels.get(channel.lower())
            if ch:
                ch.remove_nick(kicked)

    async def _handle_names(self, msg: IRCMessage) -> None:
        """353 RPL_NAMREPLY — populate channel nick list."""
        if len(msg.params) < 3:
            return
        channel = msg.params[2]
        nicks_str = msg.params[-1]
        buf = self._names_buffer.setdefault(channel.lower(), [])

        prefix_chars = "@+%~&!"  # common mode prefix chars
        for entry in nicks_str.split():
            # Strip mode prefixes
            while entry and entry[0] in prefix_chars:
                entry = entry[1:]
            if entry:
                buf.append(entry)

    async def _handle_end_of_names(self, msg: IRCMessage) -> None:
        """366 RPL_ENDOFNAMES — finalise nick list."""
        if len(msg.params) < 2:
            return
        channel = msg.params[1]
        buf = self._names_buffer.pop(channel.lower(), [])
        ch = self.channels.get(channel.lower())
        if ch:
            for nick in buf:
                if nick.lower() not in ch.nicks:
                    ch.add_nick(nick)

    async def _handle_topic(self, msg: IRCMessage) -> None:
        """332 RPL_TOPIC."""
        if len(msg.params) < 2:
            return
        channel = msg.params[1]
        ch = self.channels.get(channel.lower())
        if ch:
            ch.topic = msg.params[-1]

    async def _handle_topic_change(self, msg: IRCMessage) -> None:
        """TOPIC command — someone changed the topic."""
        channel = msg.params[0] if msg.params else ""
        ch = self.channels.get(channel.lower())
        if ch:
            ch.topic = msg.text

    async def _handle_mode(self, msg: IRCMessage) -> None:
        """MODE — update nick mode prefixes in channel."""
        if not msg.params:
            return
        target = msg.params[0]
        ch = self.channels.get(target.lower())
        if not ch or len(msg.params) < 2:
            return

        modestr = msg.params[1]
        nicks = msg.params[2:]
        setting = True
        nick_idx = 0
        for char in modestr:
            if char == "+":
                setting = True
            elif char == "-":
                setting = False
            elif char in "ov":  # op and voice
                if nick_idx < len(nicks):
                    ns = ch.get_nick(nicks[nick_idx])
                    if ns:
                        if setting:
                            ns.modes.add(char)
                        else:
                            ns.modes.discard(char)
                    nick_idx += 1

    async def _handle_account(self, msg: IRCMessage) -> None:
        """account-notify cap — user's account changed."""
        nick = msg.nick
        account = msg.params[0] if msg.params else None
        if account == "*":
            account = None
        for ch in self.channels.values():
            ns = ch.get_nick(nick)
            if ns:
                ns.account = account

    async def _handle_chghost(self, msg: IRCMessage) -> None:
        """chghost cap — user's host changed."""
        nick = msg.nick
        new_user = msg.params[0] if msg.params else ""
        new_host = msg.params[1] if len(msg.params) > 1 else ""
        for ch in self.channels.values():
            ns = ch.get_nick(nick)
            if ns:
                ns.user = new_user
                ns.host = new_host

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all subsystems and run until stopped."""
        import signal

        # Configure logging
        from pybot.core.logging import setup_logging

        setup_logging(self.config)

        logger.info(f"Pyra {_get_version()} starting up")
        logger.info(f"Nick: {self.config.core.nick}, Owner: {self.config.core.owner}")

        # Initialise database
        from pybot.core.database import init_db

        await init_db(self.config.database.url)

        # Start scheduler
        from pybot.core.scheduler import Scheduler

        self.scheduler = Scheduler(self)
        await self.scheduler.start()

        # Load plugins
        from pathlib import Path

        from pybot.core.plugin_loader import PluginLoader

        self.plugin_loader = PluginLoader(self)
        plugin_dirs = [Path(__file__).parent.parent / "plugins"]
        if self.config.plugins.extra_dir:
            extra = Path(self.config.plugins.extra_dir)
            if extra.exists():
                plugin_dirs.append(extra)
        await self.plugin_loader.load_all(plugin_dirs)

        # Start partyline
        if self.config.partyline.enabled:
            from pybot.core.partyline import PartylineServer

            self.partyline = PartylineServer(self)
            asyncio.create_task(self.partyline.start(), name="partyline")

        # Start web interface
        if self.config.web.enabled:
            asyncio.create_task(self._run_web(), name="web")

        # Signal handlers
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self._shutdown()))
        if hasattr(signal, "SIGHUP"):
            loop.add_signal_handler(
                signal.SIGHUP,
                lambda: asyncio.create_task(self._reload_plugins()),
            )

        # Connect to IRC
        await self.irc.run()

    async def _run_web(self) -> None:
        import uvicorn

        from pybot.web.app import create_app

        app = create_app(self)
        cfg = self.config.web
        config = uvicorn.Config(
            app,
            host=cfg.host,
            port=cfg.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def _shutdown(self) -> None:
        logger.info("Shutting down...")
        await self.quit("Shutting down")
        if self.scheduler:
            await self.scheduler.stop()
        if self.partyline:
            await self.partyline.stop()
        await self.irc.stop()

    async def _reload_plugins(self) -> None:
        logger.info("Reloading plugins (SIGHUP)")
        if self.plugin_loader:
            await self.plugin_loader.reload_all()


def _get_version() -> str:
    from pybot import __version__

    return __version__
