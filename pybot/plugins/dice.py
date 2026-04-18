"""Dice plugin — NdM+K dice rolling."""

from __future__ import annotations

import random
import re

from pybot import plugin
from pybot.plugin import Trigger

_DICE_RE = re.compile(
    r"^(\d+)?d(\d+)(?:([+-]\d+))?(?:\s+(drop\s+(?:lowest|highest)))?$",
    re.IGNORECASE,
)

MAX_DICE = 100
MAX_SIDES = 10000


@plugin.command(
    "roll",
    aliases=["dice"],
    help="Roll dice using NdM+K notation",
    usage="!roll 2d6+3 | !roll d20 | !roll 4d6 drop lowest",
)
async def cmd_roll(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !roll <NdM[+K]> [drop lowest|highest]")  # type: ignore[attr-defined]
        return

    expr = " ".join(trigger.args).strip()
    result = _parse_and_roll(expr)
    await bot.say(trigger.target, f"{trigger.nick}: {result}")  # type: ignore[attr-defined]


@plugin.command("rand", help="Random number in range", usage="!rand <min> <max>")
async def cmd_rand(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !rand <min> <max>")  # type: ignore[attr-defined]
        return
    try:
        lo = int(trigger.args[0])
        hi = int(trigger.args[1])
    except ValueError:
        await bot.reply(trigger, "Min and max must be integers.")  # type: ignore[attr-defined]
        return
    if lo > hi:
        lo, hi = hi, lo
    result = random.randint(lo, hi)  # noqa: S311
    await bot.say(trigger.target, f"{trigger.nick}: {result}")  # type: ignore[attr-defined]


def _parse_and_roll(expr: str) -> str:
    # Normalise: "drop lowest" / "drop highest"
    drop_mode: str | None = None
    expr_clean = expr
    dl_match = re.search(r"\s+drop\s+(lowest|highest)", expr, re.IGNORECASE)
    if dl_match:
        drop_mode = dl_match.group(1).lower()
        expr_clean = expr[:dl_match.start()].strip()

    m = _DICE_RE.match(expr_clean.strip())
    if not m:
        return f"Invalid dice notation: {expr!r}. Use e.g. 2d6, d20, 3d8+5"

    num_dice = int(m.group(1)) if m.group(1) else 1
    num_sides = int(m.group(2))
    modifier = int(m.group(3)) if m.group(3) else 0

    if num_dice < 1:
        return "Must roll at least 1 die."
    if num_dice > MAX_DICE:
        return f"Too many dice (max {MAX_DICE})."
    if num_sides < 1:
        return "Dice must have at least 1 side."
    if num_sides > MAX_SIDES:
        return f"Too many sides (max {MAX_SIDES})."

    rolls = [random.randint(1, num_sides) for _ in range(num_dice)]  # noqa: S311
    original_rolls = list(rolls)

    if drop_mode and num_dice > 1:
        if drop_mode == "lowest":
            rolls.remove(min(rolls))
        else:
            rolls.remove(max(rolls))

    total = sum(rolls) + modifier

    rolls_str = ", ".join(str(r) for r in original_rolls)
    parts = [f"Rolled {num_dice}d{num_sides}"]
    if drop_mode:
        parts.append(f"drop {drop_mode}")
    parts.append(f"[{rolls_str}]")

    if modifier:
        sign = "+" if modifier > 0 else ""
        parts.append(f"{sign}{modifier}")

    parts.append(f"= \x02{total}\x02")
    return " ".join(parts)
