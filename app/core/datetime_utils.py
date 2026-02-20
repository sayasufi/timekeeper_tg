from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import dateparser
from dateutil import parser as dateutil_parser


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_datetime_input(value: str, timezone: str, languages: list[str] | None = None) -> datetime | None:
    try:
        parsed_iso = dateutil_parser.isoparse(value)
        return ensure_utc(parsed_iso)
    except (ValueError, TypeError):
        pass

    parsed = dateparser.parse(
        value,
        languages=languages,
        settings={
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if parsed is None:
        return None
    return ensure_utc(parsed)


def parse_date_input(value: str, timezone: str, languages: list[str] | None = None) -> datetime | None:
    parsed = dateparser.parse(
        value,
        languages=languages,
        settings={
            "TIMEZONE": timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "first",
        },
    )
    if parsed is None:
        return None
    return ensure_utc(parsed)


def start_of_local_day(day: datetime, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    local_day = day.astimezone(tz)
    start = datetime.combine(local_day.date(), time.min, tzinfo=tz)
    return start.astimezone(UTC)


def end_of_local_day(day: datetime, timezone: str) -> datetime:
    return start_of_local_day(day, timezone) + timedelta(days=1)


def next_weekday_time(weekday: str, hhmm: str, timezone: str, now_utc: datetime | None = None) -> datetime:
    weekday_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    if weekday not in weekday_map:
        msg = f"Unsupported weekday: {weekday}"
        raise ValueError(msg)

    now = now_utc or datetime.now(tz=UTC)
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)

    hour, minute = hhmm.split(":")
    candidate = local_now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    target_weekday = weekday_map[weekday]

    days_ahead = (target_weekday - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= local_now:
        days_ahead = 7

    candidate = candidate + timedelta(days=days_ahead)
    return candidate.astimezone(UTC)


def user_now(timezone: str) -> datetime:
    return datetime.now(tz=UTC).astimezone(ZoneInfo(timezone))


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def is_local_time_in_range(current: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= current < end
    return current >= start or current < end
