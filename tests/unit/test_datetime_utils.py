from __future__ import annotations

from datetime import UTC, datetime

from app.core.datetime_utils import ensure_utc, next_weekday_time, parse_datetime_input


def test_ensure_utc_on_naive_datetime() -> None:
    naive = datetime(2026, 1, 1, 12, 0)

    utc_dt = ensure_utc(naive)

    assert utc_dt.tzinfo == UTC


def test_parse_datetime_input_iso() -> None:
    parsed = parse_datetime_input("2026-04-01T10:00:00+03:00", timezone="Europe/Moscow")

    assert parsed is not None
    assert parsed.tzinfo == UTC


def test_next_weekday_time_returns_future_datetime() -> None:
    now = datetime(2026, 2, 19, 10, 0, tzinfo=UTC)
    nxt = next_weekday_time("FR", "12:00", timezone="UTC", now_utc=now)

    assert nxt > now
