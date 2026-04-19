"""Tests for the cron expression parser and scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from pybot import plugin as plugin_api
from pybot.core.scheduler import Scheduler, next_cron_time, parse_cron
from pybot.plugin import IntervalHandler

# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------


def test_wildcard_all_fields() -> None:
    mins, hours, doms, months, dows = parse_cron("* * * * *")
    assert 0 in mins and 59 in mins
    assert 0 in hours and 23 in hours
    assert 1 in doms and 31 in doms


def test_specific_values() -> None:
    mins, hours, doms, months, dows = parse_cron("30 8 1 6 0")
    assert mins == {30}
    assert hours == {8}
    assert doms == {1}
    assert months == {6}
    assert dows == {0}


def test_step_syntax() -> None:
    mins, *_ = parse_cron("*/15 * * * *")
    assert mins == {0, 15, 30, 45}


def test_range_syntax() -> None:
    _, hours, *_ = parse_cron("* 9-17 * * *")
    assert hours == set(range(9, 18))


def test_comma_list() -> None:
    mins, *_ = parse_cron("0,15,30,45 * * * *")
    assert mins == {0, 15, 30, 45}


def test_invalid_field_count() -> None:
    with pytest.raises(ValueError, match="5 fields"):
        parse_cron("* * * *")  # only 4 fields


# ---------------------------------------------------------------------------
# next_cron_time
# ---------------------------------------------------------------------------


def test_next_minute() -> None:
    after = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = next_cron_time("* * * * *", after=after)
    assert nxt == datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)


def test_next_hour_boundary() -> None:
    after = datetime(2024, 1, 1, 12, 59, 0, tzinfo=timezone.utc)
    nxt = next_cron_time("0 * * * *", after=after)
    assert nxt == datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)


def test_specific_time_today() -> None:
    after = datetime(2024, 6, 1, 7, 0, 0, tzinfo=timezone.utc)
    nxt = next_cron_time("30 8 * * *", after=after)
    assert nxt == datetime(2024, 6, 1, 8, 30, 0, tzinfo=timezone.utc)


def test_specific_time_tomorrow() -> None:
    after = datetime(2024, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    nxt = next_cron_time("30 8 * * *", after=after)
    # 08:30 already passed today, should be tomorrow
    assert nxt == datetime(2024, 6, 2, 8, 30, 0, tzinfo=timezone.utc)


def test_specific_day_of_month() -> None:
    after = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    nxt = next_cron_time("0 0 15 * *", after=after)
    assert nxt == datetime(2024, 6, 15, 0, 0, 0, tzinfo=timezone.utc)


def test_warn_if_delayed_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = Scheduler(bot=None)
    warnings: list[str] = []

    from pybot.core import scheduler as scheduler_mod

    monkeypatch.setattr(scheduler_mod.logger, "warning", lambda msg: warnings.append(str(msg)))

    scheduler._warn_if_delayed("test.job", 5.0, 100.0)
    scheduler._warn_if_delayed("test.job", 11.0, 100.0)

    assert len(warnings) == 1
    assert "test.job" in warnings[0]


@pytest.mark.asyncio
async def test_scheduler_list_pause_resume_for_interval_job() -> None:
    events: list[str] = []

    async def _job(_bot: object) -> None:
        events.append("ran")

    scheduler = Scheduler(bot=object())
    handler = IntervalHandler(seconds=60.0, cron=None, func=_job, plugin_name="demo")
    scheduler.add_interval_handler(handler)

    try:
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "demo._job"
        assert jobs[0]["paused"] is False
        assert jobs[0]["schedule"] == "every 60s"

        assert scheduler.pause_job("demo._job") is True
        jobs = scheduler.list_jobs()
        assert jobs[0]["paused"] is True

        assert scheduler.resume_job("demo._job") is True
        jobs = scheduler.list_jobs()
        assert jobs[0]["paused"] is False

        assert scheduler.pause_job("does.not.exist") is False
        assert scheduler.resume_job("does.not.exist") is False
    finally:
        await scheduler.stop()
        await asyncio.sleep(0)


def test_plugin_interval_accepts_cron_expression() -> None:
    registry = plugin_api.get_registry()
    before = len(registry.intervals)

    plugin_api._set_current_plugin("cron_test")

    @plugin_api.interval("*/5 * * * *")
    async def _cron_job(_bot: object) -> None:
        return None

    try:
        handler = registry.intervals[-1]
        assert handler.plugin_name == "cron_test"
        assert handler.cron == "*/5 * * * *"
        assert handler.seconds is None
    finally:
        del registry.intervals[before:]
