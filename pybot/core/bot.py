"""
Main bot class — orchestrates all subsystems and provides the plugin API.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from pybot.core.config import BotConfig
from pybot.core.irc import IRCConnection, IRCMessage
from pybot.core.services import ServicesInterface

if TYPE_CHECKING:
    from pathlib import Path

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
    bans: set[str] = field(default_factory=set)  # ban masks

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
        self.start_time: float = self._monotonic()
        self._current_nick: str = config.core.nick

        # Subsystems — set by run() before any plugin code executes
        self.irc: IRCConnection = IRCConnection(config, self._on_irc_message)
        self.services: ServicesInterface = ServicesInterface(self)
        self.scheduler: "Scheduler | None" = None
        self.plugin_loader: "PluginLoader | None" = None
        self.partyline: "PartylineServer | None" = None

        # Message dispatch: command -> list[Callable]
        self._internal_handlers: dict[str, list[Callable[[IRCMessage], Any]]] = {}
        self._register_internal_handlers()

        # Track names list being built during 353/366
        self._names_buffer: dict[str, list[str]] = {}

        # Runtime observability thresholds (seconds) for profiling hotspots.
        self.slow_handler_warn_seconds: float = 0.5
        self.slow_dispatch_warn_seconds: float = 1.0

    def _monotonic(self) -> float:
        return time.monotonic()

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

    async def invite(self, nick: str, channel: str) -> None:
        await self.irc.invite(nick, channel)

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

    async def reload_runtime(self) -> None:
        """Reload config from disk and reload all loaded plugins."""
        from pybot.core.config import load_config

        config_path = self._resolve_runtime_config_path()
        new_config = load_config(config_path)

        # Keep active runtime config in sync for IRC/web/partyline behavior.
        self.config = new_config
        self.irc._config = new_config

        if self.plugin_loader:
            await self.plugin_loader.reload_all()

    async def shutdown_process(self, reason: str = "Shutdown requested") -> None:
        """Gracefully stop the bot process."""
        await self.quit(reason)
        await self._shutdown()

    async def restart_process(self) -> None:
        """Restart the current bot process in-place."""
        config_path = self._resolve_runtime_config_path()
        await self._shutdown()
        # Controlled self-reexec for restart functionality.
        os.execv(  # noqa: S606
            sys.executable,
            [sys.executable, "-m", "pybot", "--config", str(config_path)],
        )

    def _resolve_runtime_config_path(self) -> "Path":
        from pathlib import Path

        configured = os.environ.get("CONFIG_FILE")
        if configured:
            return Path(configured)

        docker_default = Path("/data/config.yaml")
        if docker_default.exists() or Path("/.dockerenv").exists():
            return docker_default

        return Path("config/config.yaml")

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
        dispatch_started = self._monotonic()

        # Internal handlers first
        for handler in self._internal_handlers.get(msg.command, []):
            handler_started = self._monotonic()
            try:
                await handler(msg)
            except Exception as exc:
                logger.error(f"Internal handler error for {msg.command}: {exc}")
            finally:
                self._warn_if_slow(
                    f"internal handler {msg.command}:{handler.__name__}",
                    self._monotonic() - handler_started,
                    self.slow_handler_warn_seconds,
                )

        # Partyline broadcast
        if self.partyline:
            await self.partyline.on_irc_message(msg)

        await self._persist_log_entry(msg)

        # Plugin dispatch (Phase 3+)
        await self._dispatch_to_plugins(msg)

        self._warn_if_slow(
            f"dispatch {msg.command}",
            self._monotonic() - dispatch_started,
            self.slow_dispatch_warn_seconds,
        )

    async def _persist_log_entry(self, msg: IRCMessage) -> None:
        """Persist selected IRC events for the web log viewer."""
        event_type = msg.command.upper()
        if event_type not in {
            "PRIVMSG",
            "NOTICE",
            "JOIN",
            "PART",
            "QUIT",
            "KICK",
            "MODE",
            "TOPIC",
            "NICK",
            "INVITE",
        }:
            return

        if event_type in {"QUIT", "NICK"}:
            channel = ""
        elif event_type == "INVITE":
            channel = msg.params[1] if len(msg.params) > 1 else ""
        else:
            channel = msg.channel or (msg.params[0] if msg.params else "")

        message = self._sanitize_log_message(msg)
        hostmask = msg.hostmask or f"{msg.nick}!{msg.user}@{msg.host}"

        try:
            from pybot.core.database import Log, get_session

            async with get_session() as session:
                session.add(
                    Log(
                        channel=channel or "",
                        nick=msg.nick,
                        hostmask=hostmask,
                        event_type=event_type,
                        message=message,
                    )
                )
        except Exception as exc:
            logger.debug(f"Could not persist IRC log entry: {exc}")

    def _sanitize_log_message(self, msg: IRCMessage) -> str | None:
        """Redact sensitive authentication payloads before log persistence."""
        message = msg.text or None
        if not message or msg.command.upper() not in {"PRIVMSG", "NOTICE"}:
            return message

        target = (msg.params[0] if msg.params else "").strip().lower()
        service = target.split("@", 1)[0]
        if service not in {"nickserv", "authserv", "q", "userserv"}:
            return message

        stripped = message.strip()
        upper = stripped.upper()
        if upper.startswith("IDENTIFY"):
            return "IDENTIFY [REDACTED]"
        if upper.startswith("AUTH"):
            return "AUTH [REDACTED]"
        if upper.startswith("LOGIN"):
            return "LOGIN [REDACTED]"

        return message

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
        started = self._monotonic()
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
        finally:
            self._warn_if_slow(
                f"plugin handler {func.__module__}.{func.__name__} ({msg.command})",
                self._monotonic() - started,
                self.slow_handler_warn_seconds,
            )

    def _warn_if_slow(self, label: str, elapsed: float, threshold: float) -> None:
        if elapsed >= threshold:
            logger.warning(
                f"Slow handler: {label} took {elapsed:.3f}s (threshold {threshold:.3f}s)"
            )

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

        # Prefer account-tag when explicitly present, even when unauthenticated.
        if "account" in msg.tags:
            tag_account = msg.tags.get("account")
            account: str | None = None if tag_account in (None, "", "*") else tag_account
        else:
            account = None

        if account is None and channel:
            ch = self.get_channel(channel)
            if ch:
                ns = ch.get_nick(nick)
                if ns:
                    account = ns.account

        # For commands, do a WHOIS account fallback when account-tag/state is missing.
        if account is None and args:
            whois_data = await self.whois(nick)
            whois_account = whois_data.get("account")
            if whois_account:
                account = whois_account

        # Determine admin/owner status
        from pybot.core.database import get_session
        from pybot.core.permissions import has_flag

        admin = False
        owner = False
        try:
            async with get_session() as session:
                owner = await has_flag(session, hostmask, "n", account=account)
                admin = owner or await has_flag(session, hostmask, "a", account=account)
        except Exception:
            # DB may not be initialised yet
            if nick == self.config.core.owner:
                owner = True
                admin = True

        configured_owner_account = self.config.core.owner_account.strip()
        if (
            not owner
            and configured_owner_account
            and account
            and account.lower() == configured_owner_account.lower()
        ):
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
            "001": [self._handle_welcome],
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
            "324": [self._handle_channel_mode_is],
            "352": [self._handle_who_reply],
            "367": [self._handle_ban_list],
            "368": [self._handle_end_of_ban_list],
            "ACCOUNT": [self._handle_account],
            "CHGHOST": [self._handle_chghost],
            "NOTICE": [self._handle_notice],
        }

    async def _handle_welcome(self, msg: IRCMessage) -> None:
        """001 RPL_WELCOME — reset runtime state for a fresh server session."""
        if msg.params and msg.params[0]:
            self._current_nick = msg.params[0]

        # A reconnect starts a new server session; stale state must be dropped.
        self.channels.clear()
        self._names_buffer.clear()

    async def _handle_notice(self, msg: IRCMessage) -> None:
        """Route NOTICE events to the services interface for service replies."""
        source = msg.nick or (msg.prefix or "").split("!")[0]
        self.services.on_notice(source, msg.text)

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
            await self.irc.send(f"WHO {channel}")
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
        self.irc.invalidate_whois_cache(old_nick)
        self.irc.invalidate_whois_cache(new_nick)
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
            if self.config.services.enabled and self.config.services.channel_guard:
                if self.config.services.channel_guard_reinvite:
                    await self.services.chanserv_invite(channel, self._current_nick)
                if self.config.services.channel_guard_reop:
                    await self.services.chanserv_op(channel, self._current_nick)
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

        prefix_chars = self.irc.nick_prefix_chars or "@+"
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

        self._apply_channel_mode_changes(ch, target, msg.params[1], msg.params[2:])

    def _apply_channel_mode_changes(
        self,
        channel: ChannelState,
        target: str,
        modestr: str,
        args: list[str],
    ) -> None:
        mode_flags = set(channel.modes)
        setting = True
        arg_idx = 0

        for char in modestr:
            if char == "+":
                setting = True
                continue
            if char == "-":
                setting = False
                continue

            arg: str | None = None
            if self.irc.mode_takes_parameter(char, setting) and arg_idx < len(args):
                arg = args[arg_idx]
                arg_idx += 1

            if char in self.irc.nick_prefix_modes and arg is not None:
                mode_nick = arg
                ns = channel.get_nick(mode_nick)
                if ns:
                    if setting:
                        ns.modes.add(char)
                    else:
                        ns.modes.discard(char)

                if (
                    self.config.services.enabled
                    and self.config.services.channel_guard
                    and char == "o"
                    and mode_nick.lower() == self._current_nick.lower()
                    and not setting
                    and self.config.services.channel_guard_reop
                ):
                    asyncio.create_task(self.services.chanserv_op(target, self._current_nick))
                continue

            if char == "b" and arg is not None:
                if setting:
                    channel.bans.add(arg)
                else:
                    channel.bans.discard(arg)

            if setting:
                mode_flags.add(char)
            else:
                mode_flags.discard(char)

        channel.modes = "".join(sorted(mode_flags))

    async def _handle_channel_mode_is(self, msg: IRCMessage) -> None:
        """324 RPL_CHANNELMODEIS — sync current channel mode state."""
        if len(msg.params) < 3:
            return

        channel_name = msg.params[1]
        ch = self.channels.get(channel_name.lower())
        if not ch:
            return

        self._apply_channel_mode_changes(ch, channel_name, msg.params[2], msg.params[3:])

    async def _handle_who_reply(self, msg: IRCMessage) -> None:
        """352 RPL_WHOREPLY — refresh nick user/host state from WHO replies."""
        if len(msg.params) < 7:
            return

        channel_name = msg.params[1]
        ch = self.channels.get(channel_name.lower())
        if not ch:
            return

        user = msg.params[2]
        host = msg.params[3]
        nick = msg.params[5]
        ns = ch.get_nick(nick)
        if ns is None:
            ns = ch.add_nick(nick)
        ns.user = user
        ns.host = host

    async def _handle_ban_list(self, msg: IRCMessage) -> None:
        """367 RPL_BANLIST — include listed ban mask in channel state."""
        if len(msg.params) < 3:
            return

        channel_name = msg.params[1]
        ch = self.channels.get(channel_name.lower())
        if ch:
            ch.bans.add(msg.params[2])

    async def _handle_end_of_ban_list(self, msg: IRCMessage) -> None:
        """368 RPL_ENDOFBANLIST marker."""
        return

    async def _handle_account(self, msg: IRCMessage) -> None:
        """account-notify cap — user's account changed."""
        nick = msg.nick
        self.irc.invalidate_whois_cache(nick)
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
        self.irc.invalidate_whois_cache(nick)
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
        await self._bootstrap_owner_account()

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

    async def _bootstrap_owner_account(self) -> None:
        """Ensure an owner account exists for partyline/web auth in first-run setups."""
        owner_nick = self.config.core.owner.strip()
        partyline_password = self.config.partyline.password.get_secret_value().strip()

        if not owner_nick:
            logger.warning("core.owner is empty; cannot bootstrap owner login for web/partyline")
            return
        if not partyline_password:
            logger.warning(
                "partyline.password is empty; set it in config "
                "to enable initial web/partyline login"
            )
            return

        from sqlalchemy import select

        from pybot.core.database import User, get_session
        from pybot.core.permissions import add_owner_bootstrap
        from pybot.web.auth import hash_password, verify_password

        async with get_session() as session:
            result = await session.execute(select(User).where(User.nick == owner_nick))
            user = result.scalar_one_or_none()

            if user is None:
                # Bootstrap a safe default owner hostmask for admin UI + partyline login.
                await add_owner_bootstrap(
                    session,
                    owner_nick,
                    f"{owner_nick}!*@*",
                    hash_password(partyline_password),
                )
                logger.info(f"Bootstrapped owner login for '{owner_nick}'")
                return

            updated = False
            if "n" not in (user.global_flags or ""):
                user.global_flags = "".join(sorted(set((user.global_flags or "") + "n")))
                updated = True
            if not user.password_hash:
                user.password_hash = hash_password(partyline_password)
                updated = True
            elif not verify_password(partyline_password, user.password_hash):
                # Keep config as source-of-truth for first-run/admin bootstrap credentials.
                user.password_hash = hash_password(partyline_password)
                updated = True

            if updated:
                logger.info(f"Updated owner account bootstrap data for '{owner_nick}'")

    async def _run_web(self) -> None:
        import uvicorn

        from pybot.web.app import create_app

        try:
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
        except Exception as exc:
            logger.error(f"Web interface failed to start: {exc}")

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
