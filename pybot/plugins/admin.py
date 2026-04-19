"""
Admin plugin — bot administration commands (owner/admin only).

Author:  Jarsky
Version: 1.2.0
Date:    2026-04-20

All commands require 'a' (admin) or 'n' (owner) flag.
Most are designed to be used via /msg or from the partyline.

Commands:
  !join <#channel> [key]          Join a channel
  !part [#channel] [reason]       Part a channel
  !say <target> <message>         Make the bot say something
  !raw <line>                     Send raw IRC line (owner only)
  !reload [plugin]                Reload a plugin or all plugins
  !quit [message]                 Disconnect and exit (owner only)
  !adduser <nick> <flags>         Add a user with flags
  !deluser <nick>                 Remove a user
  !flags <nick> [+/-flags]        Show or modify user flags
  !setpass <nick> <password>      Set Web UI/partyline password for a user (owner)
  !passwd <newpassword>           Change your own Web UI/partyline password
  !useserviceauth                 Bind your IRC services account as owner account (owner only)
    !jobs list                      Show scheduler jobs
    !jobs pause <plugin.func>       Pause a scheduler job
    !jobs resume <plugin.func>      Resume a scheduler job
"""

from __future__ import annotations

import secrets
import string

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.2.0",
    "updated": "2026-04-20",
    "description": "Bot admin commands (join, part, say, reload, quit). Owner/admin only.",
    "url": "https://github.com/Jarsky/pyra",
}

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


@plugin.command("load", privilege="n", help="Load a plugin", usage="!load <plugin_name_or_path>")
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


@plugin.command("unload", privilege="n", help="Unload a plugin", usage="!unload <plugin>")
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

    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import add_flag
    from pybot.web.auth import hash_password

    nick = mask.split("!")[0] if "!" in mask else mask

    async with get_session() as session:
        for flag in flags_str:
            try:
                await add_flag(session, trigger.hostmask, mask, flag)
            except PermissionError as e:
                await bot.reply(trigger, str(e))  # type: ignore[attr-defined]
                return

        result = await session.execute(select(User).where(User.hostmask == mask))
        user = result.scalars().first()
        if user is None:
            result = await session.execute(select(User).where(User.nick == nick))
            user = result.scalars().first()

        if user is None:
            await bot.reply(  # type: ignore[attr-defined]
                trigger,
                f"Added user {nick}, but no user record was found for password setup.",
            )
            await bot.reply(  # type: ignore[attr-defined]
                trigger,
                f"Run !setpass {nick} <password> to set credentials manually.",
            )
            return

        generated_password = _generate_password()
        user.password_hash = hash_password(generated_password)

    await bot.reply(trigger, f"Added user {nick} with flags: {flags_str}")  # type: ignore[attr-defined]
    await _send_new_user_credentials(bot, nick, generated_password, trigger)


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
    "setpass",
    privilege="n",
    help="Set a user's Web UI/partyline password",
    usage="!setpass <nick> <password>",
)
async def cmd_setpass(bot: object, trigger: Trigger) -> None:
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
        user = result.scalar_one_or_none()
        if not user:
            await bot.reply(trigger, f"User '{nick}' not found. Add them first with !adduser.")  # type: ignore[attr-defined]
            return

        user.password_hash = hash_password(password)

    await bot.reply(trigger, f"Password updated for {nick}.")  # type: ignore[attr-defined]


@plugin.command(
    "passwd",
    privilege="a",
    help="Change your own Web UI/partyline password",
    usage="!passwd <newpassword>",
)
async def cmd_passwd(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !passwd <newpassword>")  # type: ignore[attr-defined]
        return

    password = " ".join(trigger.args).strip()
    if len(password) < 8:
        await bot.reply(trigger, "Password must be at least 8 characters.")  # type: ignore[attr-defined]
        return

    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.web.auth import hash_password

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == trigger.nick))
        user = result.scalar_one_or_none()
        if not user:
            await bot.reply(  # type: ignore[attr-defined]
                trigger,
                "No user record found for your nick. Ask owner to run !setpass.",
            )
            return

        user.password_hash = hash_password(password)

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
        await bot.notice(trigger.nick, f"Scheduler jobs ({len(jobs)}):")  # type: ignore[attr-defined]
        for job in sorted(jobs, key=lambda j: str(j["name"])):
            paused = "paused" if bool(job["paused"]) else "running"
            next_run = str(job["next_run"] or "-")
            await bot.notice(  # type: ignore[attr-defined]
                trigger.nick,
                f"{job['name']} [{paused}] {job['schedule']} next={next_run}",
            )
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

    After running this command the bot will recognise the owner by their
    services account rather than just their current nick, preventing
    privilege escalation via nick spoofing.
    """
    account = trigger.account
    if not account:
        await bot.reply(  # type: ignore[attr-defined]
            trigger,
            "You are not logged in to an IRC services account. " "Identify with NickServ first.",
        )
        return

    from sqlalchemy import select

    from pybot.core.database import User, get_session

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == trigger.nick))
        user = result.scalar_one_or_none()
        if user is None:
            await bot.reply(trigger, f"No user record found for '{trigger.nick}'.")  # type: ignore[attr-defined]
            return
        user.account = account

    await bot.reply(  # type: ignore[attr-defined]
        trigger,
        f"Owner account bound to services account '{account}'. "
        "The bot will now verify your identity via account name.",
    )


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


def _generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
    password: str,
    trigger: Trigger,
) -> None:
    url = _web_login_url(bot)
    try:
        await bot.say(  # type: ignore[attr-defined]
            nick,
            "Your Pyra admin login is ready.",
        )
        await bot.say(nick, f"URL: {url}")  # type: ignore[attr-defined]
        await bot.say(nick, f"Username: {nick}")  # type: ignore[attr-defined]
        await bot.say(nick, f"Password: {password}")  # type: ignore[attr-defined]
        await bot.say(  # type: ignore[attr-defined]
            nick,
            "Please run !passwd <newpassword> after first login.",
        )
        await bot.reply(  # type: ignore[attr-defined]
            trigger,
            f"Login credentials generated and sent privately to {nick}.",
        )
    except Exception as exc:
        await bot.reply(  # type: ignore[attr-defined]
            trigger,
            f"User added, but failed to DM credentials to {nick}: {exc}",
        )
