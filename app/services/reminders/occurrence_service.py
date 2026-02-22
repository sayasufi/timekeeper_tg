from __future__ import annotations

from datetime import datetime

from dateutil.rrule import rrulestr

from app.core.datetime_utils import ensure_utc
from app.db.models import Event


def event_occurrences_between(event: Event, start_utc: datetime, end_utc: datetime) -> list[datetime]:
    start = ensure_utc(start_utc)
    end = ensure_utc(end_utc)
    if end <= start:
        return []

    event_start = ensure_utc(event.starts_at)
    if not event.rrule:
        if start <= event_start < end:
            if _is_excluded(event, event_start):
                return []
            return [event_start]
        return []

    try:
        rule = rrulestr(event.rrule, dtstart=event_start)
    except Exception:
        return []

    occurrences = rule.between(start, end, inc=True)
    normalized = [ensure_utc(dt) for dt in occurrences]
    return [item for item in normalized if not _is_excluded(event, item)]


def event_next_occurrence(event: Event, after_utc: datetime) -> datetime | None:
    after = ensure_utc(after_utc)
    event_start = ensure_utc(event.starts_at)

    if not event.rrule:
        if event_start >= after:
            if _is_excluded(event, event_start):
                return None
            return event_start
        return None

    try:
        rule = rrulestr(event.rrule, dtstart=event_start)
        next_dt = rule.after(after, inc=True)
    except Exception:
        return None

    if next_dt is None:
        return None
    normalized = ensure_utc(next_dt)
    if _is_excluded(event, normalized):
        try:
            later = rule.after(normalized, inc=False)
        except Exception:
            return None
        if later is None:
            return None
        normalized = ensure_utc(later)
    if _is_excluded(event, normalized):
        return None
    return normalized


def _is_excluded(event: Event, occurrence: datetime) -> bool:
    raw = event.extra_data.get("excluded_occurrences", [])
    if not isinstance(raw, list):
        return False
    normalized = occurrence.isoformat()
    return any(str(item) == normalized for item in raw)
