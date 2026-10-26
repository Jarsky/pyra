"""
Partyline user and channel commands (Eggdrop style): .chattr, .+user/.-user, .+host/.-host,
.match, .whois, .chanset, .chaninfo, .status.

Partyline sessions are already limited to admins and owners; the same permission rules as
on IRC apply (only owners touch owners or the n flag) and every change is audited.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot

# Known channel settings and their defaults (shown by .chaninfo)
CHANNEL_SETTINGS = {
    "greet": "false",
    "greet_msg": "Welcome to {channel}, {nick}!",
    "antispam": "true",
    "url_titles": "true",
    "autoop": "false",
    "autovoice": "false",
    "enforce_bans": "true",
    "flood_join": "5:60",
    "flood_nick": "5:60",
    "flood_ctcp": "3:60",
    "flood_ban_minutes": "10",
    "autolimit": "0",
    "chanmode": "",
    "bitch": "false",
    "protectops": "false",
}

HELP = [
    "  .chattr <user|mask> [+flags-flags] [#chan]  show/change flags",
    "  .+user <name> <nick!user@host>   add a user (no flags)",
    "  .-user <name>                    delete a user (owner only)",
    "  .+host / .-host <user> <mask>    add/remove a user's hostmask",
    "  .match <pattern>                 find users by name or hostmask",
    "  .whois <user>                    a user's hosts and flags",
    "  .chanset <#chan> <key> <value>   or  .chanset <#chan> +bitch / -protectops",
    "  .chaninfo <#chan>                channel settings",
    "  .status                          bot status",
]

COMMANDS = {
    "chattr",
    "+user",
    "-user",
    "+host",
    "-host",
    "match",
    "whois",
    "chanset",
    "chaninfo",
    "status",
}

Send = Callable[[str], Awaitable[None]]


async def run(
    bot: "PyraBot", cmd: str, args: list[str], actor: str, is_owner: bool, say: Send, source: str
) -> None:
    """Run one partyline user/channel command; say() writes one line to the session."""
    actor_flags = {"n"} if is_owner else {"a"}
    try:
        if cmd == "chattr":
            await _chattr(args, actor, actor_flags, say, source)
        elif cmd in ("+user", "-user"):
            await _user(cmd, args, actor, is_owner, say, source)
        elif cmd in ("+host", "-host"):
            await _host(cmd, args, actor, actor_flags, say, source)
        elif cmd == "match":
            await _match(args, say)
        elif cmd == "whois":
            await _whois(args, say)
        elif cmd == "chanset":
            await _chanset(args, actor, say, source)
        elif cmd == "chaninfo":
            await _chaninfo(args, say)
        elif cmd == "status":
            await _status(bot, say)
    except (PermissionError, ValueError) as exc:
        await say(str(exc))


async def _find(session: Any, target: str) -> Any:
    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import User
    from pybot.core.permissions import find_user_by_mask

    if "!" in target:
        user = await find_user_by_mask(session, target)
        if user is None:
            raise ValueError(f"No user registered with mask {target}")
        return user
    result = await session.execute(
        select(User)
        .options(selectinload(User.hosts))
        .where(func.lower(User.nick) == target.lower())
    )
    users = result.scalars().all()
    if len(users) != 1:
        raise ValueError(
            f"No user named {target}" if not users else f"Several users named {target}: use a mask"
        )
    return users[0]


def _fmt(flags: set[str]) -> str:
    return "+" + "".join(sorted(flags)) if flags else "none"


async def _chattr(
    args: list[str], actor: str, actor_flags: set[str], say: Send, source: str
) -> None:
    if not args:
        raise ValueError("Usage: .chattr <user|mask> [+flags-flags] [#chan]")
    target, rest = args[0], args[1:]
    channel = next((a for a in rest if a[:1] in ("#", "&")), None)
    change = next((a for a in rest if a[:1] in ("+", "-")), None)

    from pybot.core.database import get_session
    from pybot.core.permissions import get_user_flags, grant_flag, revoke_flag

    async with get_session() as session:
        user = await _find(session, target)
        if change:
            sign = "+"
            for char in change:
                if char in "+-":
                    sign = char
                elif not char.isalpha():
                    raise ValueError(f"'{char}' is not a flag")
                elif sign == "+":
                    await grant_flag(session, actor_flags, user, char, channel, actor, source)
                else:
                    await revoke_flag(session, actor_flags, user, char, channel, actor, source)
            await session.flush()
        flags = await get_user_flags(session, user, channel)
        name = user.nick
    where = f" on {channel} (incl. global)" if channel else ""
    await say(f"{name} flags{where}: {_fmt(flags)}")


async def _user(
    cmd: str, args: list[str], actor: str, is_owner: bool, say: Send, source: str
) -> None:
    from sqlalchemy import func, select

    from pybot.core.audit import audit
    from pybot.core.database import User, get_session
    from pybot.core.permissions import find_user_by_mask

    async with get_session() as session:
        if cmd == "+user":
            if len(args) != 2 or "!" not in args[1] or "@" not in args[1]:
                raise ValueError("Usage: .+user <name> <nick!user@host>")
            name, mask = args
            exists = await session.execute(
                select(User).where(func.lower(User.nick) == name.lower())
            )
            if exists.scalars().first() or await find_user_by_mask(session, mask):
                raise ValueError(f"A user named {name} or with mask {mask} already exists")
            session.add(
                User(
                    nick=name,
                    hostmask=mask,
                    global_flags="",
                    created_at=datetime.now(tz=timezone.utc),
                )
            )
            await audit(actor, "user.add", name, mask, source, session)
            await say(f"Added user {name} [{mask}] (no flags; use .chattr)")
        else:
            if not is_owner:
                raise PermissionError("Only owners can delete users")
            if len(args) != 1:
                raise ValueError("Usage: .-user <name>")
            user = await _find(session, args[0])
            if user.nick.lower() == actor.lower():
                raise ValueError("You can't delete your own user")
            await session.delete(user)
            await audit(actor, "user.delete", user.nick, user.hostmask or "", source, session)
            await say(f"Deleted user {user.nick}")


async def _host(
    cmd: str, args: list[str], actor: str, actor_flags: set[str], say: Send, source: str
) -> None:
    from pybot.core.database import get_session
    from pybot.core.permissions import add_host, remove_host

    if len(args) != 2:
        raise ValueError(f"Usage: .{cmd} <user> <nick!user@host>")
    async with get_session() as session:
        user = await _find(session, args[0])
        if cmd == "+host":
            done = await add_host(session, actor_flags, user, args[1], actor, source)
        else:
            done = await remove_host(session, actor_flags, user, args[1], actor, source)
        await session.flush()
        await session.refresh(user, ["hosts"])
        masks, name = user.all_masks, user.nick
    verb = (
        ("Added" if done else "Already had")
        if cmd == "+host"
        else ("Removed" if done else "No such host")
    )
    await say(f"{verb} {args[1]}. {name} hosts: {', '.join(masks) or 'none'}")


async def _match(args: list[str], say: Send) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import User, get_session
    from pybot.core.permissions import match_hostmask

    if len(args) != 1:
        raise ValueError("Usage: .match <pattern>  (wildcards * and ?)")
    pattern = args[0] if any(c in args[0] for c in "*?") else f"*{args[0]}*"
    async with get_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.hosts), selectinload(User.flags))
            .order_by(User.nick)
        )
        found = [
            u
            for u in result.scalars().all()
            if match_hostmask(pattern, u.nick)
            or any(match_hostmask(pattern, m) for m in u.all_masks)
        ]
    for user in found[:20]:
        flags = set(user.global_flags or "") | {f.flag for f in user.flags if not f.channel}
        await say(f"{user.nick} [{', '.join(user.all_masks) or 'account only'}] {_fmt(flags)}")
    tail = f" (showing 20 of {len(found)})" if len(found) > 20 else ""
    await say(f"{len(found)} match(es){tail}")


async def _whois(args: list[str], say: Send) -> None:
    from sqlalchemy.orm import selectinload  # noqa: F401  (loaded by _find)

    from pybot.core.database import get_session

    if len(args) != 1:
        raise ValueError("Usage: .whois <user>")
    async with get_session() as session:
        user = await _find(session, args[0])
        await session.refresh(user, ["flags"])
        global_flags = set(user.global_flags or "") | {f.flag for f in user.flags if not f.channel}
        by_channel: dict[str, str] = {}
        for f in user.flags:
            if f.channel:
                by_channel[f.channel] = by_channel.get(f.channel, "") + f.flag
        await say(
            f"{user.nick}: hosts {', '.join(user.all_masks) or 'none'}"
            + (f" | account {user.account}" if user.account else "")
        )
        await say(
            f"Flags: global {_fmt(global_flags)}"
            + "".join(f" | {ch} +{''.join(sorted(fl))}" for ch, fl in sorted(by_channel.items()))
        )


async def _chanset(args: list[str], actor: str, say: Send, source: str) -> None:
    from pybot.core.audit import audit
    from pybot.core.database import get_session, set_channel_setting

    if len(args) < 2 or args[0][:1] not in ("#", "&"):
        raise ValueError("Usage: .chanset <#chan> <key> <value>  or  .chanset <#chan> +bitch")
    channel = args[0]
    if args[1][:1] in ("+", "-") and len(args) == 2:  # Eggdrop style booleans: +bitch / -bitch
        key, value = args[1][1:], "true" if args[1][0] == "+" else "false"
    else:
        key, value = args[1], " ".join(args[2:])
    if not key or not key.replace("_", "").isalnum():
        raise ValueError(f"'{key}' is not a setting name")
    async with get_session() as session:
        await set_channel_setting(session, channel, key, value)
        await audit(actor, "channel.settings", channel, f"{key}={value}", source, session)
    await say(f"{channel}: {key} = {value!r}")


async def _chaninfo(args: list[str], say: Send) -> None:
    from sqlalchemy import select

    from pybot.core.database import Channel, ChannelSetting, get_session

    if len(args) != 1 or args[0][:1] not in ("#", "&"):
        raise ValueError("Usage: .chaninfo <#chan>")
    async with get_session() as session:
        result = await session.execute(
            select(ChannelSetting.key, ChannelSetting.value)
            .join(Channel)
            .where(Channel.name == args[0].lower())
        )
        stored = dict(result.all())
    values = {**CHANNEL_SETTINGS, **stored}
    items = [
        f"{k}={v!r}" + ("" if k in stored else "*")
        for k, v in sorted(values.items())
        if k not in ("autojoin", "join_key")
    ]
    await say(f"{args[0]} settings (* = default): {'; '.join(items)}")


async def _status(bot: "PyraBot", say: Send) -> None:
    uptime = int(bot.uptime_seconds)
    server = bot.irc.current_server
    nick = bot.nick + (
        ""
        if bot.nick.lower() == bot.config.core.nick.lower()
        else f" (wants {bot.config.core.nick})"
    )
    link = "connected" if bot.irc.registered else "not connected"
    await say(
        f"Pyra: nick {nick} on {server.host}:{server.port}, {link}, "
        f"up {uptime // 3600}h {uptime % 3600 // 60}m"
    )
    channels = [
        f"{state.name} ({len(state.nicks)}{', op' if bot.can_set_modes(state.name) else ''})"
        for state in bot.channels.values()
    ]
    await say(f"Channels: {', '.join(channels) or 'none'}")
    loader = bot.plugin_loader
    plugins = len(loader.get_loaded_plugins()) if loader else 0
    jobs = len(bot.scheduler.list_jobs()) if bot.scheduler else 0
    retention = bot.config.database.log_retention_days
    await say(
        f"Plugins: {plugins} loaded, {len(loader.get_errors()) if loader else 0} with errors; "
        f"jobs: {jobs}; message log kept {f'{retention} days' if retention else 'forever'}"
    )
