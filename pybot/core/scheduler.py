"""
Async task scheduler — interval jobs and 5-field cron expressions.

Usage (from plugins):
    @plugin.interval(300)
    async def periodic(bot):
        await bot.say("#general", "Still alive!")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from pybot.plugin import IntervalHandler, get_registry


@dataclass
class _RunningJob:
    name: str
    handler: IntervalHandler
    task: asyncio.Task[None]
    paused: bool = False
    last_run: datetime | None = None
    next_run: datetime | None = None


class Scheduler:
    def __init__(self, bot: Any) -> None:
        self._bot = bot
        self._jobs: list[_RunningJob] = []

    async def start(self) -> None:
        """Start tasks for all registered interval handlers."""
        registry = get_registry()
        for handler in registry.intervals:
            self._jobs.append(self._start_job(handler))

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
        self._jobs.append(self._start_job(handler))

    def list_jobs(self) -> list[dict[str, str | bool | None]]:
        return [
            {
                "name": job.name,
                "schedule": self._format_schedule(job.handler),
                "paused": job.paused,
                "next_run": job.next_run.isoformat() if job.next_run else None,
                "last_run": job.last_run.isoformat() if job.last_run else None,
            }
            for job in self._jobs
        ]

    def pause_job(self, name: str) -> bool:
        for job in self._jobs:
            if job.name == name:
                job.paused = True
                return True
        return False

    def resume_job(self, name: str) -> bool:
        for job in self._jobs:
            if job.name == name:
                job.paused = False
                return True
        return False

    def _start_job(self, handler: IntervalHandler) -> _RunningJob:
        job_name = f"{handler.plugin_name}.{handler.func.__name__}"
        if handler.cron:
            task = asyncio.create_task(
                self._cron_loop(job_name, handler),
                name=f"scheduler-{handler.plugin_name}-{handler.func.__name__}",
            )
        else:
            task = asyncio.create_task(
                self._interval_loop(job_name, handler),
                name=f"scheduler-{handler.plugin_name}-{handler.func.__name__}",
            )
        return _RunningJob(name=job_name, handler=handler, task=task)

    def _format_schedule(self, handler: IntervalHandler) -> str:
        if handler.cron:
            return f"cron:{handler.cron}"
        if handler.seconds is not None:
            return f"every {handler.seconds:g}s"
        return "unknown"

    def _warn_if_delayed(
        self, job_name: str, delay_seconds: float, expected_seconds: float
    ) -> None:
        if expected_seconds <= 0:
            return
        if delay_seconds > expected_seconds * 0.1:
            logger.warning(
                f"Scheduler late fire: {job_name} delayed by {delay_seconds:.2f}s "
                f"(expected interval {expected_seconds:.2f}s)"
            )

    def _get_job(self, job_name: str) -> _RunningJob | None:
        for job in self._jobs:
            if job.name == job_name:
                return job
        return None

    async def _interval_loop(self, job_name: str, handler: IntervalHandler) -> None:
        seconds = float(handler.seconds or 0)
        if seconds <= 0:
            logger.error(
                f"Scheduler invalid interval for {handler.plugin_name}.{handler.func.__name__}: "
                f"{handler.seconds!r}"
            )
            return

        loop = asyncio.get_event_loop()
        next_due = loop.time() + seconds
        while True:
            wait = max(0.0, next_due - loop.time())
            await asyncio.sleep(wait)

            now_mono = loop.time()
            delay = max(0.0, now_mono - next_due)
            self._warn_if_delayed(job_name, delay, seconds)

            job = self._get_job(job_name)
            if job:
                job.next_run = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
                if job.paused:
                    next_due = now_mono + seconds
                    continue

            try:
                await handler.func(self._bot)
                if job:
                    job.last_run = datetime.now(tz=timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    f"Scheduler error in {handler.plugin_name}.{handler.func.__name__}: {exc}"
                )

            next_due += seconds
            if next_due < now_mono:
                next_due = now_mono + seconds

    async def _cron_loop(self, job_name: str, handler: IntervalHandler) -> None:
        assert handler.cron is not None
        anchor = datetime.now(tz=timezone.utc)

        while True:
            next_fire = next_cron_time(handler.cron, after=anchor)
            now = datetime.now(tz=timezone.utc)
            wait = max(0.0, (next_fire - now).total_seconds())

            job = self._get_job(job_name)
            if job:
                job.next_run = next_fire

            await asyncio.sleep(wait)
            current = datetime.now(tz=timezone.utc)
            delay = max(0.0, (current - next_fire).total_seconds())
            subsequent = next_cron_time(handler.cron, after=next_fire)
            expected = (subsequent - next_fire).total_seconds()
            self._warn_if_delayed(job_name, delay, expected)

            if job and job.paused:
                anchor = next_fire
                continue

            try:
                await handler.func(self._bot)
                if job:
                    job.last_run = datetime.now(tz=timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    f"Scheduler error in {handler.plugin_name}.{handler.func.__name__}: {exc}"
                )

            anchor = next_fire


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
    for _i, (fld, (lo, hi)) in enumerate(zip(fields, ranges, strict=True)):
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
