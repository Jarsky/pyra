"""
SelfAuthorize plugin — DB-backed self-op/hop/owner list per channel.

Author:  Jarsky
Version: 2.0.0
Date:    2026-04-18
Note:    Python rewrite of SelfAuthorize.tcl; now DB-backed with per-channel scope


Authorized managers add nicks to a role list; those nicks can then grant
themselves the corresponding IRC channel mode without needing a bot flag.

Commands (channel only):
  !self +op <nick>      Add nick to ops list
  !self -op <nick>      Remove nick from ops list
  !self +owner <nick>   Add nick to owners list
  !self -owner <nick>   Remove nick from owners list
  !self +hop <nick>     Add nick to half-ops list
  !self -hop <nick>     Remove nick from half-ops list
  !self list            Show current list
  !ops                  Grant yourself +o (if on ops list)
  !owners               Grant yourself +q (if on owners list)
  !hops                 Grant yourself +h (if on half-ops list)

Plugin vars (config.yaml plugins.vars.selfauth):
  authorized:
    - Jarsky
    - waynenewman_
  channel: "#mychan"    # optional — restrict to one channel
"""

from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pybot import plugin
from pybot.plugin import Trigger

# ── Model ────────────────────────────────────────────────────────────────────


def _get_base() -> type:
    from pybot.core.database import Base
    return Base


class _SelfAuthEntry:
    """Placeholder — replaced by setup() after Base is available."""


_SelfAuthModel: type | None = None


def _build_model() -> type:
    from pybot.core.database import Base

    class SelfAuthEntry(Base):  # type: ignore[misc,valid-type]
        __tablename__ = "selfauth"
        __table_args__ = (UniqueConstraint("nick", "channel", name="uq_selfauth_nick_chan"),)

        id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
        nick: Mapped[str] = mapped_column(String(64), index=True)
        channel: Mapped[str] = mapped_column(String(128))
        role: Mapped[str] = mapped_column(String(16))  # op | hop | owner
        added_by: Mapped[str] = mapped_column(String(64))

    return SelfAuthEntry


# ── Plugin lifecycle ─────────────────────────────────────────────────────────


async def setup(bot: object) -> None:
    global _SelfAuthModel
    _SelfAuthModel = _build_model()
    from pybot.core.database import ensure_plugin_tables
    await ensure_plugin_tables(_SelfAuthModel)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _is_authorized(bot: object, nick: str) -> bool:
    cfg: dict[str, object] = bot.plugin_config("selfauth")  # type: ignore[attr-defined]
    managers = cfg.get("authorized", [])
    if isinstance(managers, list):
        return nick in managers
    return False


def _allowed_channel(bot: object, channel: str) -> bool:
    cfg: dict[str, object] = bot.plugin_config("selfauth")  # type: ignore[attr-defined]
    restrict = cfg.get("channel", "")
    return not restrict or channel == restrict


# ── Commands ─────────────────────────────────────────────────────────────────


@plugin.command(
    "self",
    help="Manage self-op/hop/owner list (authorized users only)",
    usage="!self +op|-op|+owner|-owner|+hop|-hop <nick>  or  !self list",
)
async def cmd_self(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import get_session

    if not trigger.channel:
        await bot.reply(trigger, "This command only works in a channel.")  # type: ignore[attr-defined]
        return

    if not _is_authorized(bot, trigger.nick):
        await bot.reply(trigger, "You are not authorized to manage this list.")  # type: ignore[attr-defined]
        return

    if not _allowed_channel(bot, trigger.channel):
        await bot.reply(trigger, "SelfAuth is not enabled for this channel.")  # type: ignore[attr-defined]
        return

    if not trigger.args:
        await bot.reply(  # type: ignore[attr-defined]
            trigger, "Usage: !self +op|-op|+owner|-owner|+hop|-hop <nick>"
        )
        return

    subcmd = trigger.args[0].lower()

    if subcmd == "list":
        assert _SelfAuthModel is not None
        async with get_session() as session:
            rows = (await session.execute(
                select(_SelfAuthModel).where(  # type: ignore[arg-type]
                    _SelfAuthModel.channel == trigger.channel  # type: ignore[attr-defined]
                )
            )).scalars().all()
        if not rows:
            await bot.reply(trigger, "No entries.")  # type: ignore[attr-defined]
            return
        for r in rows:
            await bot.notice(  # type: ignore[attr-defined]
                trigger.nick, f"  {r.nick} — {r.role} (added by {r.added_by})"
            )
        return

    action_map = {
        "+op": ("op", True), "-op": ("op", False),
        "+owner": ("owner", True), "-owner": ("owner", False),
        "+hop": ("hop", True), "-hop": ("hop", False),
    }
    if subcmd not in action_map or len(trigger.args) < 2:
        await bot.reply(  # type: ignore[attr-defined]
            trigger, "Usage: !self +op|-op|+owner|-owner|+hop|-hop <nick>"
        )
        return

    role, adding = action_map[subcmd]
    target_nick = trigger.args[1]
    assert _SelfAuthModel is not None

    async with get_session() as session:
        row = (await session.execute(
            select(_SelfAuthModel).where(  # type: ignore[arg-type]
                _SelfAuthModel.nick == target_nick,  # type: ignore[attr-defined]
                _SelfAuthModel.channel == trigger.channel,  # type: ignore[attr-defined]
            )
        )).scalar_one_or_none()

        if adding:
            if row:
                row.role = role  # type: ignore[attr-defined]
                row.added_by = trigger.nick  # type: ignore[attr-defined]
            else:
                session.add(_SelfAuthModel(  # type: ignore[call-arg]
                    nick=target_nick,
                    channel=trigger.channel,
                    role=role,
                    added_by=trigger.nick,
                ))
            await bot.reply(  # type: ignore[attr-defined]
                trigger, f"{target_nick} added to {role}s list."
            )
        else:
            if row:
                await session.delete(row)
                await bot.reply(trigger, f"{target_nick} removed from {role}s list.")  # type: ignore[attr-defined]
            else:
                await bot.reply(trigger, f"{target_nick} is not on the {role}s list.")  # type: ignore[attr-defined]


@plugin.command("ops", help="Grant yourself +o if on the ops list")
async def cmd_ops(bot: object, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "op", "+o")


@plugin.command("hops", help="Grant yourself +h if on the half-ops list")
async def cmd_hops(bot: object, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "hop", "+h")


@plugin.command("owners", help="Grant yourself +q if on the owners list")
async def cmd_owners(bot: object, trigger: Trigger) -> None:
    await _self_grant(bot, trigger, "owner", "+q")


async def _self_grant(bot: object, trigger: Trigger, role: str, mode: str) -> None:
    from sqlalchemy import select

    from pybot.core.database import get_session

    if not trigger.channel:
        await bot.notice(trigger.nick, "This command only works in a channel.")  # type: ignore[attr-defined]
        return

    if not _allowed_channel(bot, trigger.channel):
        return

    assert _SelfAuthModel is not None
    async with get_session() as session:
        row = (await session.execute(
            select(_SelfAuthModel).where(  # type: ignore[arg-type]
                _SelfAuthModel.nick == trigger.nick,  # type: ignore[attr-defined]
                _SelfAuthModel.channel == trigger.channel,  # type: ignore[attr-defined]
                _SelfAuthModel.role == role,  # type: ignore[attr-defined]
            )
        )).scalar_one_or_none()

    if not row:
        await bot.notice(  # type: ignore[attr-defined]
            trigger.nick, f"You are not on the {role}s list for {trigger.channel}."
        )
        return

    await bot.mode(trigger.channel, mode, trigger.nick)  # type: ignore[attr-defined]
    await bot.notice(  # type: ignore[attr-defined]
        trigger.nick, f"Granted {mode} in {trigger.channel}."
    )
