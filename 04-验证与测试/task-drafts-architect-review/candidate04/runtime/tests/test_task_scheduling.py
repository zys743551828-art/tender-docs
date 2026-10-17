"""Business clock boundaries; these tests do not start any task or model."""

from datetime import UTC, datetime

import pytest

from packages.domain.runtime import DomainError
from packages.tasks.scheduling import Schedule, recover


def moment(text):
    return datetime.fromisoformat(text).astimezone(UTC)


def test_china_weekdays_and_strict_boundary():
    schedule = Schedule.parse("0 9 * * 1-5")
    friday = moment("2026-10-16T09:00:00+08:00")
    assert schedule.next_after(friday) == moment("2026-10-19T09:00:00+08:00")
    assert schedule.next_after(friday.replace(second=59)) == schedule.next_after(friday)


def test_calendar_leap_and_month_end_are_real_dates():
    leap = Schedule.parse("0 0 29 2 *")
    assert leap.next_after(moment("2025-01-01T00:00:00Z")) == moment("2028-02-29T00:00:00+08:00")
    last = Schedule.parse("0 0 31 * *")
    assert last.next_after(moment("2026-04-01T00:00:00+08:00")) == moment(
        "2026-05-31T00:00:00+08:00"
    )


def test_spring_gap_is_skipped_and_autumn_folds_have_distinct_utc_identity():
    spring = Schedule.parse("30 2 * * *", "America/New_York")
    assert spring.next_after(moment("2026-03-08T00:00:00-05:00")) == moment(
        "2026-03-09T02:30:00-04:00"
    )
    autumn = Schedule.parse("30 1 * * *", "America/New_York")
    first = autumn.next_after(moment("2026-11-01T00:00:00-04:00"))
    assert first == moment("2026-11-01T01:30:00-04:00")
    assert autumn.next_after(first) == moment("2026-11-01T01:30:00-05:00")


def test_list_step_and_sunday_alias():
    schedule = Schedule.parse("*/15 8,10 * * 0,7")
    assert schedule.next_after(moment("2026-10-18T08:00:00+08:00")) == moment(
        "2026-10-18T08:15:00+08:00"
    )
    assert schedule.next_after(moment("2026-10-18T08:45:00+08:00")) == moment(
        "2026-10-18T10:00:00+08:00"
    )


@pytest.mark.parametrize(
    "cron,zone",
    [
        ("* * * * * *", "Asia/Shanghai"),
        ("@hourly", "Asia/Shanghai"),
        ("60 * * * *", "UTC"),
        ("*/0 * * * *", "UTC"),
        ("0 0 30 2 *", "UTC"),
        ("0 0 1 * 1", "UTC"),
        ("0 0 * JAN *", "UTC"),
        ("0 0 * * *", "../UTC"),
        ("0 0 * * *", "Missing/Zone"),
        ("0 0 * * ８", "UTC"),
        ("0, * * * *", "UTC"),
        ("5/2 * * * *", "UTC"),
    ],
)
def test_invalid_or_ambiguous_schedule_is_rejected(cron, zone):
    with pytest.raises(DomainError) as failure:
        Schedule.parse(cron, zone)
    assert failure.value.code == "TASK_SCHEDULE_INVALID"


def test_many_years_of_missed_minutes_never_create_catchup_runs():
    schedule = Schedule.parse("* * * * *")
    observed = moment("2026-10-17T09:01:33+08:00")
    result = recover(schedule, moment("2010-01-01T00:00:00Z"), observed)
    assert result["automatic_catchup_runs"] == 0
    assert result["next_fire_at"] == moment("2026-10-17T09:02:00+08:00")
    assert result["missed"]["count"] is None  # Not a fabricated exact count.
    assert result["missed"]["through"] == observed


def test_future_next_fire_is_preserved_and_naive_times_are_rejected():
    schedule = Schedule.parse("0 9 * * *")
    future = moment("2026-10-18T09:00:00+08:00")
    assert recover(schedule, future, moment("2026-10-17T09:00:00+08:00"))["next_fire_at"] == future
    with pytest.raises(DomainError) as failure:
        schedule.next_after(datetime(2026, 10, 17))
    assert failure.value.code == "TASK_TIMEZONE_REQUIRED"
