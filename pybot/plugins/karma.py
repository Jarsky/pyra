"""
Karma plugin — track nick++ / nick-- karma scores.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Usage:
  <nick>++          Increment nick's karma
  <nick>--          Decrement nick's karma
  ++<nick>          Also works (prefix form)
  !karma [nick]     Show karma for nick (or yourself)
  !karma top [n]    Top n by karma (default 5)
  !karma bottom [n] Bottom n by karma (default 5)

Rules:
  - Can't karma yourself
  - Self-karma attempts are quietly ignored (no lecture)
"""

from __future__ import annotations

import re

from pybot import plugin
from pybot.plugin import Trigger

_SUFFIX_RE = re.compile(r"(\S+?)(\+\+|--)(?:\s|$)")
_PREFIX_RE = re.compile(r"(?:^|\s)(\+\+|--)(\S+)")


@plugin.rule(r"(?:\S+\+\+|\S+--|(?:\+\+|--)\S+)")
async def karma_listener(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Karma, get_session

    text = trigger.message.text or ""
    changes: dict[str, int] = {}

    for m in _SUFFIX_RE.finditer(text):
        nick, op = m.group(1), m.group(2)
        changes[nick.lower()] = changes.get(nick.lower(), 0) + (1 if op == "++" else -1)

    for m in _PREFIX_RE.finditer(text):
        op, nick = m.group(1), m.group(2)
        changes[nick.lower()] = changes.get(nick.lower(), 0) + (1 if op == "++" else -1)

    if not changes:
        return

    # Remove self-karma silently
    changes.pop(trigger.nick.lower(), None)

    if not changes:
        return

    async with get_session() as session:
        for nick_lower, delta in changes.items():
            row = (await session.execute(
                select(Karma).where(Karma.nick == nick_lower)
            )).scalar_one_or_none()
            if row is None:
                row = Karma(nick=nick_lower, score=0, given_up=0, given_down=0)
                session.add(row)
            row.score += delta
            if delta > 0:
                row.given_up += delta
            else:
                row.given_down += abs(delta)

            score = row.score
            sign = "+" if delta > 0 else ""
            await bot.say(  # type: ignore[attr-defined]
                trigger.channel or trigger.nick,
                f"{nick_lower} karma: {score:+d} ({sign}{delta})"
            )


@plugin.command(
    "karma",
    help="Show karma score",
    usage="!karma [nick] | !karma top [n] | !karma bottom [n]",
)
async def cmd_karma(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Karma, get_session

    target = trigger.channel or trigger.nick

    if not trigger.args:
        nick = trigger.nick.lower()
        async with get_session() as session:
            row = (await session.execute(
                select(Karma).where(Karma.nick == nick)
            )).scalar_one_or_none()
        score = row.score if row else 0
        await bot.say(target, f"{nick} has karma {score:+d}")  # type: ignore[attr-defined]
        return

    sub = trigger.args[0].lower()

    if sub in ("top", "bottom"):
        has_n = len(trigger.args) > 1 and trigger.args[1].isdigit()
        n = min(int(trigger.args[1]) if has_n else 5, 10)
        async with get_session() as session:
            order = Karma.score.desc() if sub == "top" else Karma.score.asc()
            rows = (await session.execute(select(Karma).order_by(order).limit(n))).scalars().all()
        if not rows:
            await bot.say(target, "No karma data yet.")  # type: ignore[attr-defined]
            return
        label = "Top" if sub == "top" else "Bottom"
        entries = ", ".join(f"{r.nick} ({r.score:+d})" for r in rows)
        await bot.say(target, f"{label} {n} karma: {entries}")  # type: ignore[attr-defined]
        return

    # Look up specific nick
    nick = trigger.args[0].lower()
    async with get_session() as session:
        row = (await session.execute(select(Karma).where(Karma.nick == nick))).scalar_one_or_none()
    score = row.score if row else 0
    await bot.say(target, f"{nick} has karma {score:+d}")  # type: ignore[attr-defined]
