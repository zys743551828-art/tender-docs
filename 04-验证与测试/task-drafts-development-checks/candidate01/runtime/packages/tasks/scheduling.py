"""Five-field schedules, UTC identities and bounded skip recovery; no task execution."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.domain.runtime import DomainError

HORIZON_DAYS = 366 * 8


def invalid():
    return DomainError(
        "TASK_SCHEDULE_INVALID",
        "请输入合法五段cron和IANA时区；日期与星期不能同时限制，不支持秒、宏或文字月份",
        422,
    )


def field(expression, low, high):
    values: set[int] = set()
    for piece in expression.split(","):
        if not piece or piece.count("/") > 1:
            raise invalid()
        interval, separator, step_text = piece.partition("/")
        if separator and (not step_text.isascii() or not step_text.isdecimal()):
            raise invalid()
        step = int(step_text) if separator else 1
        if not 1 <= step <= high - low + 1:
            raise invalid()
        if interval == "*":
            start, stop = low, high
        elif "-" in interval:
            bounds = interval.split("-")
            if len(bounds) != 2 or not all(x.isascii() and x.isdecimal() for x in bounds):
                raise invalid()
            start, stop = map(int, bounds)
        else:
            if not interval.isascii() or not interval.isdecimal() or separator:
                raise invalid()
            start = stop = int(interval)
        if not low <= start <= stop <= high:
            raise invalid()
        values.update(range(start, stop + 1, step))
    return frozenset(values)


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainError("TASK_TIMEZONE_REQUIRED", "任务时刻必须包含时区", 422)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Schedule:
    cron: str
    timezone: str
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]

    @classmethod
    def parse(cls, cron, timezone="Asia/Shanghai"):
        if not isinstance(cron, str) or len(cron) > 200:
            raise invalid()
        parts = cron.split()
        if len(parts) != 5 or parts[2] != "*" and parts[4] != "*":
            raise invalid()
        if not isinstance(timezone, str) or len(timezone) > 100:
            raise invalid()
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise invalid() from None
        limits = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
        fields = [field(text, *bounds) for text, bounds in zip(parts, limits, strict=True)]
        fields[-1] = frozenset(x % 7 for x in fields[-1])
        result = cls(" ".join(parts), timezone, *fields)
        # A Gregorian 400-year cycle proves combinations such as February 30 impossible.
        if not any(
            result.matches_date(date(year, month, day))
            for year in range(2000, 2400)
            for month in result.month
            for day in range(1, 32)
            if _valid_date(year, month, day)
        ):
            raise invalid()
        return result

    def matches_date(self, value):
        return (
            value.month in self.month
            and value.day in self.day
            and (value.weekday() + 1) % 7 in self.weekday
        )

    def next_after(self, after):
        """Strictly after an aware instant, preserving separate DST fold UTC identities."""
        after = utc(after)
        zone = ZoneInfo(self.timezone)
        first = after.astimezone(zone).date()
        for offset in range(HORIZON_DAYS + 1):
            candidate_date = first + timedelta(days=offset)
            if not self.matches_date(candidate_date):
                continue
            candidates = set()
            for hour in self.hour:
                for minute in self.minute:
                    wall = datetime.combine(candidate_date, datetime.min.time()).replace(
                        hour=hour, minute=minute
                    )
                    for fold in (0, 1):
                        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                        # Exclude nonexistent wall times; collapse ordinary fold aliases.
                        if (
                            instant.astimezone(zone).replace(tzinfo=None) == wall
                            and instant > after
                        ):
                            candidates.add(instant)
            if candidates:
                return min(candidates)
        raise DomainError("TASK_SCHEDULE_HORIZON", "未来八年没有可执行时刻，请修改定时设置", 422)


def _valid_date(year, month, day):
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def recover(schedule, next_fire_at, observed_at):
    """Skip overdue slots in bounded work, never enqueue historical paid catch-up."""
    due, observed = utc(next_fire_at), utc(observed_at)
    if due > observed:
        return {"next_fire_at": due, "missed": None, "automatic_catchup_runs": 0}
    return {
        "next_fire_at": schedule.next_after(observed),
        "missed": {
            "from": due,
            "through": observed,
            "count": None,
            "count_status": "not_enumerated",
            "resolution": "skipped_run_current_once_available",
        },
        "automatic_catchup_runs": 0,
    }
