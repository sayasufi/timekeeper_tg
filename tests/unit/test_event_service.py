from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.domain.commands import (
    CreateBirthdayCommand,
    CreateReminderCommand,
    CreateScheduleCommand,
    CreateStudentCommand,
    DeleteReminderCommand,
    DeleteStudentCommand,
    ListEventsCommand,
    ParseBankTransferCommand,
    ScheduleSlotInput,
    StudentCardCommand,
    UpdateReminderCommand,
    UpdateScheduleCommand,
    UpdateStudentCommand,
)
from app.domain.enums import Intent
from app.repositories.event_repository import EventRepository
from app.repositories.payment_transaction_repository import PaymentTransactionRepository
from app.repositories.student_repository import StudentRepository
from app.repositories.user_repository import UserRepository
from app.services.events.event_service import EventService


@pytest.mark.asyncio
async def test_create_reminder(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events = EventRepository(db_session)
    service = EventService(events)

    user = await users.get_or_create(telegram_id=1, language="ru")
    user.timezone = "UTC"

    cmd = CreateReminderCommand(
        intent=Intent.CREATE_REMINDER,
        title="Оплата",
        start_at="2026-03-01T10:00:00+00:00",
        remind_offsets=[15, 0],
    )

    message = await service.create_reminder(user, cmd)
    await db_session.commit()

    all_events = await events.list_for_user(user.id)
    assert len(all_events) == 1
    assert "создано" in message.lower()


@pytest.mark.asyncio
async def test_create_schedule(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events = EventRepository(db_session)
    service = EventService(events)

    user = await users.get_or_create(telegram_id=2, language="ru")
    user.timezone = "Europe/Moscow"

    cmd = CreateScheduleCommand(
        intent=Intent.CREATE_SCHEDULE,
        slots=[
            ScheduleSlotInput(weekday="MO", time="10:00", subject="Math"),
            ScheduleSlotInput(weekday="TU", time="11:00", subject="Physics"),
        ],
    )

    text = await service.create_schedule(user, cmd)
    await db_session.commit()

    all_events = await events.list_for_user(user.id)
    assert len(all_events) == 2
    assert "2" in text


@pytest.mark.asyncio
async def test_create_birthday(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events = EventRepository(db_session)
    service = EventService(events)

    user = await users.get_or_create(telegram_id=3, language="ru")
    user.timezone = "UTC"

    cmd = CreateBirthdayCommand(intent=Intent.CREATE_BIRTHDAY, person="Анна", date="14 мая")
    text = await service.create_birthday(user, cmd)
    await db_session.commit()

    all_events = await events.list_for_user(user.id)
    assert len(all_events) == 1
    assert all_events[0].rrule == "FREQ=YEARLY"
    assert "Анна" in text


@pytest.mark.asyncio
async def test_list_events_today(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=4, language="ru")
    user.timezone = "UTC"

    event = Event(
        user_id=user.id,
        event_type="reminder",
        title="Today event",
        starts_at=datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )
    await events_repo.create(event)
    await db_session.commit()

    text = await service.list_events(user, ListEventsCommand(intent=Intent.LIST_EVENTS, period="today"))

    assert "Today event" in text


@pytest.mark.asyncio
async def test_update_reminder(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=5, language="ru")
    user.timezone = "UTC"

    event = Event(
        user_id=user.id,
        event_type="reminder",
        title="Old title",
        starts_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )
    await events_repo.create(event)
    await db_session.commit()

    cmd = UpdateReminderCommand(intent=Intent.UPDATE_REMINDER, search_text="Old", title="New title")
    text = await service.update_reminder(user, cmd)
    await db_session.commit()

    found = await events_repo.find_by_title(user.id, "New")
    assert found is not None
    assert text == "Событие обновлено."


@pytest.mark.asyncio
async def test_delete_reminder(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=6, language="ru")
    user.timezone = "UTC"

    event = Event(
        user_id=user.id,
        event_type="reminder",
        title="Delete me",
        starts_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )
    await events_repo.create(event)
    await db_session.commit()

    cmd = DeleteReminderCommand(intent=Intent.DELETE_REMINDER, search_text="Delete")
    text = await service.delete_reminder(user, cmd)
    await db_session.commit()

    still_active = await events_repo.list_for_user(user.id, only_active=True)
    assert not still_active
    assert "удалено" in text


@pytest.mark.asyncio
async def test_list_events_filters_by_student_name(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=7, language="ru")
    user.timezone = "UTC"

    event_masha = Event(
        user_id=user.id,
        event_type="lesson",
        title="Маша",
        starts_at=datetime.now(tz=UTC) + timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(hours=3),
        rrule=None,
        remind_offsets=[15],
        extra_data={"student_name": "Маша"},
    )
    event_ivan = Event(
        user_id=user.id,
        event_type="lesson",
        title="Иван",
        starts_at=datetime.now(tz=UTC) + timedelta(hours=4),
        ends_at=datetime.now(tz=UTC) + timedelta(hours=5),
        rrule=None,
        remind_offsets=[15],
        extra_data={"student_name": "Иван"},
    )
    await events_repo.create(event_masha)
    await events_repo.create(event_ivan)
    await db_session.commit()

    text = await service.list_events(
        user,
        ListEventsCommand(intent=Intent.LIST_EVENTS, period="all", student_name="Маша"),
    )

    assert "Маша" in text
    assert "Иван" not in text


@pytest.mark.asyncio
async def test_tutor_day_report_contains_load_and_windows(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=8, language="ru")
    user.timezone = "UTC"

    base = datetime.now(tz=UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    await events_repo.create(
        Event(
            user_id=user.id,
            event_type="lesson",
            title="Маша",
            starts_at=base,
            ends_at=base + timedelta(minutes=60),
            rrule=None,
            remind_offsets=[15],
            extra_data={"student_name": "Маша"},
        )
    )
    await events_repo.create(
        Event(
            user_id=user.id,
            event_type="lesson",
            title="Иван",
            starts_at=base + timedelta(minutes=120),
            ends_at=base + timedelta(minutes=180),
            rrule=None,
            remind_offsets=[15],
            extra_data={"student_name": "Иван"},
        )
    )
    await db_session.commit()

    text = await service.tutor_day_report(user=user, day=base.date())
    assert "Нагрузка" in text
    assert "Свободные окна" in text


@pytest.mark.asyncio
async def test_update_schedule_reschedule_single_week_creates_override(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)

    user = await users.get_or_create(telegram_id=9, language="ru")
    user.timezone = "UTC"

    base = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)
    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Маша",
        starts_at=base,
        ends_at=base + timedelta(minutes=60),
        rrule="FREQ=WEEKLY;BYDAY=MO",
        remind_offsets=[15],
        extra_data={"weekday": "MO", "time": "17:00", "student_name": "Маша"},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    text = await service.update_schedule(
        user,
        UpdateScheduleCommand(
            intent=Intent.UPDATE_SCHEDULE,
            event_id=lesson.id,
            occurrence_date="2026-03-02 17:00",
            new_date="2026-03-04",
            new_time="19:00",
            apply_scope="single_week",
        ),
    )
    await db_session.commit()

    assert "только для этой недели" in text
    all_events = await events_repo.list_for_user(user.id, only_active=True)
    assert len(all_events) == 2


@pytest.mark.asyncio
async def test_mark_lesson_paid_with_subscription_warns_last_lesson(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)

    user = await users.get_or_create(telegram_id=10, language="ru")
    user.timezone = "UTC"

    student = await students_repo.get_or_create_by_name(user.id, "Маша")
    student.subscription_total_lessons = 8
    student.subscription_remaining_lessons = 2
    await students_repo.update(student)

    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Маша",
        starts_at=datetime(2026, 3, 2, 17, 0, tzinfo=UTC),
        ends_at=datetime(2026, 3, 2, 18, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[15],
        extra_data={"student_name": "Маша", "student_id": str(student.id)},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    text = await service.mark_lesson_paid(
        user=user,
        event_id=lesson.id,
        amount=3000,
    )
    await db_session.commit()
    assert "осталось 1 занятие" in text


@pytest.mark.asyncio
async def test_cancel_lesson_counts_tutor_cancellation(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)

    user = await users.get_or_create(telegram_id=11, language="ru")
    user.timezone = "UTC"
    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Иван",
        starts_at=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        ends_at=datetime(2026, 3, 3, 11, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[15],
        extra_data={},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    text = await service.cancel_lesson(user=user, event_id=lesson.id, canceled_by="tutor")
    await db_session.commit()
    assert "отменен" in text.lower()


@pytest.mark.asyncio
async def test_finance_report_contains_period_summary(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=12, language="ru")
    user.timezone = "UTC"
    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Катя",
        starts_at=datetime.now(tz=UTC) - timedelta(days=1),
        ends_at=datetime.now(tz=UTC),
        rrule=None,
        remind_offsets=[15],
        extra_data={"payment_status": "paid", "payment_amount": 2500, "payment_paid_at": datetime.now(tz=UTC).isoformat()},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    text = await service.tutor_finance_report(user=user, period_days=7)
    assert "Финансы" in text
    assert "Оплачено" in text


@pytest.mark.asyncio
async def test_suggest_reschedule_slots_returns_candidates(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    service = EventService(events_repo)
    user = await users.get_or_create(telegram_id=13, language="ru")
    user.timezone = "UTC"

    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Миша",
        starts_at=datetime.now(tz=UTC) + timedelta(hours=1),
        ends_at=datetime.now(tz=UTC) + timedelta(hours=2),
        rrule=None,
        remind_offsets=[15],
        extra_data={},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    slots = await service.suggest_reschedule_slots(user=user, event=lesson, limit=3)
    assert len(slots) > 0


@pytest.mark.asyncio
async def test_set_initial_prepaid_balance_without_lesson_event(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=14, language="ru")
    user.timezone = "UTC"

    text = await service.mark_lesson_paid(
        user=user,
        event_id=None,
        search_text="Оля",
        prepaid_lessons_set=6,
        payment_total=18000,
    )
    await db_session.commit()

    student = await students_repo.find_by_name(user.id, "Оля")
    assert student is not None
    assert student.subscription_remaining_lessons == 6
    assert "осталось 6" in text


@pytest.mark.asyncio
async def test_add_prepaid_balance_without_lesson_event(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=15, language="ru")
    user.timezone = "UTC"

    student = await students_repo.get_or_create_by_name(user.id, "Петя")
    student.subscription_remaining_lessons = 3
    await students_repo.update(student)
    await db_session.commit()

    text = await service.mark_lesson_paid(
        user=user,
        event_id=None,
        search_text="Петя",
        prepaid_lessons_add=5,
    )
    await db_session.commit()

    updated = await students_repo.find_by_name(user.id, "Петя")
    assert updated is not None
    assert updated.subscription_remaining_lessons == 8
    assert "осталось 8" in text


@pytest.mark.asyncio
async def test_payment_total_auto_converts_to_lessons_by_default_price(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=16, language="ru")
    user.timezone = "UTC"

    student = await students_repo.get_or_create_by_name(user.id, "Маша")
    student.default_lesson_price = 2500
    student.subscription_remaining_lessons = 1
    await students_repo.update(student)
    await db_session.commit()

    text = await service.mark_lesson_paid(
        user=user,
        event_id=None,
        search_text="Маша",
        payment_total=10000,
    )
    await db_session.commit()

    updated = await students_repo.find_by_name(user.id, "Маша")
    assert updated is not None
    assert updated.subscription_remaining_lessons == 5
    assert "осталось 5" in text


@pytest.mark.asyncio
async def test_payment_total_requests_clarification_when_lesson_price_unknown(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=17, language="ru")
    user.timezone = "UTC"

    await students_repo.get_or_create_by_name(user.id, "Иван")
    await db_session.commit()

    text = await service.mark_lesson_paid(
        user=user,
        event_id=None,
        search_text="Иван",
        payment_total=10000,
    )

    assert "не знаю цену одного занятия" in text


@pytest.mark.asyncio
async def test_update_student_lesson_price(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=18, language="ru")
    user.timezone = "UTC"

    text = await service.update_student(
        user=user,
        cmd=UpdateStudentCommand(
            intent=Intent.UPDATE_STUDENT,
            student_name="Лена",
            lesson_price=2700,
        ),
    )
    await db_session.commit()

    student = await students_repo.find_by_name(user.id, "Лена")
    assert student is not None
    assert student.default_lesson_price == 2700
    assert "обновлена" in text


@pytest.mark.asyncio
async def test_mark_lesson_paid_creates_ledger_transaction(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    payment_repo = PaymentTransactionRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo, payment_repository=payment_repo)
    user = await users.get_or_create(telegram_id=19, language="ru")
    user.timezone = "UTC"
    student = await students_repo.get_or_create_by_name(user.id, "Рома")
    student.subscription_remaining_lessons = 3
    await students_repo.update(student)
    lesson = Event(
        user_id=user.id,
        event_type="lesson",
        title="Рома",
        starts_at=datetime.now(tz=UTC),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=60),
        rrule=None,
        remind_offsets=[15],
        extra_data={"student_name": "Рома", "student_id": str(student.id)},
    )
    await events_repo.create(lesson)
    await db_session.commit()

    _ = await service.mark_lesson_paid(user=user, event_id=lesson.id, amount=3000)
    await db_session.commit()
    items = await payment_repo.list_for_user(user.id)
    assert len(items) >= 1
    assert items[0].amount >= 3000


@pytest.mark.asyncio
async def test_student_card_contains_main_fields(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=20, language="ru")
    user.timezone = "UTC"
    student = await students_repo.get_or_create_by_name(user.id, "Алина")
    student.default_lesson_price = 2200
    student.subscription_remaining_lessons = 4
    await students_repo.update(student)
    await db_session.commit()

    text = await service.student_card(
        user=user,
        cmd=StudentCardCommand(intent=Intent.STUDENT_CARD, student_name="Алина", view="card"),
    )
    assert "Карточка ученика" in text
    assert "Предоплачено занятий" in text


@pytest.mark.asyncio
async def test_parse_bank_transfer_requires_student_and_amount(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=21, language="ru")
    user.timezone = "UTC"

    text, student_name, amount = await service.parse_bank_transfer(
        user=user,
        cmd=ParseBankTransferCommand(intent=Intent.PARSE_BANK_TRANSFER, raw_text="Перевод", student_name=None, amount=1000),
    )
    assert "Уточните, от какого ученика" in text
    assert student_name is None
    assert amount is None


@pytest.mark.asyncio
async def test_create_and_delete_student(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)
    events_repo = EventRepository(db_session)
    students_repo = StudentRepository(db_session)
    service = EventService(events_repo, student_repository=students_repo)
    user = await users.get_or_create(telegram_id=22, language="ru")
    user.timezone = "UTC"

    create_text = await service.create_student(
        user=user,
        cmd=CreateStudentCommand(intent=Intent.CREATE_STUDENT, student_name="Дима", lesson_price=2000),
    )
    await db_session.commit()
    assert "добавлен" in create_text

    delete_text = await service.delete_student(
        user=user,
        cmd=DeleteStudentCommand(intent=Intent.DELETE_STUDENT, student_name="Дима", delete_future_lessons=False),
    )
    await db_session.commit()
    assert "удален" in delete_text
