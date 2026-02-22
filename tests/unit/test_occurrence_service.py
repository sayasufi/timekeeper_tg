from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import Event
from app.services.reminders.occurrence_service import (
    event_next_occurrence,
    event_occurrences_between,
)


def test_occurrences_for_one_time_event() -> None:
    event = Event(
        user_id=1,
        event_type="reminder",
        title="One",
        starts_at=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )

    items = event_occurrences_between(
        event,
        datetime(2026, 3, 1, 7, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
    )

    assert len(items) == 1


def test_occurrences_for_weekly_rrule() -> None:
    event = Event(
        user_id=1,
        event_type="lesson",
        title="Math",
        starts_at=datetime(2026, 3, 2, 8, 0, tzinfo=UTC),
        rrule="FREQ=WEEKLY;BYDAY=MO",
        remind_offsets=[15],
        extra_data={},
    )

    items = event_occurrences_between(
        event,
        datetime(2026, 3, 9, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 10, 0, 0, tzinfo=UTC),
    )

    assert len(items) == 1


def test_next_occurrence_returns_none_for_past_one_time() -> None:
    event = Event(
        user_id=1,
        event_type="reminder",
        title="Past",
        starts_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )

    nxt = event_next_occurrence(event, datetime(2026, 1, 2, 0, 0, tzinfo=UTC))

    assert nxt is None

