"""Notes plugin — store admin notes about users."""

from __future__ import annotations

from datetime import datetime, timezone

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command(
    "note",
    help="Manage notes about users",
    usage="!note add <text> | list | del <id> | show <id>",
)
async def cmd_note(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !note add <text> | list | del <id> | show <id>")  # type: ignore[attr-defined]
        return

    subcommand = trigger.args[0].lower()

    if subcommand == "add":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !note add <text>")  # type: ignore[attr-defined]
            return
        await _note_add(bot, trigger, " ".join(trigger.args[1:]))

    elif subcommand == "list":
        await _note_list(bot, trigger)

    elif subcommand == "del":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !note del <id>")  # type: ignore[attr-defined]
            return
        try:
            note_id = int(trigger.args[1])
        except ValueError:
            await bot.reply(trigger, "Note ID must be a number.")  # type: ignore[attr-defined]
            return
        await _note_del(bot, trigger, note_id)

    elif subcommand == "show":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !note show <id>")  # type: ignore[attr-defined]
            return
        try:
            note_id = int(trigger.args[1])
        except ValueError:
            await bot.reply(trigger, "Note ID must be a number.")  # type: ignore[attr-defined]
            return
        await _note_show(bot, trigger, note_id)

    else:
        await bot.reply(trigger, "Unknown subcommand. Use: add, list, del, show")  # type: ignore[attr-defined]


async def _note_add(bot: object, trigger: Trigger, content: str) -> None:
    from sqlalchemy import select

    from pybot.core.database import Note, User, get_session

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == trigger.nick))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                nick=trigger.nick,
                hostmask=trigger.hostmask,
                global_flags="",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(user)
            await session.flush()

        note = Note(
            user_id=user.id,
            author_nick=trigger.nick,
            content=content[:1024],
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(note)
        await session.flush()
        note_id = note.id

    await bot.reply(trigger, f"Note saved (ID: {note_id}).")  # type: ignore[attr-defined]


async def _note_list(bot: object, trigger: Trigger) -> None:
    from sqlalchemy import select

    from pybot.core.database import Note, User, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Note)
            .join(User)
            .where(User.nick == trigger.nick)
            .order_by(Note.created_at.desc())
            .limit(10)
        )
        notes = result.scalars().all()

    if not notes:
        await bot.reply(trigger, "You have no notes.")  # type: ignore[attr-defined]
        return

    await bot.notice(trigger.nick, "Your recent notes:")  # type: ignore[attr-defined]
    for n in notes:
        date = n.created_at.strftime("%Y-%m-%d") if n.created_at else "?"
        preview = n.content[:60] + ("..." if len(n.content) > 60 else "")
        await bot.notice(trigger.nick, f"  [{n.id}] {date}: {preview}")  # type: ignore[attr-defined]


async def _note_del(bot: object, trigger: Trigger, note_id: int) -> None:
    from sqlalchemy import select

    from pybot.core.database import Note, User, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Note).join(User).where(Note.id == note_id, User.nick == trigger.nick)
        )
        note = result.scalar_one_or_none()
        if not note:
            await bot.reply(trigger, f"Note {note_id} not found or not yours.")  # type: ignore[attr-defined]
            return
        await session.delete(note)

    await bot.reply(trigger, f"Note {note_id} deleted.")  # type: ignore[attr-defined]


async def _note_show(bot: object, trigger: Trigger, note_id: int) -> None:
    from sqlalchemy import select

    from pybot.core.database import Note, User, get_session

    async with get_session() as session:
        result = await session.execute(
            select(Note).join(User).where(Note.id == note_id, User.nick == trigger.nick)
        )
        note = result.scalar_one_or_none()

    if not note:
        await bot.reply(trigger, f"Note {note_id} not found or not yours.")  # type: ignore[attr-defined]
        return

    date = note.created_at.strftime("%Y-%m-%d %H:%M UTC") if note.created_at else "?"
    await bot.notice(trigger.nick, f"Note {note_id} ({date}):")  # type: ignore[attr-defined]
    await bot.notice(trigger.nick, note.content)  # type: ignore[attr-defined]
