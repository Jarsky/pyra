"""
Async task scheduler — interval jobs and 5-field cron expressions.

Usage (from plugins):
    @plugin.interval(300)
    async def periodic(bot):
        await bot.say("#general", "Still alive!")
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from pybot.plugin import IntervalHandler, get_registry


@dataclass
class _RunningJob:
    handler: IntervalHandler
    task: asyncio.Task[None]


class Scheduler:
    def __init__(self, bot: Any) -> None:
        self._bot = bot
        self._jobs: list[_RunningJob] = []

    async def start(self) -> None:
        """Start tasks for all registered interval handlers."""
        registry = get_registry()
        for handler in registry.intervals:
            task = asyncio.create_task(
                self._interval_loop(handler),
                name=f"scheduler-{handler.plugin_name}-{handler.func.__name__}",
            )
            self._jobs.append(_RunningJob(handler=handler, task=task))

    async def stop(self) -> None:
        for job in self._jobs:
            job.task.cancel()
        self._jobs.clear()

    def remove_plugin_jobs(self, plugin_name: str) -> None:
        """Cancel and remove all jobs for a specific plugin."""
        remaining = []
        for job in self._jobs:
            if job.handler.plugin_name == plugin_name:
                job.task.cancel()
            else:
                remaining.append(job)
        self._jobs = remaining

    def add_interval_handler(self, handler: IntervalHandler) -> None:
        """Register a new interval handler at runtime (used after plugin reload)."""
        task = asyncio.create_task(
            self._interval_loop(handler),
            name=f"scheduler-{handler.plugin_name}-{handler.func.__name__}",
        )
        self._jobs.append(_RunningJob(handler=handler, task=task))

    async def _interval_loop(self, handler: IntervalHandler) -> None:
        while True:
            await asyncio.sleep(handler.seconds)
            try:
                await handler.func(self._bot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    f"Scheduler error in {handler.plugin_name}.{handler.func.__name__}: {exc}"
                )


# ---------------------------------------------------------------------------
# Simple 5-field cron expression parser + next-fire calculator
# ---------------------------------------------------------------------------


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse a 5-field cron expression: 'min hour dom month dow'.

    Returns sets of allowed values for each field.
    Supports: *, */N, ranges (a-b), and comma-separated lists.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got: {expr!r}")

    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    result = []
    for i, (fld, (lo, hi)) in enumerate(zip(fields, ranges)):
        result.append(_parse_cron_field(fld, lo, hi))
    return tuple(result)  # type: ignore[return-value]


def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    if field == "*":
        return set(range(lo, hi + 1))
    result: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            rng, step_str = part.split("/", 1)
            step = int(step_str)
            if rng == "*":
                start, end = lo, hi
            elif "-" in rng:
                a, b = rng.split("-", 1)
                start, end = int(a), int(b)
            else:
                start = end = int(rng)
            result.update(range(start, end + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return result


def next_cron_time(
    expr: str,
    after: datetime | None = None,
) -> datetime:
    """Return the next datetime (UTC) that matches the cron expression."""
    mins, hours, doms, months, dows = parse_cron(expr)
    now = after or datetime.now(tz=timezone.utc)
    # Advance by 1 minute to avoid re-triggering the same minute
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

    for _ in range(366 * 24 * 60):  # max 1 year of minutes
        if (
            candidate.month in months
            and candidate.day in doms
            and candidate.weekday() in dows
            and candidate.hour in hours
            and candidate.minute in mins
        ):
            return candidate
        candidate += timedelta(minutes=1)

    raise ValueError(f"Could not find next run time for cron: {expr!r}")
