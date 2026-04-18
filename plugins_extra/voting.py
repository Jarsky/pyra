"""
Voting plugin — DB-backed polls with per-channel one-active-poll limit.

Author:  Jarsky
Version: 2.0.0
Date:    2026-04-18


Commands:
  !vote <duration>|<topic>|<ans1>:<ans2>:...   Start a poll
  !vote                                         Show active/last poll results
  !vote <answer>                                Cast your vote (also via /msg)
  !endvote                                      End current poll early (starter or op+)

Duration format: 5m, 2h, 1h30m
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pybot import plugin
from pybot.core.database import Base
from pybot.plugin import Trigger

_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


class Poll(Base):
    """A channel poll / vote."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(128), index=True)
    topic: Mapped[str] = mapped_column(Text)
    answers: Mapped[str] = mapped_column(Text)  # JSON list of answer strings
    starter_nick: Mapped[str] = mapped_column(String(64))
    starter_hostmask: Mapped[str] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended: Mapped[bool] = mapped_column(Boolean, default=False)

    votes: Mapped[list["PollVote"]] = relationship(
        "PollVote", back_populates="poll", cascade="all, delete-orphan"
    )


class PollVote(Base):
    """A single vote cast in a Poll."""

    __tablename__ = "poll_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(Integer, ForeignKey("polls.id"), index=True)
    hostmask: Mapped[str] = mapped_column(String(256))
    answer: Mapped[str] = mapped_column(String(200))
    voted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    poll: Mapped["Poll"] = relationship("Poll", back_populates="votes")


async def setup(bot: object) -> None:
    from pybot.core.database import ensure_plugin_tables

    await ensure_plugin_tables(Poll, PollVote)


def _parse_duration(s: str) -> int | None:
    m = _DURATION_RE.match(s.strip())
    if not m or not any(m.groups()):
        return None
    total = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60
    return total if total > 0 else None


def _fmt_results(topic: str, answers: list[str], votes: list[str]) -> list[str]:
    count = Counter(votes)
    total = len(votes)
    lines = [f"\x02Poll: {topic}\x02 — {total} vote(s)"]
    for ans in answers:
        n = count.get(ans, 0)
        pct = int(n / total * 100) if total else 0
        bar = "█" * (pct // 10)
        lines.append(f"  {ans}: {n} ({pct}%) {bar}")
    return lines


@plugin.command(
    "vote",
    help="Start a poll or cast/check votes",
    usage="!vote <dur>|<topic>|<ans1>:<ans2>  or  !vote <answer>  or  !vote",
)
async def cmd_vote(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import get_session

    channel = trigger.channel or trigger.nick

    # ── No args: show status ───────────────────────────────────────────────
    if not trigger.args:
        async with get_session() as session:
            poll = (
                await session.execute(
                    select(Poll).where(Poll.channel == channel).order_by(Poll.started_at.desc())
                )
            ).scalar_one_or_none()
            if not poll:
                await bot.reply(  # type: ignore[attr-defined]
                    trigger, "No poll yet. Start one: !vote <dur>|<topic>|<ans1>:<ans2>"
                )
                return
            answers = json.loads(poll.answers)
            votes = [
                v.answer
                for v in (
                    await session.execute(select(PollVote).where(PollVote.poll_id == poll.id))
                )
                .scalars()
                .all()
            ]
        label = "ACTIVE" if not poll.ended else "ENDED"
        for line in _fmt_results(f"{poll.topic} [{label}]", answers, votes):
            await bot.say(channel, line)  # type: ignore[attr-defined]
        return

    raw = " ".join(trigger.args)

    # ── Pipe-separated: start new poll ────────────────────────────────────
    if "|" in raw:
        parts = raw.split("|", 2)
        if len(parts) != 3:
            await bot.reply(trigger, "Usage: !vote <duration>|<topic>|<ans1>:<ans2>:...")  # type: ignore[attr-defined]
            return
        dur_str, topic, ans_str = parts
        secs = _parse_duration(dur_str.strip())
        if not secs:
            await bot.reply(  # type: ignore[attr-defined]
                trigger, f"Invalid duration '{dur_str.strip()}'. Examples: 5m, 2h, 1h30m"
            )
            return
        answers = [a.strip() for a in ans_str.split(":") if a.strip()]
        if len(answers) < 2:
            await bot.reply(trigger, "Need at least 2 answers separated by ':'")  # type: ignore[attr-defined]
            return

        async with get_session() as session:
            existing = (
                await session.execute(
                    select(Poll).where(Poll.channel == channel, Poll.ended == False)  # noqa: E712
                )
            ).scalar_one_or_none()
            if existing:
                await bot.reply(  # type: ignore[attr-defined]
                    trigger, f"Poll already active: '{existing.topic}' — !endvote first"
                )
                return
            ends_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
            poll = Poll(
                channel=channel,
                topic=topic.strip(),
                answers=json.dumps(answers),
                starter_nick=trigger.nick,
                starter_hostmask=trigger.hostmask,
                ends_at=ends_at,
            )
            session.add(poll)
            await session.flush()
            poll_id = poll.id

        dur_h = f"{secs // 3600}h" if secs >= 3600 else ""
        dur_m = f"{(secs % 3600) // 60}m" if secs % 3600 else ""
        dur_human = dur_h + dur_m
        ans_list = " | ".join(f"\x02{a}\x02" for a in answers)
        await bot.say(channel, f"\x02POLL STARTED\x02 ({dur_human}): {topic.strip()} — {ans_list}")  # type: ignore[attr-defined]
        asyncio.get_event_loop().call_later(
            secs, lambda: asyncio.create_task(_end_poll(bot, channel, poll_id))
        )
        return

    # ── Cast a vote ────────────────────────────────────────────────────────
    answer = raw.strip()
    async with get_session() as session:
        poll = (
            await session.execute(
                select(Poll)
                .where(Poll.channel == channel, Poll.ended == False)  # noqa: E712
                .order_by(Poll.started_at.desc())
            )
        ).scalar_one_or_none()
        if not poll:
            await bot.reply(trigger, "No active poll.")  # type: ignore[attr-defined]
            return
        answers = json.loads(poll.answers)
        matched = next((a for a in answers if a.lower() == answer.lower()), None)
        if not matched:
            await bot.reply(trigger, f"Invalid answer. Choose: {', '.join(answers)}")  # type: ignore[attr-defined]
            return
        existing_vote = (
            await session.execute(
                select(PollVote).where(
                    PollVote.poll_id == poll.id, PollVote.hostmask == trigger.hostmask
                )
            )
        ).scalar_one_or_none()
        if existing_vote:
            existing_vote.answer = matched
            await bot.reply(trigger, f"Vote changed to: \x02{matched}\x02")  # type: ignore[attr-defined]
        else:
            session.add(PollVote(poll_id=poll.id, hostmask=trigger.hostmask, answer=matched))
            await bot.reply(trigger, f"Voted: \x02{matched}\x02")  # type: ignore[attr-defined]


@plugin.command("endvote", help="End the current poll early")
async def cmd_endvote(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import get_session

    channel = trigger.channel or trigger.nick

    async with get_session() as session:
        poll = (
            await session.execute(
                select(Poll).where(Poll.channel == channel, Poll.ended == False)  # noqa: E712
            )
        ).scalar_one_or_none()
        if not poll:
            await bot.reply(trigger, "No active poll.")  # type: ignore[attr-defined]
            return
        if trigger.nick.lower() != poll.starter_nick.lower() and not trigger.admin:
            await bot.reply(trigger, "Only the poll starter or admins can end the poll.")  # type: ignore[attr-defined]
            return
        poll.ended = True
        answers = json.loads(poll.answers)
        votes = [
            v.answer
            for v in (await session.execute(select(PollVote).where(PollVote.poll_id == poll.id)))
            .scalars()
            .all()
        ]

    await bot.say(channel, "\x02POLL ENDED\x02")  # type: ignore[attr-defined]
    for line in _fmt_results(poll.topic, answers, votes):
        await bot.say(channel, line)  # type: ignore[attr-defined]


async def _end_poll(bot: object, channel: str, poll_id: int) -> None:
    from sqlalchemy import select

    from pybot.core.database import get_session

    async with get_session() as session:
        poll = await session.get(Poll, poll_id)
        if not poll or poll.ended:
            return
        poll.ended = True
        answers = json.loads(poll.answers)
        votes = [
            v.answer
            for v in (await session.execute(select(PollVote).where(PollVote.poll_id == poll_id)))
            .scalars()
            .all()
        ]

    await bot.say(channel, "\x02POLL ENDED\x02")  # type: ignore[attr-defined]
    for line in _fmt_results(poll.topic, answers, votes):
        await bot.say(channel, line)  # type: ignore[attr-defined]
