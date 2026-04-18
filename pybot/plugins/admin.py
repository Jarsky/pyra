"""
Admin plugin — bot administration commands (owner/admin only).

All commands require a or n flag. Most are designed to be used
via /msg or from the partyline.
"""

from __future__ import annotations

from datetime import datetime

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command("join", privilege="a", help="Join a channel", usage="!join <#channel> [key]")
async def cmd_join(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !join <#channel> [key]")  # type: ignore[attr-defined]
        return
    channel = trigger.args[0]
    key = trigger.args[1] if len(trigger.args) > 1 else ""
    await bot.join(channel, key)  # type: ignore[attr-defined]


@plugin.command("part", privilege="a", help="Leave a channel", usage="!part [#channel] [reason]")
async def cmd_part(bot: object, trigger: Trigger) -> None:
    channel = trigger.args[0] if trigger.args else trigger.channel
    reason = " ".join(trigger.args[1:]) if len(trigger.args) > 1 else "Leaving"
    if not channel:
        await bot.reply(trigger, "Usage: !part [#channel] [reason]")  # type: ignore[attr-defined]
        return
    await bot.part(channel, reason)  # type: ignore[attr-defined]


@plugin.command("quit", privilege="n", help="Shut down the bot", usage="!quit [message]")
async def cmd_quit(bot: object, trigger: Trigger) -> None:
    message = " ".join(trigger.args) if trigger.args else "Shutdown by owner"
    await bot.quit(message)  # type: ignore[attr-defined]


@plugin.command("reload", privilege="n", help="Reload a plugin", usage="!reload <plugin>")
async def cmd_reload(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !reload <plugin>")  # type: ignore[attr-defined]
        return
    name = trigger.args[0].lower()
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    try:
        await bot.plugin_loader.reload(name)  # type: ignore[attr-defined]
        await bot.reply(trigger, f"Plugin '{name}' reloaded.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to reload '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command(
    "load", privilege="n", help="Load a plugin", usage="!load <plugin_name_or_path>"
)
async def cmd_load(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !load <plugin>")  # type: ignore[attr-defined]
        return
    name = trigger.args[0].lower()
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    from pathlib import Path

    # Search in built-in and extra dirs
    search_dirs = [Path(__file__).parent]
    extra = getattr(bot.config.plugins, "extra_dir", "")  # type: ignore[attr-defined]
    if extra:
        search_dirs.append(Path(extra))

    found = None
    for d in search_dirs:
        p = d / f"{name}.py"
        if p.exists():
            found = p
            break

    if not found:
        await bot.reply(trigger, f"Plugin file '{name}.py' not found.")  # type: ignore[attr-defined]
        return

    try:
        await bot.plugin_loader.load(name, found)  # type: ignore[attr-defined]
        await bot.reply(trigger, f"Plugin '{name}' loaded.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to load '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command(
    "unload", privilege="n", help="Unload a plugin", usage="!unload <plugin>"
)
async def cmd_unload(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !unload <plugin>")  # type: ignore[attr-defined]
        return
    name = trigger.args[0].lower()
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    if name in ("admin",):
        await bot.reply(trigger, "Cannot unload the admin plugin.")  # type: ignore[attr-defined]
        return
    try:
        await bot.plugin_loader.unload(name)  # type: ignore[attr-defined]
        await bot.reply(trigger, f"Plugin '{name}' unloaded.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to unload '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command("plugins", privilege="a", help="List all loaded plugins")
async def cmd_plugins(bot: object, trigger: Trigger) -> None:
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    loaded = bot.plugin_loader.get_loaded_plugins()  # type: ignore[attr-defined]
    if not loaded:
        await bot.reply(trigger, "No plugins loaded.")  # type: ignore[attr-defined]
        return
    await bot.notice(trigger.nick, f"Loaded plugins ({len(loaded)}):")  # type: ignore[attr-defined]
    for name in sorted(loaded.keys()):
        await bot.notice(trigger.nick, f"  {name}")  # type: ignore[attr-defined]


@plugin.command(
    "say",
    privilege="a",
    help="Make the bot say something",
    usage="!say <target> <message>",
)
async def cmd_say(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !say <target> <message>")  # type: ignore[attr-defined]
        return
    target = trigger.args[0]
    message = " ".join(trigger.args[1:])
    await bot.say(target, message)  # type: ignore[attr-defined]


@plugin.command(
    "me",
    privilege="a",
    help="Make the bot perform an action",
    usage="!me <target> <action>",
)
async def cmd_me(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !me <target> <action>")  # type: ignore[attr-defined]
        return
    target = trigger.args[0]
    action = " ".join(trigger.args[1:])
    await bot.action(target, action)  # type: ignore[attr-defined]


@plugin.command(
    "raw",
    privilege="n",
    help="Send a raw IRC line (owner only)",
    usage="!raw <line>",
)
async def cmd_raw(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !raw <line>")  # type: ignore[attr-defined]
        return
    line = " ".join(trigger.args)
    await bot.raw(line)  # type: ignore[attr-defined]


@plugin.command(
    "announce",
    privilege="a",
    help="Send a message to all joined channels",
    usage="!announce <message>",
)
async def cmd_announce(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !announce <message>")  # type: ignore[attr-defined]
        return
    message = " ".join(trigger.args)
    for channel_name in bot.channels:  # type: ignore[attr-defined]
        await bot.say(channel_name, message)  # type: ignore[attr-defined]


@plugin.command("version", help="Show bot version")
async def cmd_version(bot: object, trigger: Trigger) -> None:
    from pybot import __version__

    await bot.say(trigger.target, f"Pyra IRC Bot v{__version__} — https://github.com/Jarsky/pyra")  # type: ignore[attr-defined]


@plugin.command(
    "ignore",
    privilege="a",
    help="Ignore a hostmask",
    usage="!ignore <mask> [duration]",
)
async def cmd_ignore(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !ignore <mask> [duration]")  # type: ignore[attr-defined]
        return
    from datetime import datetime, timezone

    from pybot.core.database import Ignore, get_session

    mask = trigger.args[0]
    expires_at = None
    if len(trigger.args) > 1:
        expires_at = _parse_duration(trigger.args[1])

    async with get_session() as session:
        session.add(
            Ignore(
                hostmask=mask,
                reason=" ".join(trigger.args[2:]) if len(trigger.args) > 2 else "",
                set_by=trigger.hostmask,
                set_at=datetime.now(tz=timezone.utc),
                expires_at=expires_at,
                active=True,
            )
        )
    await bot.reply(trigger, f"Added ignore for {mask}.")  # type: ignore[attr-defined]


@plugin.command(
    "unignore",
    privilege="a",
    help="Remove a hostmask ignore",
    usage="!unignore <mask>",
)
async def cmd_unignore(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !unignore <mask>")  # type: ignore[attr-defined]
        return
    from sqlalchemy import select

    from pybot.core.database import Ignore, get_session

    mask = trigger.args[0]
    async with get_session() as session:
        result = await session.execute(
            select(Ignore).where(Ignore.hostmask == mask, Ignore.active == True)  # noqa: E712
        )
        for ignore in result.scalars().all():
            ignore.active = False
    await bot.reply(trigger, f"Removed ignore for {mask}.")  # type: ignore[attr-defined]


@plugin.command("ignores", privilege="a", help="List active ignores")
async def cmd_ignores(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Ignore, get_session

    async with get_session() as session:
        result = await session.execute(select(Ignore).where(Ignore.active == True))  # noqa: E712
        ignores = result.scalars().all()

    if not ignores:
        await bot.reply(trigger, "No active ignores.")  # type: ignore[attr-defined]
        return
    for ig in ignores:
        line = f"{ig.hostmask}"
        if ig.reason:
            line += f" — {ig.reason}"
        if ig.expires_at:
            line += f" (expires {ig.expires_at.strftime('%Y-%m-%d %H:%M UTC')})"
        await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]


@plugin.command(
    "adduser",
    privilege="n",
    help="Add a user with flags",
    usage="!adduser <nick!user@host> <flags>",
)
async def cmd_adduser(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !adduser <nick!user@host> <flags>")  # type: ignore[attr-defined]
        return
    mask = trigger.args[0]
    flags_str = trigger.args[1]

    from pybot.core.database import get_session
    from pybot.core.permissions import add_flag

    nick = mask.split("!")[0] if "!" in mask else mask

    async with get_session() as session:
        for flag in flags_str:
            try:
                await add_flag(session, trigger.hostmask, mask, flag)
            except PermissionError as e:
                await bot.reply(trigger, str(e))  # type: ignore[attr-defined]
                return

    await bot.reply(trigger, f"Added user {nick} with flags: {flags_str}")  # type: ignore[attr-defined]


@plugin.command(
    "deluser",
    privilege="n",
    help="Remove a user record",
    usage="!deluser <nick>",
)
async def cmd_deluser(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !deluser <nick>")  # type: ignore[attr-defined]
        return
    nick = trigger.args[0]
    from sqlalchemy import select

    from pybot.core.database import User, get_session

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == nick))
        user = result.scalar_one_or_none()
        if user:
            await session.delete(user)
            await bot.reply(trigger, f"Deleted user {nick}.")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"User '{nick}' not found.")  # type: ignore[attr-defined]


@plugin.command(
    "whois",
    privilege="a",
    help="Show user record and flags",
    usage="!whois <nick>",
)
async def cmd_whois(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !whois <nick>")  # type: ignore[attr-defined]
        return
    nick = trigger.args[0]
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import User, get_session

    async with get_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.flags)).where(User.nick == nick)
        )
        user = result.scalar_one_or_none()

    if not user:
        await bot.reply(trigger, f"No record for '{nick}'.")  # type: ignore[attr-defined]
        return

    await bot.notice(trigger.nick, f"Nick: {user.nick}")  # type: ignore[attr-defined]
    await bot.notice(trigger.nick, f"Hostmask: {user.hostmask}")  # type: ignore[attr-defined]
    if user.account:
        await bot.notice(trigger.nick, f"Account: {user.account}")  # type: ignore[attr-defined]
    global_flags = user.global_flags or ""
    for f in user.flags:
        if f.channel is None:
            global_flags += f.flag
    if global_flags:
        await bot.notice(trigger.nick, f"Global flags: {global_flags}")  # type: ignore[attr-defined]
    chan_flags = [(f.channel, f.flag) for f in user.flags if f.channel]
    for ch, fl in chan_flags:
        await bot.notice(trigger.nick, f"Channel flag: {ch} +{fl}")  # type: ignore[attr-defined]
    if user.last_seen:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick,
            f"Last seen: {user.last_seen.strftime('%Y-%m-%d %H:%M UTC')} "
            f"in {user.last_seen_where or '?'}",
        )


@plugin.command("servers", privilege="a", help="List configured IRC servers")
async def cmd_servers(bot: object, trigger: Trigger) -> None:
    servers = bot.config.servers  # type: ignore[attr-defined]
    for s in servers:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick,
            f"{s.host}:{s.port} SSL={s.ssl} priority={s.priority}",
        )


@plugin.command(
    "services",
    privilege="a",
    help="Send a command to IRC services",
    usage="!services <NickServ|ChanServ|...> <command>",
)
async def cmd_services(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !services <service> <command>")  # type: ignore[attr-defined]
        return
    service = trigger.args[0]
    command = " ".join(trigger.args[1:])
    await bot.say(service, command)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_duration(s: str) -> datetime:
    from datetime import timedelta, timezone

    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if s[-1].lower() in unit_map:
        try:
            seconds = int(s[:-1]) * unit_map[s[-1].lower()]
            return datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
        except ValueError:
            pass
    raise ValueError(f"Invalid duration: {s!r}. Use e.g. 10m, 2h, 1d")
