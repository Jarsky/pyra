"""
IRC protocol handler — async IRC/IRCv3 client using asyncio StreamReader/StreamWriter.

Responsibilities:
- IRC wire protocol parsing (IRCMessage)
- TCP/SSL connection management with reconnect + exponential backoff
- IRCv3 capability negotiation
- SASL authentication (PLAIN, EXTERNAL, SCRAM-SHA-256)
- Outbound flood protection via asyncio.Queue + token bucket
- PING/PONG keepalive
- DCC CHAT initiation
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# IRCMessage — parsed wire representation
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^@([^ ]+) ")
_PREFIX_RE = re.compile(r"^:([^ ]+) ")


@dataclass
class IRCMessage:
    """A fully parsed IRC message."""

    tags: dict[str, str] = field(default_factory=dict)
    prefix: str | None = None  # nick!user@host or server name
    command: str = ""
    params: list[str] = field(default_factory=list)
    raw: str = ""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def nick(self) -> str:
        if self.prefix and "!" in self.prefix:
            return self.prefix.split("!", 1)[0]
        return self.prefix or ""

    @property
    def user(self) -> str:
        if self.prefix and "!" in self.prefix and "@" in self.prefix:
            return self.prefix.split("!", 1)[1].split("@", 1)[0]
        return ""

    @property
    def host(self) -> str:
        if self.prefix and "@" in self.prefix:
            return self.prefix.rsplit("@", 1)[1]
        return ""

    @property
    def hostmask(self) -> str:
        if self.prefix and "!" in self.prefix:
            return self.prefix
        return ""

    @property
    def channel(self) -> str | None:
        if self.params and self.params[0].startswith(("#", "&", "!", "+")):
            return self.params[0]
        return None

    @property
    def text(self) -> str:
        """The message text (last parameter for PRIVMSG/NOTICE/TOPIC/etc)."""
        if self.params:
            return self.params[-1]
        return ""

    @property
    def ctcp_command(self) -> str | None:
        """Returns the CTCP command if this is a CTCP message, else None."""
        if self.command in ("PRIVMSG", "NOTICE") and self.text.startswith("\x01"):
            body = self.text.strip("\x01")
            return body.split(" ", 1)[0]
        return None

    @property
    def ctcp_text(self) -> str:
        """Returns CTCP payload (after command), or empty string."""
        if self.ctcp_command and " " in self.text.strip("\x01"):
            return self.text.strip("\x01").split(" ", 1)[1]
        return ""

    @property
    def server_time(self) -> str | None:
        """IRCv3 server-time tag value if present."""
        return self.tags.get("time")

    @property
    def account_tag(self) -> str | None:
        """IRCv3 account tag (account name) if present."""
        return self.tags.get("account")

    # ------------------------------------------------------------------
    # Parser
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, raw: str) -> "IRCMessage":
        """Parse a raw IRC line into an IRCMessage.

        Handles:
        - IRCv3 message tags (@key=value;key2=value2)
        - Optional prefix (:nick!user@host or :server)
        - Command (numeric or named)
        - Parameters (last param may be prefixed with ':')
        """
        msg = cls(raw=raw)
        s = raw.rstrip("\r\n")

        # Parse IRCv3 tags
        if s.startswith("@"):
            tag_match = _TAG_RE.match(s)
            if tag_match:
                tag_str = tag_match.group(1)
                for tag in tag_str.split(";"):
                    if "=" in tag:
                        k, v = tag.split("=", 1)
                        # Unescape IRCv3 tag values
                        msg.tags[k] = _unescape_tag_value(v)
                    else:
                        msg.tags[tag] = ""
                s = s[tag_match.end() :]

        # Parse prefix
        if s.startswith(":"):
            prefix_match = _PREFIX_RE.match(s)
            if prefix_match:
                msg.prefix = prefix_match.group(1)
                s = s[prefix_match.end() :]

        # Parse command and params
        if " :" in s:
            head, trailing = s.split(" :", 1)
            parts = head.split()
            parts.append(trailing)
        else:
            parts = s.split()

        if not parts:
            return msg

        msg.command = parts[0].upper()
        msg.params = parts[1:]
        return msg


def _unescape_tag_value(value: str) -> str:
    """Unescape IRCv3 tag value escape sequences."""
    return (
        value.replace("\\:", ";")
        .replace("\\s", " ")
        .replace("\\\\", "\\")
        .replace("\\r", "\r")
        .replace("\\n", "\n")
    )


# ---------------------------------------------------------------------------
# IRCConnection — async TCP/SSL IRC client
# ---------------------------------------------------------------------------

# Capabilities we want to request
DESIRED_CAPS = {
    "sasl",
    "multi-prefix",
    "extended-join",
    "account-notify",
    "away-notify",
    "message-tags",
    "batch",
    "echo-message",
    "server-time",
    "account-tag",
    "chghost",
    "invite-notify",
    "cap-notify",
}


class IRCConnection:
    """Async IRC connection with flood protection and IRCv3 cap negotiation."""

    def __init__(
        self,
        config: Any,  # BotConfig — avoid circular import
        message_handler: Callable[[IRCMessage], None],
    ) -> None:
        self._config = config
        self._on_message = message_handler

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._registered = False  # True after 001

        # Flood protection: outbound message queue with token bucket
        self._flood_queue: asyncio.Queue[str] = asyncio.Queue()
        self._flood_tokens: float = 5.0
        self._flood_token_rate: float = 0.5  # seconds per token
        self._flood_burst: int = 5

        # IRCv3 capability negotiation state
        self._caps_available: set[str] = set()
        self._caps_acked: set[str] = set()
        self._cap_negotiating = False

        # ISUPPORT (005) parsed state
        self.isupport: dict[str, str | bool] = {}
        self.network_name: str = ""
        self.mode_to_prefix: dict[str, str] = {"o": "@", "v": "+"}
        self.prefix_to_mode: dict[str, str] = {"@": "o", "+": "v"}
        self.nick_prefix_chars: str = "@+"
        self.nick_prefix_modes: set[str] = {"o", "v"}
        self.chanmodes: str = ""
        self.chanmodes_a: set[str] = {"b"}
        self.chanmodes_b: set[str] = set()
        self.chanmodes_c: set[str] = set()
        self.chanmodes_d: set[str] = set()

        # SASL
        self._sasl_done = asyncio.Event()

        # WHOIS futures: nick -> Future resolved when 318 arrives
        self._whois_futures: dict[str, asyncio.Future[dict[str, str]]] = {}
        self._whois_data: dict[str, dict[str, str]] = {}
        self._whois_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._whois_cache_ttl_seconds: float = 30.0

        # Reconnect state
        self._reconnect_delay: float = 2.0
        self._max_reconnect_delay: float = 300.0
        self._running = False

        # Background tasks
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------
    # Public send methods
    # ------------------------------------------------------------------

    async def send_raw(self, line: str) -> None:
        """Send a raw IRC line, bypassing the flood queue. Use for PING/PONG only."""
        if self._writer:
            self._writer.write(f"{line}\r\n".encode())
            await self._writer.drain()

    async def send(self, line: str) -> None:
        """Enqueue a line for flood-protected delivery."""
        await self._flood_queue.put(line)

    async def privmsg(self, target: str, text: str) -> None:
        await self.send(f"PRIVMSG {target} :{text}")

    async def notice(self, target: str, text: str) -> None:
        await self.send(f"NOTICE {target} :{text}")

    async def join(self, channel: str, key: str = "") -> None:
        if key:
            await self.send(f"JOIN {channel} {key}")
        else:
            await self.send(f"JOIN {channel}")

    async def part(self, channel: str, reason: str = "") -> None:
        if reason:
            await self.send(f"PART {channel} :{reason}")
        else:
            await self.send(f"PART {channel}")

    async def invite(self, nick: str, channel: str) -> None:
        await self.send(f"INVITE {nick} {channel}")

    async def kick(self, channel: str, nick: str, reason: str = "") -> None:
        await self.send(f"KICK {channel} {nick} :{reason}")

    async def mode(self, target: str, modes: str, *args: str) -> None:
        if args:
            await self.send(f"MODE {target} {modes} {' '.join(args)}")
        else:
            await self.send(f"MODE {target} {modes}")

    async def topic(self, channel: str, text: str) -> None:
        await self.send(f"TOPIC {channel} :{text}")

    async def nick(self, new_nick: str) -> None:
        await self.send(f"NICK {new_nick}")

    async def quit(self, message: str = "Pyra IRC Bot") -> None:
        await self.send(f"QUIT :{message}")

    async def whois(self, nick: str) -> dict[str, str]:
        """Send WHOIS and return a dict of collected info when 318 arrives."""
        nick_key = nick.lower()
        loop = asyncio.get_event_loop()

        cached = self._whois_cache.get(nick_key)
        if cached and cached[0] > loop.time():
            return dict(cached[1])

        existing = self._whois_futures.get(nick_key)
        if existing is not None:
            return dict(await existing)

        fut: asyncio.Future[dict[str, str]] = loop.create_future()
        self._whois_futures[nick_key] = fut
        self._whois_data[nick_key] = {}
        await self.send(f"WHOIS {nick}")
        result = await fut
        self._whois_cache[nick_key] = (loop.time() + self._whois_cache_ttl_seconds, dict(result))
        return dict(result)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main connection loop with automatic reconnect."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(self._format_connection_error(exc))

            if not self._running:
                break

            logger.info(f"Reconnecting in {self._reconnect_delay:.0f}s...")
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    def _format_connection_error(self, exc: Exception) -> str:
        """Return a concise, actionable message for expected connection failures."""
        if isinstance(exc, socket.gaierror):
            return (
                f"IRC connection error: cannot resolve host '{self._config.primary_server.host}'. "
                "Check server.host DNS/name in config."
            )
        if isinstance(exc, ConnectionRefusedError):
            return (
                f"IRC connection error: connection refused by "
                f"{self._config.primary_server.host}:{self._config.primary_server.port}. "
                "Check port/firewall/SSL settings."
            )
        if isinstance(exc, ssl.SSLError):
            return (
                "IRC connection error: SSL handshake failed. "
                "Verify server SSL support and the ssl/ssl_verify settings."
            )
        return f"IRC connection error: {type(exc).__name__}: {exc}"

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._writer:
            try:
                await self.quit()
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")

    async def _connect_and_run(self) -> None:
        server = self._config.primary_server
        logger.info(f"Connecting to {server.host}:{server.port} (SSL={server.ssl})")

        ssl_ctx: ssl.SSLContext | bool | None = None
        if server.ssl:
            ssl_ctx = ssl.create_default_context()
            if not server.ssl_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            # Client cert for SASL EXTERNAL
            auth = self._config.auth
            if auth.certfile:
                ssl_ctx.load_cert_chain(auth.certfile, auth.keyfile or None)

        self._reader, self._writer = await asyncio.open_connection(
            server.host, server.port, ssl=ssl_ctx
        )
        # Enable TCP keepalive
        sock = self._writer.get_extra_info("socket")
        if sock:
            import socket as _socket

            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)

        self._connected = True
        self._registered = False
        self._caps_available.clear()
        self._caps_acked.clear()
        self._sasl_done.clear()

        logger.info("TCP connection established")

        # Start reader and writer tasks
        self._tasks = [
            asyncio.create_task(self._reader_loop(), name="irc-reader"),
            asyncio.create_task(self._writer_loop(), name="irc-writer"),
        ]

        # Begin IRC registration
        await self._begin_registration()

        # Wait for reader to exit (connection dropped or cancelled)
        try:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    continue
                if isinstance(result, Exception):
                    logger.error(f"IRC task error: {result}")
        finally:
            self._connected = False
            self._registered = False

    async def _begin_registration(self) -> None:
        """Send CAP LS, PASS, NICK, USER to begin registration."""
        server = self._config.primary_server
        core = self._config.core

        self._cap_negotiating = True
        await self.send_raw("CAP LS 302")

        if server.password.get_secret_value():
            await self.send_raw(f"PASS :{server.password.get_secret_value()}")

        await self.send_raw(f"NICK {core.nick}")
        await self.send_raw(f"USER {core.ident} 0 * :{core.realname}")

    # ------------------------------------------------------------------
    # Reader loop
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                data = await self._reader.readline()
                if not data:
                    logger.warning("IRC server closed connection")
                    break
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    logger.debug(f"<< {line}")
                    msg = IRCMessage.parse(line)
                    await self._handle_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Reader loop error: {exc}")
        finally:
            current = asyncio.current_task()
            for task in self._tasks:
                if task is not current and not task.done():
                    task.cancel()

    # ------------------------------------------------------------------
    # Writer loop (token bucket flood control)
    # ------------------------------------------------------------------

    async def _writer_loop(self) -> None:
        assert self._writer is not None
        last_refill = asyncio.get_event_loop().time()
        try:
            while True:
                # Refill tokens
                now = asyncio.get_event_loop().time()
                elapsed = now - last_refill
                self._flood_tokens = min(
                    float(self._flood_burst),
                    self._flood_tokens + elapsed / self._flood_token_rate,
                )
                last_refill = now

                if self._flood_tokens >= 1.0:
                    try:
                        line = self._flood_queue.get_nowait()
                        self._flood_tokens -= 1.0
                        logger.debug(f">> {line}")
                        self._writer.write(f"{line}\r\n".encode())
                        await self._writer.drain()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(self._flood_token_rate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Writer loop error: {exc}")

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _handle_message(self, msg: IRCMessage) -> None:
        handlers = {
            "PING": self._on_ping,
            "CAP": self._on_cap,
            "AUTHENTICATE": self._on_authenticate,
            "001": self._on_001,
            "005": self._on_005,
            "433": self._on_433,
            "311": self._on_whois_311,
            "330": self._on_whois_330,
            "318": self._on_whois_318,
            "900": self._on_900,
            "903": self._on_903,
            "904": self._on_904,
        }
        handler = handlers.get(msg.command)
        if handler:
            await handler(msg)
        # Always pass to the bot's dispatch for plugin handlers
        self._on_message(msg)

    async def _on_ping(self, msg: IRCMessage) -> None:
        await self.send_raw(f"PONG :{msg.text}")

    async def _on_001(self, msg: IRCMessage) -> None:
        """Welcome — registration complete."""
        self._registered = True
        self._reconnect_delay = 2.0  # reset backoff on successful connect
        logger.info(f"Registered on {msg.prefix} as {msg.params[0] if msg.params else '?'}")

        # Auto-join channels
        for channel in self._config.channels.autojoin:
            await self.join(channel)

        # Service authentication
        auth = self._config.auth
        method = auth.auth_method
        password = auth.nickserv_password.get_secret_value()

        if method == "nickserv" and password:
            await self.privmsg("NickServ", f"IDENTIFY {password}")
        elif method == "authserv" and password:
            username = auth.sasl_username or self._config.core.nick
            await self.privmsg("AuthServ@services.undernet.org", f"AUTH {username} {password}")
        elif method == "q" and password:
            username = auth.sasl_username or self._config.core.nick
            await self.privmsg("Q@CServe.quakenet.org", f"AUTH {username} {password}")
        elif method == "userserv" and password:
            username = auth.sasl_username or self._config.core.nick
            await self.privmsg("UserServ", f"LOGIN {username} {password}")
        elif method == "server_password" and password:
            # Already sent as PASS during connection; nothing further needed.
            pass
        # "sasl" and "none" require no post-001 action

        # Anope HostServ vhost
        if self._config.services.enabled and self._config.services.vhost:
            await self.privmsg("HostServ", f"ON {self._config.services.vhost}")

        # commands_on_connect — executed after auth commands are queued
        for line in self._config.services.commands_on_connect:
            await self.send(line)

    async def _on_005(self, msg: IRCMessage) -> None:
        """ISUPPORT — server feature advertisement."""
        if len(msg.params) < 2:
            return

        # 005 format: <nick> <token> <token> ... :are supported by this server
        tokens = msg.params[1:]
        if tokens and tokens[-1].startswith("are supported by"):
            tokens = tokens[:-1]

        for token in tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                self.isupport[key] = value

                if key == "NETWORK":
                    self.network_name = value
                elif key == "CHANMODES":
                    self.chanmodes = value
                    self._apply_chanmodes_token(value)
                elif key == "PREFIX":
                    self._apply_prefix_token(value)
            else:
                # Feature flag without value (e.g. SAFELIST)
                self.isupport[token] = True

    def _apply_prefix_token(self, value: str) -> None:
        """Parse PREFIX token value in the form `(modes)prefixes`."""
        m = re.match(r"^\(([^)]+)\)(.+)$", value)
        if not m:
            return

        modes, prefixes = m.groups()
        if not modes or not prefixes:
            return

        pairs = list(zip(modes, prefixes, strict=False))
        if not pairs:
            return

        self.mode_to_prefix = {mode: prefix for mode, prefix in pairs}
        self.prefix_to_mode = {prefix: mode for mode, prefix in pairs}
        self.nick_prefix_modes = set(self.mode_to_prefix.keys())
        self.nick_prefix_chars = "".join(prefix for _, prefix in pairs)

    def _apply_chanmodes_token(self, value: str) -> None:
        """Parse CHANMODES token into mode groups A,B,C,D."""
        groups = value.split(",")
        if len(groups) != 4:
            return

        self.chanmodes_a = set(groups[0])
        self.chanmodes_b = set(groups[1])
        self.chanmodes_c = set(groups[2])
        self.chanmodes_d = set(groups[3])

    def mode_takes_parameter(self, mode: str, setting: bool) -> bool:
        """Return whether a channel mode consumes an argument in this direction."""
        if mode in self.nick_prefix_modes:
            return True
        if mode in self.chanmodes_a or mode in self.chanmodes_b:
            return True
        if mode in self.chanmodes_c:
            return setting
        return False

    async def _on_433(self, msg: IRCMessage) -> None:
        """Nick already in use — try next altnick."""
        core = self._config.core
        current = msg.params[1] if len(msg.params) > 1 else core.nick

        if current == core.nick:
            altnicks = core.altnicks
        else:
            try:
                idx = core.altnicks.index(current)
                altnicks = core.altnicks[idx + 1 :]
            except (ValueError, IndexError):
                altnicks = []

        if altnicks:
            new_nick = altnicks[0]
        else:
            new_nick = f"{core.nick}_{os.getpid() % 9999}"

        logger.warning(f"Nick '{current}' in use, trying '{new_nick}'")
        await self.send_raw(f"NICK {new_nick}")

    # ------------------------------------------------------------------
    # CAP / SASL negotiation
    # ------------------------------------------------------------------

    async def _on_cap(self, msg: IRCMessage) -> None:
        """Handle CAP sub-commands."""
        if len(msg.params) < 2:
            return
        subcommand = msg.params[1].upper()

        if subcommand == "LS":
            cap_list = msg.params[-1]
            # Parse available caps (may include =value for cap-notify)
            for cap in cap_list.split():
                self._caps_available.add(cap.split("=")[0])

            # Only send REQ if we got the final LS (not multi-line)
            # Multi-line LS has a '*' as second param
            if len(msg.params) > 2 and msg.params[2] == "*":
                return  # More caps coming

            # Request the intersection of desired and available
            to_request = self._desired_caps() & self._caps_available
            if to_request:
                await self.send_raw(f"CAP REQ :{' '.join(sorted(to_request))}")
            else:
                self._cap_negotiating = False
                await self.send_raw("CAP END")

        elif subcommand == "ACK":
            acked = msg.params[-1].split()
            for cap in acked:
                cap_name = cap.lstrip("~=")
                if cap_name.startswith("-"):
                    self._caps_acked.discard(cap_name[1:])
                else:
                    self._caps_acked.add(cap_name)

            if "sasl" in self._caps_acked:
                await self._begin_sasl()
            else:
                self._cap_negotiating = False
                await self.send_raw("CAP END")

        elif subcommand == "NAK":
            logger.warning(f"CAP NAK: {msg.params[-1]}")
            self._cap_negotiating = False
            await self.send_raw("CAP END")

        elif subcommand == "NEW":
            # cap-notify: new caps appeared at runtime
            new_caps = {cap.split("=", 1)[0] for cap in msg.params[-1].split()}
            self._caps_available.update(new_caps)
            to_request = (self._desired_caps() & new_caps) - self._caps_acked
            if to_request:
                await self.send_raw(f"CAP REQ :{' '.join(sorted(to_request))}")

        elif subcommand == "DEL":
            # cap-notify: caps removed at runtime
            for cap in msg.params[-1].split():
                cap_name = cap.split("=", 1)[0]
                self._caps_available.discard(cap_name)
                self._caps_acked.discard(cap_name)

    async def _begin_sasl(self) -> None:
        mechanism = self._config.auth.sasl_mechanism
        if mechanism == "none":
            await self.send_raw("CAP END")
            return

        if not self._sasl_is_configured():
            logger.warning(
                "SASL is enabled in config but credentials are incomplete; "
                "continuing without SASL"
            )
            await self.send_raw("CAP END")
            return
        logger.info(f"Beginning SASL {mechanism}")
        await self.send_raw(f"AUTHENTICATE {mechanism}")

    def _desired_caps(self) -> set[str]:
        caps = set(DESIRED_CAPS)
        if not self._sasl_is_configured():
            caps.discard("sasl")
        return caps

    def _sasl_is_configured(self) -> bool:
        auth = self._config.auth
        mechanism = auth.sasl_mechanism

        if mechanism == "none":
            return False
        if mechanism == "EXTERNAL":
            return bool(auth.certfile)
        if mechanism in {"PLAIN", "SCRAM-SHA-256"}:
            return bool(auth.sasl_password.get_secret_value())
        return False

    async def _on_authenticate(self, msg: IRCMessage) -> None:
        """Server sent AUTHENTICATE prompt."""
        if msg.text != "+":
            return

        auth = self._config.auth
        mechanism = auth.sasl_mechanism

        if mechanism == "PLAIN":
            user = auth.sasl_username or self._config.core.nick
            password = auth.sasl_password.get_secret_value()
            payload = base64.b64encode(f"\x00{user}\x00{password}".encode()).decode()
            await self.send_raw(f"AUTHENTICATE {payload}")

        elif mechanism == "EXTERNAL":
            await self.send_raw("AUTHENTICATE +")

        elif mechanism == "SCRAM-SHA-256":
            # Phase 1 of SCRAM exchange
            user = auth.sasl_username or self._config.core.nick
            self._scram_nonce = base64.b64encode(os.urandom(18)).decode()
            client_first = f"n,,n={user},r={self._scram_nonce}"
            payload = base64.b64encode(client_first.encode()).decode()
            await self.send_raw(f"AUTHENTICATE {payload}")

    async def _on_900(self, msg: IRCMessage) -> None:
        """RPL_LOGGEDIN — SASL success, account logged in."""
        account = msg.params[2] if len(msg.params) > 2 else "?"
        logger.info(f"Logged in as services account: {account}")

    async def _on_903(self, msg: IRCMessage) -> None:
        """RPL_SASLSUCCESS."""
        logger.info("SASL authentication successful")
        self._sasl_done.set()
        await self.send_raw("CAP END")

    async def _on_904(self, msg: IRCMessage) -> None:
        """ERR_SASLFAIL."""
        logger.error("SASL authentication failed")
        self._sasl_done.set()
        await self.send_raw("CAP END")

    # ------------------------------------------------------------------
    # WHOIS tracking
    # ------------------------------------------------------------------

    async def _on_whois_311(self, msg: IRCMessage) -> None:
        """RPL_WHOISUSER."""
        if len(msg.params) < 2:
            return
        nick = msg.params[1].lower()
        self._whois_data.setdefault(nick, {})
        if len(msg.params) >= 4:
            self._whois_data[nick]["user"] = msg.params[2]
            self._whois_data[nick]["host"] = msg.params[3]

    async def _on_whois_330(self, msg: IRCMessage) -> None:
        """RPL_WHOISACCOUNT — nick is logged in as account."""
        if len(msg.params) < 3:
            return
        nick = msg.params[1].lower()
        account = msg.params[2]
        self._whois_data.setdefault(nick, {})
        self._whois_data[nick]["account"] = account

    async def _on_whois_318(self, msg: IRCMessage) -> None:
        """RPL_ENDOFWHOIS — resolve any waiting futures."""
        if len(msg.params) < 2:
            return
        nick = msg.params[1].lower()
        data = self._whois_data.pop(nick, {})
        fut = self._whois_futures.pop(nick, None)
        if fut and not fut.done():
            fut.set_result(data)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def caps(self) -> frozenset[str]:
        return frozenset(self._caps_acked)
