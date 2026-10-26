"""
Admin plugin — bot administration commands (owner/admin only).

Author:  Jarsky
Version: 1.3.0
Date:    2026-10-26

All commands require 'a' (admin) or 'n' (owner) flag.
Most are designed to be used via /msg or from the partyline.

Commands:
  !join <#channel> [key]          Join a channel
  !part [#channel] [reason]       Part a channel
  !say <target> <message>         Make the bot say something
  !raw <line>                     Send raw IRC line (owner only)
  !reload <plugin>                Reload a plugin
  !load / !unload <plugin>        Load or unload a plugin (remembered across restarts)
  !plugin <on|off> <plugin> [#chan]  Switch a plugin on/off in one channel (channel op)
  !plugins                        List loaded/available plugins, errors and warnings
  !quit [message]                 Disconnect and exit (owner only)
  !adduser <nick!user@host> <flags> [name]  Add a user with global flags (owner)
  !deluser <nick>                 Remove a user (owner)
  !flags <nick|mask> [#channel] [+flags-flags]  Show or change flags (channel masters:
                                  o/v/k/b in their channel)
  !whois <nick>                   Show a user's hosts, account and flags
  !host <list|add|del> <user> [mask]  Manage a user's hostmasks
  !audit [count] [actor|action.]  Recent admin actions
  !setpass <nick> <password>      Set Web UI/partyline password for a user (owner)
  !passwd <newpassword>           Change your own Web UI/partyline password
  !useserviceauth                 Bind your IRC services account as owner account (owner only)
    !jobs list                      Show scheduler jobs
    !jobs pause <plugin.func>       Pause a scheduler job
    !jobs resume <plugin.func>      Resume a scheduler job
    !memo <nick> <message>          Send a MemoServ memo
    !akick <add|del|list> ...       Manage ChanServ AKICK entries
"""

from __future__ import annotations

import re
import secrets
import string

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.3.0",
    "updated": "2026-10-26",
    "description": "Bot admin commands (join, part, say, reload, users, flags). Owner/admin only.",
    "url": "https://github.com/Jarsky/pyra",
}

from datetime import datetime
from typing import TYPE_CHECKING

from pybot import plugin
from pybot.plugin import Trigger

if TYPE_CHECKING:
    from pybot.core.database import User


@plugin.command(
    "join",
    privilege="a",
    help="Join a channel (rejoined on restart)",
    usage="!join <#channel> [key]",
)
async def cmd_join(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !join <#channel> [key]")  # type: ignore[attr-defined]
        return
    channel = trigger.args[0]
    key = trigger.args[1] if len(trigger.args) > 1 else ""
    await bot.join_channel(channel, key)  # type: ignore[attr-defined]
    await _audit(trigger, "channel.join", channel)


@plugin.command(
    "part", privilege="a", help="Leave a channel (not rejoined)", usage="!part [#channel] [reason]"
)
async def cmd_part(bot: object, trigger: Trigger) -> None:
    channel = trigger.args[0] if trigger.args else trigger.channel
    reason = " ".join(trigger.args[1:]) if len(trigger.args) > 1 else "Leaving"
    if not channel:
        await bot.reply(trigger, "Usage: !part [#channel] [reason]")  # type: ignore[attr-defined]
        return
    await bot.part_channel(channel, reason)  # type: ignore[attr-defined]
    await _audit(trigger, "channel.part", channel)


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
    if not _PLUGIN_NAME.fullmatch(name):
        await bot.reply(trigger, "Plugin names use letters, digits and underscores.")  # type: ignore[attr-defined]
        return
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    try:
        await bot.plugin_loader.reload(name)  # type: ignore[attr-defined]
        await bot.reply(trigger, f"Plugin '{name}' reloaded.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to reload '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command(
    "load", privilege="n", help="Load a plugin (remembered across restarts)", usage="!load <plugin>"
)
async def cmd_load(bot: object, trigger: Trigger) -> None:
    name = await _plugin_name_arg(bot, trigger, "!load <plugin>")
    if name is None:
        return
    loader = bot.plugin_loader  # type: ignore[attr-defined]
    await loader.rescan()  # pick up a file added a moment ago
    try:
        await loader.enable(name)
        await _audit(trigger, "plugin.load", name)
        await bot.reply(trigger, f"Plugin '{name}' loaded.")  # type: ignore[attr-defined]
    except KeyError:
        await bot.reply(trigger, f"Plugin file '{name}.py' not found.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to load '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command(
    "unload",
    privilege="n",
    help="Unload a plugin (remembered across restarts)",
    usage="!unload <plugin>",
)
async def cmd_unload(bot: object, trigger: Trigger) -> None:
    name = await _plugin_name_arg(bot, trigger, "!unload <plugin>")
    if name is None:
        return
    try:
        await bot.plugin_loader.disable(name)  # type: ignore[attr-defined]
        await _audit(trigger, "plugin.unload", name)
        await bot.reply(trigger, f"Plugin '{name}' unloaded.")  # type: ignore[attr-defined]
    except Exception as exc:
        await bot.reply(trigger, f"Failed to unload '{name}': {exc}")  # type: ignore[attr-defined]


@plugin.command(
    "plugin",
    privilege="o",
    help="Switch a plugin on or off in one channel",
    usage="!plugin <on|off> <plugin> [#channel]",
)
async def cmd_plugin(bot: object, trigger: Trigger) -> None:
    usage = "Usage: !plugin <on|off> <plugin> [#channel]"
    args = trigger.args
    if len(args) < 2 or args[0].lower() not in ("on", "off"):
        await bot.reply(trigger, usage)  # type: ignore[attr-defined]
        return
    turn_on, name = args[0].lower() == "on", args[1].lower()
    channel = args[2] if len(args) > 2 else trigger.channel
    if not channel or channel[:1] not in ("#", "&"):
        await bot.reply(trigger, usage)  # type: ignore[attr-defined]
        return
    from pybot.core.plugin_loader import PROTECTED_PLUGINS

    if name in PROTECTED_PLUGINS:
        await bot.reply(trigger, f"The {name} plugin can't be switched off.")  # type: ignore[attr-defined]
        return
    loader = bot.plugin_loader  # type: ignore[attr-defined]
    if not loader or name not in loader.get_available_plugins():
        await bot.reply(trigger, f"Unknown plugin '{name}'.")  # type: ignore[attr-defined]
        return
    # A channel op may only manage channels they are op in (trigger.channel was checked)
    if channel.lower() != (trigger.channel or "").lower() and not (
        trigger.admin or await trigger.has_flag("o", channel)
    ):
        await bot.reply(trigger, f"You need the o flag in {channel}.")  # type: ignore[attr-defined]
        return
    await bot.set_plugin_enabled_in_channel(name, channel, turn_on)  # type: ignore[attr-defined]
    state = "on" if turn_on else "off"
    await _audit(trigger, f"plugin.{state}", name, channel)
    await bot.reply(trigger, f"Plugin '{name}' is now {state} in {channel}.")  # type: ignore[attr-defined]


@plugin.command("plugins", privilege="a", help="List plugins: loaded, available, problems")
async def cmd_plugins(bot: object, trigger: Trigger) -> None:
    loader = bot.plugin_loader  # type: ignore[attr-defined]
    if not loader:
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return
    loaded = sorted(loader.get_loaded_plugins())
    idle = sorted(set(loader.get_available_plugins()) - set(loaded))
    lines = [f"Loaded ({len(loaded)}): {', '.join(loaded) or 'none'}"]
    if idle:
        lines.append(f"Not loaded ({len(idle)}): {', '.join(idle)}")
    if trigger.channel:
        off = sorted(
            p
            for p, ch in bot.disabled_plugin_channels  # type: ignore[attr-defined]
            if ch == trigger.channel.lower()
        )
        if off:
            lines.append(f"Off in {trigger.channel}: {', '.join(off)}")
    for name, error in sorted(loader.get_errors().items()):
        lines.append(f"Error in {name}: {error}")
    for name, warnings in sorted(loader.get_warnings().items()):
        lines.append(f"Warning for {name}: {'; '.join(warnings)}")
    for line in lines:  # long lines are split automatically
        await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]


async def _plugin_name_arg(bot: object, trigger: Trigger, usage: str) -> str | None:
    if not trigger.args:
        await bot.reply(trigger, f"Usage: {usage}")  # type: ignore[attr-defined]
        return None
    name = trigger.args[0].lower()
    if not _PLUGIN_NAME.fullmatch(name):
        await bot.reply(trigger, "Plugin names use letters, digits and underscores.")  # type: ignore[attr-defined]
        return None
    if not bot.plugin_loader:  # type: ignore[attr-defined]
        await bot.reply(trigger, "Plugin loader not available.")  # type: ignore[attr-defined]
        return None
    return name


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
        until = f"until {expires_at:%Y-%m-%d %H:%M} UTC" if expires_at else "permanent"
        await _audit(trigger, "ignore.add", mask, until, session)
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
        await _audit(trigger, "ignore.remove", mask, session=session)
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
    items = []
    for ig in ignores:
        notes = [ig.reason] if ig.reason else []
        if ig.expires_at:
            notes.append(f"until {ig.expires_at:%Y-%m-%d %H:%M} UTC")
        items.append(ig.hostmask + (f" ({', '.join(notes)})" if notes else ""))
    # One line; long lists are split automatically
    await bot.notice(trigger.nick, f"Ignores ({len(items)}): {'; '.join(items)}")  # type: ignore[attr-defined]


@plugin.command(
    "adduser",
    privilege="n",
    help="Add a user with flags (a login is generated for a/n users)",
    usage="!adduser <nick!user@host> <flags> [name]",
)
async def cmd_adduser(bot: object, trigger: Trigger) -> None:
    usage = "Usage: !adduser <nick!user@host> <flags> [name]"
    if len(trigger.args) < 2 or not re.fullmatch(r"\+?[A-Za-z]+", trigger.args[1]):
        await bot.reply(trigger, usage)  # type: ignore[attr-defined]
        return
    mask = trigger.args[0]
    flags_str = trigger.args[1].lstrip("+")
    nick_part = mask.split("!")[0] if "!" in mask else mask
    name = trigger.args[2] if len(trigger.args) > 2 else nick_part
    if any(c in name for c in "*?!@"):
        await bot.reply(  # type: ignore[attr-defined]
            trigger, f"That mask has no fixed nick; give the user a name. {usage}"
        )
        return

    from pybot.core.database import get_session
    from pybot.core.permissions import add_flag, find_user_by_mask
    from pybot.web.auth import hash_password

    # Only owners/admins can log in to the web UI and partyline
    needs_login = bool(set(flags_str) & {"a", "n"})
    generated_password = _generate_password() if needs_login else ""

    try:
        # Any refusal raises out of the session, which rolls back flags already added
        async with get_session() as session:
            for flag in flags_str:
                await add_flag(
                    session,
                    _actor_flags(trigger),
                    mask,
                    flag,
                    granted_by=trigger.hostmask,
                    nick=name,
                )
            user = await find_user_by_mask(session, mask)
            if user is not None and needs_login:
                user.password_hash = hash_password(generated_password)
            name = user.nick if user is not None else name
            await _audit(trigger, "user.add", name, f"{mask} +{flags_str}", session)
    except PermissionError as e:
        await bot.reply(trigger, str(e))  # type: ignore[attr-defined]
        return

    await bot.reply(trigger, f"Added user {name} [{mask}] with flags: {flags_str}")  # type: ignore[attr-defined]
    if needs_login:
        await _send_new_user_credentials(bot, name, mask, generated_password, trigger)


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
        users = result.scalars().all()
        if len(users) > 1:
            masks = ", ".join(u.hostmask or "(account only)" for u in users)
            await bot.reply(  # type: ignore[attr-defined]
                trigger, f"Several users are named '{nick}' ({masks}); delete them in the Web UI."
            )
            return
        if users:
            await session.delete(users[0])
            await _audit(trigger, "user.delete", nick, users[0].hostmask or "", session)
            await bot.reply(trigger, f"Deleted user {nick}.")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"User '{nick}' not found.")  # type: ignore[attr-defined]


_FLAGS_USAGE = "!flags <nick|mask> [#channel] [+flags-flags]"


@plugin.command(
    "flags",
    help="Show or change a user's flags (admins; channel masters for their channel)",
    usage=_FLAGS_USAGE,
)
async def cmd_flags(bot: object, trigger: Trigger) -> None:
    """Eggdrop's .chattr: '!flags bob', '!flags bob +ov', '!flags bob #chan +o-v'."""
    args = list(trigger.args)
    if not args:
        await bot.reply(trigger, f"Usage: {_FLAGS_USAGE}")  # type: ignore[attr-defined]
        return
    target = args.pop(0)
    channel = args.pop(0) if args and args[0][:1] in ("#", "&") else None
    change = args.pop(0) if args else None
    if args or (change is not None and not re.fullmatch(r"([+-][A-Za-z]+)+", change)):
        await bot.reply(trigger, f"Usage: {_FLAGS_USAGE}")  # type: ignore[attr-defined]
        return

    actor_flags = await _actor_flags_for(trigger, channel or trigger.channel)
    if not actor_flags & {"n", "a", "m"}:
        await bot.notice(trigger.nick, "Permission denied.")  # type: ignore[attr-defined]
        return
    if "m" in actor_flags and not actor_flags & {"n", "a"} and change is not None and not channel:
        channel = trigger.channel  # a master changes flags in their own channel
        if not channel:
            await bot.reply(trigger, f"Give the channel: {_FLAGS_USAGE}")  # type: ignore[attr-defined]
            return

    from sqlalchemy import func, select

    from pybot.core.database import UserFlag, get_session
    from pybot.core.permissions import get_user_flags, grant_flag, revoke_flag

    try:
        # A refused flag raises out of the session, rolling back the whole change
        async with get_session() as session:
            user, problem = await _find_user(session, target)
            if user is None:
                await bot.reply(trigger, problem)  # type: ignore[attr-defined]
                return
            if change is not None:
                sign = "+"
                for char in change:
                    if char in "+-":
                        sign = char
                    elif sign == "+":
                        await grant_flag(
                            session, actor_flags, user, char, channel, trigger.hostmask
                        )
                    else:
                        await revoke_flag(
                            session, actor_flags, user, char, channel, trigger.hostmask
                        )
                await session.flush()
            global_flags = await get_user_flags(session, user)
            channel_flags = set()
            if channel:
                rows = await session.execute(
                    select(UserFlag.flag).where(
                        UserFlag.user_id == user.id, func.lower(UserFlag.channel) == channel.lower()
                    )
                )
                channel_flags = set(rows.scalars().all())
            name, mask = user.nick, user.hostmask or "account only"
    except PermissionError as exc:
        await bot.reply(trigger, str(exc))  # type: ignore[attr-defined]
        return

    def fmt(flags: set[str]) -> str:
        return "+" + "".join(sorted(flags)) if flags else "none"

    if channel:
        text = (
            f"{name} [{mask}] flags on {channel}: {fmt(channel_flags)} (global {fmt(global_flags)})"
        )
    else:
        text = f"{name} [{mask}] flags (global): {fmt(global_flags)}"
    await bot.reply(trigger, text)  # type: ignore[attr-defined]


async def _find_user(session: object, target: str) -> tuple[User | None, str]:
    """A user record by exact mask (contains '!') or by nick; else (None, reason)."""
    from sqlalchemy import func, select

    from pybot.core.database import User
    from pybot.core.permissions import find_user_by_mask

    if "!" in target:
        user = await find_user_by_mask(session, target)  # type: ignore[arg-type]
        return user, "" if user else f"No user registered with mask '{target}'."
    result = await session.execute(  # type: ignore[attr-defined]
        select(User).where(func.lower(User.nick) == target.lower())
    )
    users = result.scalars().all()
    if len(users) == 1:
        return users[0], ""
    if not users:
        return None, f"No user named '{target}'. Add one with !adduser <nick!user@host> <flags>."
    masks = ", ".join(u.hostmask or "(account only)" for u in users)
    return None, f"Several users are named '{target}' ({masks}); use the mask instead."


@plugin.command(
    "setpass",
    privilege="n",
    help="Set a user's Web UI/partyline password",
    usage="!setpass <nick> <password>",
)
async def cmd_setpass(bot: object, trigger: Trigger) -> None:
    if not await _require_private(bot, trigger, "!setpass <nick> <password>"):
        return
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !setpass <nick> <password>")  # type: ignore[attr-defined]
        return

    nick = trigger.args[0]
    password = " ".join(trigger.args[1:]).strip()
    if len(password) < 8:
        await bot.reply(trigger, "Password must be at least 8 characters.")  # type: ignore[attr-defined]
        return

    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.web.auth import hash_password

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == nick))
        users = result.scalars().all()
        if not users:
            await bot.reply(trigger, f"User '{nick}' not found. Add them first with !adduser.")  # type: ignore[attr-defined]
            return
        if len(users) > 1:
            await bot.reply(trigger, f"Several users are named '{nick}'; set it in the Web UI.")  # type: ignore[attr-defined]
            return

        users[0].password_hash = hash_password(password)
        await _audit(trigger, "user.password", nick, "set by owner", session)

    await bot.reply(trigger, f"Password updated for {nick}.")  # type: ignore[attr-defined]


@plugin.command(
    "passwd",
    privilege="a",
    help="Change your own Web UI/partyline password",
    usage="!passwd <newpassword>",
)
async def cmd_passwd(bot: object, trigger: Trigger) -> None:
    if not await _require_private(bot, trigger, "!passwd <newpassword>"):
        return
    if not trigger.args:
        await bot.reply(trigger, "Usage: !passwd <newpassword>")  # type: ignore[attr-defined]
        return

    password = " ".join(trigger.args).strip()
    if len(password) < 8:
        await bot.reply(trigger, "Password must be at least 8 characters.")  # type: ignore[attr-defined]
        return

    from pybot.core.database import get_session
    from pybot.core.permissions import find_users_for_identity
    from pybot.web.auth import hash_password

    async with get_session() as session:
        # The record that grants this identity its rights, not just any record with this nick
        user = _pick_own_record(
            await find_users_for_identity(session, trigger.hostmask, trigger.account),
            trigger.nick,
        )
        if not user:
            await bot.reply(  # type: ignore[attr-defined]
                trigger,
                "No single user record matches you. Ask an owner to run !setpass.",
            )
            return

        user.password_hash = hash_password(password)
        await _audit(trigger, "user.password", user.nick, "changed own", session)

    await bot.reply(trigger, "Your password has been updated.")  # type: ignore[attr-defined]


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
    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import User, get_session

    async with get_session() as session:
        result = await session.execute(
            select(User)
            .options(selectinload(User.flags), selectinload(User.hosts))
            .where(func.lower(User.nick) == nick.lower())
        )
        users = result.scalars().all()

    if not users:
        await bot.reply(trigger, f"No record for '{nick}'.")  # type: ignore[attr-defined]
        return
    for user in users:
        global_flags = set(user.global_flags or "") | {f.flag for f in user.flags if not f.channel}
        by_channel: dict[str, str] = {}
        for f in user.flags:
            if f.channel:
                by_channel[f.channel] = by_channel.get(f.channel, "") + f.flag
        lines = [
            f"{user.nick}: hosts {', '.join(user.all_masks) or 'none'}"
            + (f" | account {user.account}" if user.account else ""),
            "Flags: global "
            + ("+" + "".join(sorted(global_flags)) if global_flags else "none")
            + "".join(f" | {ch} +{''.join(sorted(fl))}" for ch, fl in sorted(by_channel.items())),
        ]
        if user.last_seen:
            lines.append(
                f"Last seen {user.last_seen:%Y-%m-%d %H:%M} UTC in {user.last_seen_where or '?'}"
            )
        for line in lines:
            await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]


@plugin.command("servers", privilege="a", help="List configured IRC servers")
async def cmd_servers(bot: object, trigger: Trigger) -> None:
    servers = bot.config.servers  # type: ignore[attr-defined]
    for s in servers:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick,
            f"{s.host}:{s.port} SSL={s.ssl} priority={s.priority}",
        )


@plugin.command(
    "jobs",
    privilege="a",
    help="List/pause/resume scheduler jobs",
    usage="!jobs <list|pause|resume> [plugin.func]",
)
async def cmd_jobs(bot: object, trigger: Trigger) -> None:
    scheduler = getattr(bot, "scheduler", None)
    if scheduler is None:
        await bot.reply(trigger, "Scheduler not available.")  # type: ignore[attr-defined]
        return

    if not trigger.args:
        await bot.reply(trigger, "Usage: !jobs <list|pause|resume> [plugin.func]")  # type: ignore[attr-defined]
        return

    action = trigger.args[0].lower()
    if action == "list":
        jobs = scheduler.list_jobs()  # type: ignore[attr-defined]
        if not jobs:
            await bot.reply(trigger, "No scheduler jobs registered.")  # type: ignore[attr-defined]
            return
        items = []
        for job in sorted(jobs, key=lambda j: str(j["name"])):
            state = " (paused)" if job["paused"] else ""
            next_run = str(job["next_run"] or "-")[:16].replace("T", " ")
            items.append(f"{job['name']}{state} {job['schedule']}, next {next_run}")
        # One line; long lists are split automatically
        await bot.notice(trigger.nick, f"Jobs ({len(items)}, UTC): {'; '.join(items)}")  # type: ignore[attr-defined]
        return

    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !jobs <pause|resume> <plugin.func>")  # type: ignore[attr-defined]
        return

    job_name = trigger.args[1]
    if action == "pause":
        ok = scheduler.pause_job(job_name)  # type: ignore[attr-defined]
        if ok:
            await bot.reply(trigger, f"Paused job {job_name}.")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"Job not found: {job_name}")  # type: ignore[attr-defined]
        return

    if action == "resume":
        ok = scheduler.resume_job(job_name)  # type: ignore[attr-defined]
        if ok:
            await bot.reply(trigger, f"Resumed job {job_name}.")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"Job not found: {job_name}")  # type: ignore[attr-defined]
        return

    await bot.reply(trigger, "Usage: !jobs <list|pause|resume> [plugin.func]")  # type: ignore[attr-defined]


@plugin.command(
    "memo",
    privilege="a",
    help="Send a memo via MemoServ",
    usage="!memo <nick> <message>",
)
async def cmd_memo(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !memo <nick> <message>")  # type: ignore[attr-defined]
        return

    nick = trigger.args[0]
    message = " ".join(trigger.args[1:]).strip()
    if not message:
        await bot.reply(trigger, "Usage: !memo <nick> <message>")  # type: ignore[attr-defined]
        return

    result = await bot.services.memoserv_send_checked(nick, message)  # type: ignore[attr-defined]
    if result.ok:
        await bot.reply(trigger, f"MemoServ: {result.message}")  # type: ignore[attr-defined]
    else:
        await bot.reply(trigger, f"MemoServ error: {result.message}")  # type: ignore[attr-defined]


@plugin.command(
    "akick",
    privilege="a",
    help="Manage ChanServ AKICK list",
    usage="!akick <add|del|list> <#channel> [mask] [reason]",
)
async def cmd_akick(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !akick <add|del|list> <#channel> [mask] [reason]")  # type: ignore[attr-defined]
        return

    action = trigger.args[0].lower()
    channel = trigger.args[1]

    if action == "list":
        result = await bot.services.chanserv_akick_list_checked(channel)  # type: ignore[attr-defined]
        if result.ok:
            await bot.reply(trigger, f"ChanServ: {result.message}")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"ChanServ error: {result.message}")  # type: ignore[attr-defined]
        return

    if action == "add":
        if len(trigger.args) < 3:
            await bot.reply(trigger, "Usage: !akick add <#channel> <mask> [reason]")  # type: ignore[attr-defined]
            return
        mask = trigger.args[2]
        reason = " ".join(trigger.args[3:]).strip()
        result = await bot.services.chanserv_akick_add_checked(channel, mask, reason)  # type: ignore[attr-defined]
        if result.ok:
            await bot.reply(trigger, f"ChanServ: {result.message}")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"ChanServ error: {result.message}")  # type: ignore[attr-defined]
        return

    if action == "del":
        if len(trigger.args) < 3:
            await bot.reply(trigger, "Usage: !akick del <#channel> <mask>")  # type: ignore[attr-defined]
            return
        mask = trigger.args[2]
        result = await bot.services.chanserv_akick_del_checked(channel, mask)  # type: ignore[attr-defined]
        if result.ok:
            await bot.reply(trigger, f"ChanServ: {result.message}")  # type: ignore[attr-defined]
        else:
            await bot.reply(trigger, f"ChanServ error: {result.message}")  # type: ignore[attr-defined]
        return

    await bot.reply(trigger, "Usage: !akick <add|del|list> <#channel> [mask] [reason]")  # type: ignore[attr-defined]


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


@plugin.command(
    "useserviceauth",
    privilege="n",
    help="Bind your IRC services account as the authoritative owner account",
    usage="!useserviceauth",
)
async def cmd_useserviceauth(bot: object, trigger: Trigger) -> None:
    """Persist trigger.account as the bot owner's IRC services account.

    The owner is then recognised by services account. A nick-only hostmask on the
    owner record ("nick!*@*", which anyone taking the nick would match) is removed.
    """
    account = trigger.account
    if not account:
        await bot.reply(  # type: ignore[attr-defined]
            trigger,
            "You are not logged in to an IRC services account. " "Identify with NickServ first.",
        )
        return

    from pybot.core.database import get_session
    from pybot.core.permissions import find_users_for_identity, is_nick_only_mask

    async with get_session() as session:
        user = _pick_own_record(
            await find_users_for_identity(session, trigger.hostmask, trigger.account),
            trigger.nick,
        )
        if user is None:
            await bot.reply(trigger, "No single user record matches you.")  # type: ignore[attr-defined]
            return
        user.account = account
        cleared = bool(user.hostmask) and is_nick_only_mask(user.hostmask)
        if cleared:
            user.hostmask = ""
        await _audit(trigger, "user.account", user.nick, account, session)

    note = " The nick-only hostmask was removed." if cleared else ""
    await bot.reply(  # type: ignore[attr-defined]
        trigger,
        f"Bound user '{user.nick}' to services account '{account}'.{note} "
        "Set core.owner_account in config to keep this across rebuilds.",
    )


@plugin.command(
    "host",
    privilege="a",
    help="List, add or remove a user's hostmasks (Eggdrop +host/-host)",
    usage="!host <list|add|del> <nick|mask> [nick!user@host]",
)
async def cmd_host(bot: object, trigger: Trigger) -> None:
    usage = "Usage: !host <list|add|del> <nick|mask> [nick!user@host]"
    args = trigger.args
    action = args[0].lower() if args else ""
    if action not in ("list", "add", "del") or len(args) < (2 if action == "list" else 3):
        await bot.reply(trigger, usage)  # type: ignore[attr-defined]
        return
    mask = args[2] if len(args) > 2 else ""

    from pybot.core.database import get_session
    from pybot.core.permissions import add_host, remove_host

    message = ""
    try:
        async with get_session() as session:
            user, problem = await _find_user(session, args[1])
            if user is None:
                await bot.reply(trigger, problem)  # type: ignore[attr-defined]
                return
            if action == "add":
                added = await add_host(session, _actor_flags(trigger), user, mask, trigger.hostmask)
                message = f"Added {mask}. " if added else f"{user.nick} already has {mask}. "
            elif action == "del":
                removed = await remove_host(
                    session, _actor_flags(trigger), user, mask, trigger.hostmask
                )
                message = f"Removed {mask}. " if removed else f"{user.nick} has no host {mask}. "
            await session.flush()
            await session.refresh(user, ["hosts"])
            name, masks, account = user.nick, user.all_masks, user.account
    except (PermissionError, ValueError) as exc:
        await bot.reply(trigger, str(exc))  # type: ignore[attr-defined]
        return
    hosts = ", ".join(masks) or "none"
    suffix = f" | account {account}" if account else ""
    await bot.reply(trigger, f"{message}{name} hosts: {hosts}{suffix}")  # type: ignore[attr-defined]


@plugin.command(
    "audit",
    privilege="a",
    help="Show recent admin actions (newest first)",
    usage="!audit [count] [actor or action.]",
)
async def cmd_audit(bot: object, trigger: Trigger) -> None:
    from pybot.core.audit import format_entry, recent

    count, actor, action = 10, "", ""
    for arg in trigger.args:
        if arg.isdigit():
            count = max(1, min(50, int(arg)))
        elif "." in arg and "!" not in arg:
            action = arg  # e.g. "flag." or "ban.add"
        else:
            actor = arg
    entries = await recent(count, actor=actor, action=action)
    if not entries:
        await bot.notice(trigger.nick, "No matching admin actions recorded.")  # type: ignore[attr-defined]
        return
    await bot.notice(trigger.nick, f"Last {len(entries)} admin actions (UTC, newest first):")  # type: ignore[attr-defined]
    for entry in entries:
        await bot.notice(trigger.nick, format_entry(entry))  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _audit(
    trigger: Trigger, action: str, target: str = "", details: str = "", session: object = None
) -> None:
    from pybot.core.audit import audit

    await audit(trigger.hostmask, action, target, details, "irc", session)  # type: ignore[arg-type]


async def _actor_flags_for(trigger: Trigger, channel: str | None) -> set[str]:
    """Privilege flags for permission checks, including m if a master for this channel."""
    flags = _actor_flags(trigger)
    if not flags and channel and await trigger.has_flag("m", channel):
        flags.add("m")
    return flags


def _parse_duration(s: str) -> datetime:
    from datetime import timezone

    return datetime.now(tz=timezone.utc) + plugin.parse_duration(s)


def _generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


_PLUGIN_NAME = re.compile(r"[a-z_][a-z0-9_]*")


def _actor_flags(trigger: Trigger) -> set[str]:
    """The privilege flags the command's caller holds, for permission checks."""
    if trigger.owner:
        return {"n"}
    return {"a"} if trigger.admin else set()


def _pick_own_record(users: list[User], nick: str) -> User | None:
    """Among records matching the caller, the one named like them, else the only one."""
    named = [u for u in users if u.nick.lower() == nick.lower()]
    if len(named) == 1:
        return named[0]
    return users[0] if len(users) == 1 else None


async def _require_private(bot: object, trigger: Trigger, usage: str) -> bool:
    """Password commands only work in a private message to the bot."""
    if trigger.is_pm:
        return True
    await bot.notice(  # type: ignore[attr-defined]
        trigger.nick,
        f"Use this in a private message: /msg {bot.nick} {usage}. "  # type: ignore[attr-defined]
        "Your message was visible in the channel, so choose a different password.",
    )
    return False


def _web_login_url(bot: object) -> str:
    web = bot.config.web  # type: ignore[attr-defined]
    host = str(getattr(web, "host", "localhost") or "localhost")
    port = int(getattr(web, "port", 8080) or 8080)
    if host in {"0.0.0.0", "::", ""}:  # noqa: S104
        host = "localhost"
    return f"http://{host}:{port}/login"


async def _send_new_user_credentials(
    bot: object,
    nick: str,
    mask: str,
    password: str,
    trigger: Trigger,
) -> None:
    """DM the login to the new user only if whoever holds the nick matches their mask.

    Otherwise (nick offline, or held by someone else) the admin gets the password
    privately to pass on.
    """
    from pybot.core.permissions import match_hostmask

    whois = await bot.whois(nick)  # type: ignore[attr-defined]
    current = f"{nick}!{whois.get('user', '')}@{whois.get('host', '')}"
    verified = bool(whois.get("user")) and match_hostmask(mask, current)
    recipient, say = (nick, bot.say) if verified else (trigger.nick, bot.notice)  # type: ignore[attr-defined]

    url = _web_login_url(bot)
    lines = [
        "Your Pyra admin login is ready." if verified else f"Pyra admin login for {nick}:",
        f"URL: {url}",
        f"Username: {nick}",
        f"Password: {password}",
        "Change it after first login with !passwd <newpassword> (in a private message).",
    ]
    try:
        for line in lines:
            await say(recipient, line)
    except Exception as exc:
        await bot.reply(trigger, f"User added, but sending the credentials failed: {exc}")  # type: ignore[attr-defined]
        return
    if verified:
        await bot.reply(trigger, f"Login credentials sent privately to {nick}.")  # type: ignore[attr-defined]
    else:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick,
            f"{nick} is offline or does not match {mask}, so the login was sent to you. "
            "Pass it on privately.",
        )
