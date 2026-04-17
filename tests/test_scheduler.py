"""Tests for the cron expression parser and scheduler."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pybot.core.scheduler import next_cron_time, parse_cron


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
