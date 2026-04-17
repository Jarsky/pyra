"""
Anope IRC Services integration layer.

Sends commands to NickServ/ChanServ and resolves futures when
the corresponding numeric replies arrive.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot


class ServicesInterface:
    def __init__(self, bot: "PyraBot") -> None:
        self._bot = bot
        # Pending futures: keyed by (service, nick) waiting for a response
        self._pending_status: dict[str, asyncio.Future[int]] = {}

    async def nickserv_status(self, nick: str) -> int:
        """Return NickServ STATUS for nick (0-3): 0=unknown, 1=unidentified, 2=identified, 3=owner."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[int] = loop.create_future()
        self._pending_status[nick.lower()] = fut
        await self._bot.irc.privmsg("NickServ", f"STATUS {nick}")
        try:
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending_status.pop(nick.lower(), None)
            return 0

    def on_notice(self, source: str, text: str) -> None:
        """Call this from the bot's NOTICE handler to process service replies."""
        if source.lower() not in ("nickserv", "chanserv", "memoserv", "hostserv"):
            return

        # Parse NickServ STATUS reply: "STATUS <nick> <level>"
        if source.lower() == "nickserv" and text.upper().startswith("STATUS "):
            parts = text.split()
            if len(parts) >= 3:
                nick = parts[1].lower()
                try:
                    level = int(parts[2])
                except ValueError:
                    level = 0
                fut = self._pending_status.pop(nick, None)
                if fut and not fut.done():
                    fut.set_result(level)

    async def chanserv_op(self, channel: str, nick: str) -> None:
        await self._bot.irc.privmsg("ChanServ", f"OP {channel} {nick}")

    async def chanserv_deop(self, channel: str, nick: str) -> None:
        await self._bot.irc.privmsg("ChanServ", f"DEOP {channel} {nick}")

    async def chanserv_akick_add(self, channel: str, mask: str, reason: str = "") -> None:
        cmd = f"AKICK {channel} ADD {mask}"
        if reason:
            cmd += f" {reason}"
        await self._bot.irc.privmsg("ChanServ", cmd)

    async def chanserv_akick_del(self, channel: str, mask: str) -> None:
        await self._bot.irc.privmsg("ChanServ", f"AKICK {channel} DEL {mask}")

    async def memoserv_send(self, nick: str, message: str) -> None:
        await self._bot.irc.privmsg("MemoServ", f"SEND {nick} {message}")

    async def send_command(self, service: str, command: str) -> None:
        """Send an arbitrary command to a service (admin use only)."""
        await self._bot.irc.privmsg(service, command)
        logger.debug(f"Services: {service} <- {command}")
