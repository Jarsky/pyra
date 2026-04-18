"""
Eggdrop-style party line admin interface over Telnet (asyncio TCP server).

Admins connect with telnet to 127.0.0.1:3333, authenticate with username/password,
and get a real-time stream of IRC events plus a command interface.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot
    from pybot.core.irc import IRCMessage

MAX_LOGIN_ATTEMPTS = 3
SESSION_TIMEOUT = 1800  # 30 minutes idle

_PARTYLINE_BANNER = (
    "\r\n"
    "\033[1;32m"
    "  ██████╗ ██╗   ██╗██████╗  █████╗ \r\n"
    "  ██╔══██╗╚██╗ ██╔╝██╔══██╗██╔══██╗\r\n"
    "  ██████╔╝ ╚████╔╝ ██████╔╝███████║\r\n"
    "  ██╔═══╝   ╚██╔╝  ██╔══██╗██╔══██║\r\n"
    "  ██║        ██║   ██║  ██║██║  ██║\r\n"
    "  ╚═╝        ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝\r\n"
    "\033[0m"
    "\033[1;32m  Pyra IRC Bot Partyline\r\n"
    "  ─────────────────────────────────\033[0m\r\n\r\n"
)


class PartylineServer:
    def __init__(self, bot: "PyraBot") -> None:
        self._bot = bot
        self._sessions: list["PartylineSession"] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        cfg = self._bot.config.partyline
        if cfg.host == "0.0.0.0":  # noqa: S104
            logger.warning(
                "SECURITY WARNING: Partyline is bound to 0.0.0.0! "
                "This exposes the admin console to the network. "
                "Recommend binding to 127.0.0.1."
            )
        self._server = await asyncio.start_server(
            self._handle_connection,
            cfg.host,
            cfg.port,
        )
        logger.info(f"Partyline listening on {cfg.host}:{cfg.port}")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        for session in list(self._sessions):
            await session.disconnect("Server shutting down")
        if self._server:
            self._server.close()

    async def broadcast(self, message: str, exclude: "PartylineSession | None" = None) -> None:
        """Send a message to all authenticated partyline sessions."""
        for session in list(self._sessions):
            if session.authenticated and session is not exclude:
                await session.send(message)

        for queue in list(getattr(self, "_ws_queues", [])):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                continue

    async def on_irc_message(self, msg: "IRCMessage") -> None:
        """Called by the bot for every incoming IRC message — stream to sessions."""
        if not self._sessions:
            return

        line = _format_irc_event(msg)
        if line:
            for session in list(self._sessions):
                if session.authenticated:
                    await session.send(line)
            for queue in list(getattr(self, "_ws_queues", [])):
                try:
                    queue.put_nowait(line)
                except asyncio.QueueFull:
                    continue

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername", ("?", "?"))
        logger.info(f"Partyline: connection from {peer[0]}:{peer[1]}")
        session = PartylineSession(reader, writer, self._bot, self)
        self._sessions.append(session)
        try:
            await session.run()
        finally:
            self._sessions.remove(session)
            logger.info(f"Partyline: {peer[0]} disconnected")


class PartylineSession:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        bot: "PyraBot",
        server: PartylineServer,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._bot = bot
        self._server = server
        self.authenticated = False
        self.nick: str | None = None
        self._last_activity = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        if not await self._authenticate():
            return
        await self.send(
            f"\r\nWelcome to the Pyra partyline, {self.nick}!\r\n"
            "Type .help to list commands, or * message to chat.\r\n"
            "Use .quit to disconnect.\r\n"
        )
        await self._server.broadcast(f"*** {self.nick} joined the partyline", exclude=self)

        try:
            while True:
                # Idle timeout check
                if asyncio.get_event_loop().time() - self._last_activity > SESSION_TIMEOUT:
                    await self.send("Session timed out.\r\n")
                    break

                try:
                    data = await asyncio.wait_for(self._reader.readline(), timeout=30)
                except asyncio.TimeoutError:
                    continue

                if not data:
                    break

                line = data.decode("utf-8", errors="replace").strip()
                self._last_activity = asyncio.get_event_loop().time()

                if not line:
                    continue
                await self._handle_line(line)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Partyline session error: {exc}")
        finally:
            if self.nick:
                await self._server.broadcast(f"*** {self.nick} left the partyline", exclude=self)
            self._writer.close()

    async def disconnect(self, reason: str = "") -> None:
        if reason:
            await self.send(f"\r\n{reason}\r\n")
        self._writer.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> bool:
        await self.send(_PARTYLINE_BANNER + "Login: ")
        attempts = 0
        while attempts < MAX_LOGIN_ATTEMPTS:
            try:
                username_data = await asyncio.wait_for(self._reader.readline(), timeout=60)
            except asyncio.TimeoutError:
                await self.send("\r\nTimeout.\r\n")
                return False

            username = username_data.decode("utf-8", errors="replace").strip()
            if not username:
                continue

            await self.send("Password: ")
            try:
                password_data = await asyncio.wait_for(self._reader.readline(), timeout=60)
            except asyncio.TimeoutError:
                await self.send("\r\nTimeout.\r\n")
                return False

            password = password_data.decode("utf-8", errors="replace").strip()

            if await self._verify_credentials(username, password):
                self.authenticated = True
                self.nick = username
                return True

            attempts += 1
            remaining = MAX_LOGIN_ATTEMPTS - attempts
            if remaining > 0:
                await self.send(
                    f"Invalid credentials. {remaining} attempt(s) remaining.\r\nLogin: "
                )
            else:
                await self.send("Too many failed attempts. Goodbye.\r\n")
                return False

        return False

    async def _verify_credentials(self, username: str, password: str) -> bool:
        from sqlalchemy import select

        from pybot.core.database import User, get_session
        from pybot.web.auth import verify_password

        owner_nick = self._bot.config.core.owner.strip()
        owner_password = self._bot.config.partyline.password.get_secret_value().strip()
        if username == owner_nick and owner_password and password == owner_password:
            return True

        try:
            async with get_session() as session:
                result = await session.execute(select(User).where(User.nick == username))
                user = result.scalar_one_or_none()

            if not user or not user.password_hash:
                return False

            return verify_password(password, user.password_hash)
        except Exception as exc:
            logger.error(f"Partyline auth error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    async def _handle_line(self, line: str) -> None:
        # Partyline chat (lines starting with *)
        if line.startswith("*"):
            message = line[1:].strip()
            await self._server.broadcast(f"[partyline] <{self.nick}> {message}")
            return

        await execute_partyline_command(
            bot=self._bot,
            actor=self.nick or "",
            line=line,
            send=self.send,
            is_owner=self._is_owner(),
            admin_count=lambda: len([s for s in self._server._sessions if s.authenticated]),
            channel_names=lambda: sorted(ch.name for ch in self._bot.channels.values()),
            close=self._writer.close,
            line_ending="\r\n",
        )

    def _is_owner(self) -> bool:
        return bool(self.nick and self.nick.lower() == self._bot.config.core.owner.lower())

    async def _cmd_who(self) -> None:
        sessions = [s for s in self._server._sessions if s.authenticated]
        await self.send(f"Connected admins ({len(sessions)}):\r\n")
        for s in sessions:
            marker = " (you)" if s is self else ""
            await self.send(f"  {s.nick}{marker}\r\n")

    async def _cmd_channels(self) -> None:
        channels = self._bot.channels
        await self.send(f"Joined channels ({len(channels)}):\r\n")
        for _name, ch in channels.items():
            await self.send(f"  {ch.name} — {len(ch.nicks)} users  {ch.topic[:60]}\r\n")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    async def send(self, message: str) -> None:
        try:
            self._writer.write(message.encode("utf-8", errors="replace"))
            await self._writer.drain()
        except Exception as e:
            logger.debug(f"Failed to send to partyline: {e}")


# ---------------------------------------------------------------------------
# IRC event formatter for streaming to partyline
# ---------------------------------------------------------------------------


def _format_irc_event(msg: "IRCMessage") -> str | None:
    if msg.command == "PRIVMSG":
        channel = msg.channel or msg.params[0] if msg.params else ""
        if msg.text.startswith("\x01ACTION "):
            action = msg.text[8:].rstrip("\x01")
            return f"[{channel}] * {msg.nick} {action}"
        return f"[{channel}] <{msg.nick}> {msg.text}"
    elif msg.command == "JOIN":
        channel = msg.params[0] if msg.params else msg.text
        return f"*** {msg.nick} ({msg.hostmask}) joined {channel}"
    elif msg.command == "PART":
        channel = msg.params[0] if msg.params else ""
        return f"*** {msg.nick} left {channel} ({msg.text})"
    elif msg.command == "QUIT":
        return f"*** {msg.nick} quit ({msg.text})"
    elif msg.command == "NICK":
        return f"*** {msg.nick} is now known as {msg.text}"
    elif msg.command == "KICK":
        channel = msg.params[0] if msg.params else ""
        kicked = msg.params[1] if len(msg.params) > 1 else "?"
        return f"*** {msg.nick} kicked {kicked} from {channel} ({msg.text})"
    elif msg.command == "MODE":
        target = msg.params[0] if msg.params else "?"
        modes = " ".join(msg.params[1:]) if len(msg.params) > 1 else ""
        return f"*** {msg.nick} set mode {modes} on {target}"
    elif msg.command == "TOPIC":
        channel = msg.params[0] if msg.params else "?"
        return f"*** {msg.nick} changed topic of {channel}: {msg.text}"
    return None


async def execute_partyline_command(
    *,
    bot: "PyraBot",
    actor: str,
    line: str,
    send: Callable[[str], Awaitable[None]],
    is_owner: bool,
    admin_count: Callable[[], int],
    channel_names: Callable[[], list[str]],
    close: Callable[[], None | Awaitable[None]] | None = None,
    line_ending: str = "\n",
) -> None:
    lower = line.lower().strip()
    if not lower.startswith("."):
        await send(f"Unknown command. Use .help{line_ending}")
        return

    cmd, _, _ = lower[1:].partition(" ")
    raw_args = line[1 + len(cmd) :].strip()

    if cmd == "quit":
        await send(f"Goodbye!{line_ending}")
        if close is not None:
            close_result = close()
            if inspect.isawaitable(close_result):
                await close_result
        return

    if cmd == "help":
        await send(
            "Commands:" + line_ending
            + "  .help" + line_ending
            + "  .who" + line_ending
            + "  .channels" + line_ending
            + "  .say <#chan> <message>" + line_ending
            + "  .join <#chan>" + line_ending
            + "  .part <#chan>" + line_ending
            + "  .reload               (reload config + all plugins)" + line_ending
            + "  .restart              (owner only)" + line_ending
            + "  .shutdown             (owner only)" + line_ending
            + "  .raw <line>           (owner only)" + line_ending
            + "  .quit" + line_ending
        )
        return

    if cmd == "who":
        await send(f"Connected admins: {admin_count()}{line_ending}")
        return

    if cmd == "channels":
        channels = ", ".join(channel_names()) or "(none)"
        await send(f"Channels: {channels}{line_ending}")
        return

    if cmd == "say":
        parts = raw_args.split(None, 1)
        if len(parts) == 2:
            await bot.say(parts[0], parts[1])
            await send(f"Sent to {parts[0]}: {parts[1]}{line_ending}")
        else:
            await send(f"Usage: .say <#chan> <message>{line_ending}")
        return

    if cmd == "join":
        await bot.join(raw_args)
        await send(f"Joining {raw_args}...{line_ending}")
        return

    if cmd == "part":
        await bot.part(raw_args)
        await send(f"Parting {raw_args}...{line_ending}")
        return

    if cmd == "reload":
        try:
            await bot.reload_runtime()
            await send(f"Reloaded config and all plugins.{line_ending}")
        except Exception as exc:
            await send(f"Reload error: {exc}{line_ending}")
        return

    if cmd == "raw":
        if not is_owner:
            await send(f"Permission denied (owner only).{line_ending}")
            return
        await bot.raw(raw_args)
        await send(f"Sent: {raw_args}{line_ending}")
        return

    if cmd == "shutdown":
        if not is_owner:
            await send(f"Permission denied (owner only).{line_ending}")
            return
        await send(f"Shutting down bot...{line_ending}")
        await bot.shutdown_process(f"Shutdown by {actor or 'partyline admin'}")
        return

    if cmd == "restart":
        if not is_owner:
            await send(f"Permission denied (owner only).{line_ending}")
            return
        await send(f"Restarting bot...{line_ending}")
        await bot.restart_process()
        return

    await send(f"Unknown command. Use .help{line_ending}")
