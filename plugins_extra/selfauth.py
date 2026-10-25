"""
SelfAuthorize plugin — DB-backed self-op/hop/owner list per channel.

Author:  Jarsky
Version: 3.0.0
Date:    2026-10-26
Note:    Python rewrite of SelfAuthorize.tcl; now DB-backed with per-channel scope


Authorized managers add people to a role list; they can then grant themselves the
corresponding IRC channel mode without needing a bot flag.

People are identified by services account (or, without one, a *!user@host mask), never
by nick alone: anyone can take a nick. When you add a nick, the bot looks up who is
using it now and stores their account or mask. Entries from older versions that only
hold a nick no longer work; re-add those people.

Commands (channel only):
  !self +op <nick|mask|account:name>     Add to ops list (also +owner, +hop)
  !self -op <nick>                       Remove from ops list (also -owner, -hop)
  !self list                             Show current list
  !ops                  Grant yourself +o (if on ops list)
  !owners               Grant yourself +q (if on owners list)
  !hops                 Grant yourself +h (if on half-ops list)

Plugin vars (config.yaml plugins.vars.selfauth):
  authorized:            # who may manage the lists (bot admins/owners always can)
    - Jarsky             # a services account name
    - "*!wayne@home.example"   # or a hostmask (not nick!*@*)
  channel: "#mychan"    # optional — restrict to one channel
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "3.0.0",
    "updated": "2026-10-26",
    "description": "DB-backed self-op/hop/owner lists per channel. Users self-grant IRC modes.",
    "url": "https://github.com/Jarsky/pyra",
}

__plugin_config__ = {
    "channel": {"type": "str", "help": "Channel the lists apply to (blank = any)"},
    "authorized": {
        "type": "list",
        "help": "Managers: services account names or hostmasks (bot admins always can)",
    },
}

from typing import Any

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pybot import plugin
from pybot.plugin import Trigger

_USAGE = "Usage: !self +op|-op|+owner|-owner|+hop|-hop <nick|mask|account:name>  or  !self list"

# ── Model ────────────────────────────────────────────────────────────────────

_SelfAuthModel: Any = None


def _build_model() -> Any:
    from pybot.core.database import Base

    # A plugin reload runs this module again: reuse the class mapped the first time
    models: dict[str, Any] = Base.metadata.info.setdefault("plugin_models", {})
    if "selfauth" in models:
        return models["selfauth"]

    class SelfAuthEntry(Base):  # type: ignore[misc,valid-type]
        __tablename__ = "selfauth"
        __table_args__ = (UniqueConstraint("nick", "channel", name="uq_selfauth_nick_chan"),)

        id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
        nick: Mapped[str] = mapped_column(String(64), index=True)  # label for display/removal
        channel: Mapped[str] = mapped_column(String(128))
        role: Mapped[str] = mapped_column(String(16))  # op | hop | owner
        added_by: Mapped[str] = mapped_column(String(64))
        account: Mapped[str | None] = mapped_column(String(64), nullable=True)
        mask: Mapped[str | None] = mapped_column(String(256), nullable=True)

    models["selfauth"] = SelfAuthEntry
    return SelfAuthEntry


# ── Plugin lifecycle ─────────────────────────────────────────────────────────


async def setup(bot: object) -> None:
    global _SelfAuthModel
    _SelfAuthModel = _build_model()
    from pybot.core.database import ensure_plugin_tables

    await ensure_plugin_tables(_SelfAuthModel)
    await _add_identity_columns()


async def _add_identity_columns() -> None:
    """Upgrade a table created by v2 (nick only) with the account/mask columns."""
    from sqlalchemy import inspect, text

    from pybot.core.database import _engine

    if _engine is None:
        return
    async with _engine.begin() as conn:
        existing = await conn.run_sync(
            lambda sync: {c["name"] for c in inspect(sync).get_columns("selfauth")}
        )
        for column, sql_type in (("account", "VARCHAR(64)"), ("mask", "VARCHAR(256)")):
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE selfauth ADD COLUMN {column} {sql_type}"))


# ── Identity helpers ─────────────────────────────────────────────────────────


async def _account_of(bot: Any, nick: str, known: str | None) -> str | None:
    if known:
        return known
    whois = await bot.whois(nick)
    return whois.get("account") or None


def _matches(entry: Any, hostmask: str, account: str | None) -> bool:
    from pybot.core.permissions import match_hostmask

    if entry.account:
        return bool(account) and entry.account.lower() == account.lower()  # type: ignore[union-attr]
    return bool(entry.mask) and match_hostmask(entry.mask, hostmask)


async def _is_manager(bot: Any, trigger: Trigger) -> bool:
    """Bot admins/owners, or an `authorized` entry matching the caller's account or mask."""
    from pybot.core.permissions import is_nick_only_mask, match_hostmask

    if trigger.admin:
        return True
    cfg: dict[str, Any] = bot.plugin_config("selfauth")
    managers = cfg.get("authorized", [])
    if not isinstance(managers, list):
        return False
    account = None
    for entry in (str(m).strip() for m in managers):
        if "!" in entry or "@" in entry:
            if not is_nick_only_mask(entry) and match_hostmask(entry, trigger.hostmask):
                return True
        elif entry:
            account = account or await _account_of(bot, trigger.nick, trigger.account)
            if account and account.lower() == entry.removeprefix("account:").lower():
                return True
    return False


async def _identify(bot: Any, channel: str, target: str) -> tuple[str, str | None, str | None]:
    """(label, account, mask) for a list entry; raises ValueError if nobody can be identified."""
    from pybot.core.permissions import is_nick_only_mask

    if target.lower().startswith("account:"):
        account = target.split(":", 1)[1]
        return account, account, None
    if "!" in target or "@" in target:
        if is_nick_only_mask(target):
            raise ValueError(f"'{target}' would match anyone using that nick; use *!user@host")
        return target.split("!", 1)[0] if "!" in target else target, None, target

    member = bot.get_nick_in_channel(channel, target)
    whois: dict[str, str] = {}
    account = member.account if member else None
    if not account:
        whois = await bot.whois(target)
        account = whois.get("account") or None
    if account:
        return target, account, None
    user = (member.user if member else "") or whois.get("user", "")
    host = (member.host if member else "") or whois.get("host", "")
    if user and host:
        return target, None, f"*!{user}@{host}"
    raise ValueError(
        f"Can't identify {target}: they need to be online. "
        "Or give a mask (*!user@host) or account:name."
    )


def _allowed_channel(bot: Any, channel: str) -> bool:
    cfg: dict[str, Any] = bot.plugin_config("selfauth")
    restrict = str(cfg.get("channel", "") or "")
    return not restrict or channel.lower() == restrict.lower()


# ── Commands ─────────────────────────────────────────────────────────────────


@plugin.command(
    "self",
    help="Manage self-op/hop/owner list (authorized users only)",
    usage="!self +op|-op|+owner|-owner|+hop|-hop <nick|mask|account:name>  or  !self list",
)
async def cmd_self(bot: Any, trigger: Trigger) -> None:
    from sqlalchemy import func, select

    from pybot.core.database import get_session

    if not trigger.channel:
        await bot.reply(trigger, "This command only works in a channel.")
        return
    if not _allowed_channel(bot, trigger.channel):
        await bot.reply(trigger, "SelfAuth is not enabled for this channel.")
        return
    if not await _is_manager(bot, trigger):
        await bot.reply(trigger, "You are not authorized to manage this list.")
        return
    if not trigger.args:
        await bot.reply(trigger, _USAGE)
        return

    model = _SelfAuthModel
    channel = trigger.channel.lower()
    subcmd = trigger.args[0].lower()

    if subcmd == "list":
        async with get_session() as session:
            rows = (
                (await session.execute(select(model).where(func.lower(model.channel) == channel)))
                .scalars()
                .all()
            )
        if not rows:
            await bot.reply(trigger, "No entries.")
            return
        for r in rows:
            who = f"account {r.account}" if r.account else (r.mask or "no identity: re-add")
            await bot.notice(trigger.nick, f"  {r.nick} [{who}] — {r.role} (added by {r.added_by})")
        return

    action_map = {
        "+op": ("op", True),
        "-op": ("op", False),
        "+owner": ("owner", True),
        "-owner": ("owner", False),
        "+hop": ("hop", True),
        "-hop": ("hop", False),
    }
    if subcmd not in action_map or len(trigger.args) < 2:
        await bot.reply(trigger, _USAGE)
        return
    role, adding = action_map[subcmd]
    target = trigger.args[1]

    if adding:
        try:
            label, account, mask = await _identify(bot, trigger.channel, target)
        except ValueError as exc:
            await bot.reply(trigger, str(exc))
            return
    else:
        label = target.split("!", 1)[0] if "!" in target else target.removeprefix("account:")

    async with get_session() as session:
        row = (
            await session.execute(
                select(model).where(
                    func.lower(model.nick) == label.lower(), func.lower(model.channel) == channel
                )
            )
        ).scalar_one_or_none()

        if adding:
            if row is None:
                row = model(nick=label, channel=channel)
                session.add(row)
            row.role, row.added_by, row.account, row.mask = role, trigger.nick, account, mask
            who = f"account {account}" if account else mask
            await bot.reply(trigger, f"{label} [{who}] added to {role}s list.")
        elif row is not None:
            await session.delete(row)
            await bot.reply(trigger, f"{label} removed from {row.role}s list.")
        else:
            await bot.reply(trigger, f"{label} is not on the {role}s list.")


@plugin.command("ops", help="Grant yourself +o if on the ops list")
async def cmd_ops(bot: Any, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "op", "+o")


@plugin.command("hops", help="Grant yourself +h if on the half-ops list")
async def cmd_hops(bot: Any, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "hop", "+h")


@plugin.command("owners", help="Grant yourself +q if on the owners list")
async def cmd_owners(bot: Any, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "owner", "+q")


async def _self_grant(bot: Any, trigger: Trigger, role: str, mode: str) -> None:
    from sqlalchemy import func, select

    from pybot.core.database import get_session

    if not trigger.channel:
        await bot.notice(trigger.nick, "This command only works in a channel.")
        return
    if not _allowed_channel(bot, trigger.channel):
        return

    model = _SelfAuthModel
    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(model).where(
                        func.lower(model.channel) == trigger.channel.lower(), model.role == role
                    )
                )
            )
            .scalars()
            .all()
        )

    account = trigger.account
    if any(r.account for r in rows):
        account = await _account_of(bot, trigger.nick, account)
    if not any(_matches(r, trigger.hostmask, account) for r in rows):
        legacy = any(
            not r.account and not r.mask and r.nick.lower() == trigger.nick.lower() for r in rows
        )
        hint = (
            " Your entry is from before identity checks; ask a manager to re-add you."
            if legacy
            else ""
        )
        await bot.notice(
            trigger.nick, f"You are not on the {role}s list for {trigger.channel}.{hint}"
        )
        return
    if not bot.can_set_modes(trigger.channel):
        await bot.notice(trigger.nick, f"I need ops in {trigger.channel} to do that.")
        return

    await bot.mode(trigger.channel, mode, trigger.nick)
    await bot.notice(trigger.nick, f"Granted {mode} in {trigger.channel}.")
