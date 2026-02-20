from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.datetime_utils import (
    end_of_local_day,
    next_weekday_time,
    parse_date_input,
    parse_datetime_input,
    start_of_local_day,
)
from app.db.models import Event, Note, PaymentTransaction, Student, User
from app.domain.commands import (
    CreateBirthdayCommand,
    CreateNoteCommand,
    CreateReminderCommand,
    CreateScheduleCommand,
    CreateStudentCommand,
    DeleteNoteCommand,
    DeleteReminderCommand,
    DeleteStudentCommand,
    ListEventsCommand,
    ListNotesCommand,
    ParseBankTransferCommand,
    ScheduleSlotInput,
    StudentCardCommand,
    UpdateNoteCommand,
    UpdateReminderCommand,
    UpdateScheduleCommand,
    UpdateStudentCommand,
)
from app.domain.enums import EventType
from app.repositories.event_repository import EventRepository
from app.repositories.note_repository import NoteRepository
from app.repositories.payment_transaction_repository import PaymentTransactionRepository
from app.repositories.student_repository import StudentRepository
from app.services.due_index_service import DueIndexService
from app.services.occurrence_service import event_next_occurrence, event_occurrences_between
from app.services.smart_agents import ConflictDetectionAgent, ScheduleOptimizationAgent


class EventService:
    def __init__(
        self,
        event_repository: EventRepository,
        due_index_service: DueIndexService | None = None,
        note_repository: NoteRepository | None = None,
        student_repository: StudentRepository | None = None,
        payment_repository: PaymentTransactionRepository | None = None,
        conflict_detection_agent: ConflictDetectionAgent | None = None,
        schedule_optimization_agent: ScheduleOptimizationAgent | None = None,
    ) -> None:
        self._events = event_repository
        self._due_index = due_index_service
        self._notes = note_repository
        self._students = student_repository
        self._payments = payment_repository
        self._conflicts = conflict_detection_agent or ConflictDetectionAgent()
        self._optimizer = schedule_optimization_agent or ScheduleOptimizationAgent()

    async def create_reminder(self, user: User, cmd: CreateReminderCommand) -> str:
        if cmd.timezone:
            self._validate_timezone(cmd.timezone)
            user.timezone = cmd.timezone

        if not cmd.start_at:
            return "Уточните дату и время напоминания."

        parsed = parse_datetime_input(cmd.start_at, user.timezone, languages=[user.language, "ru", "en"])
        if parsed is None:
            return "Не удалось распознать дату/время. Попробуйте, например: завтра в 10:30."

        event = Event(
            user_id=user.id,
            event_type=EventType.REMINDER.value,
            title=cmd.title,
            description=cmd.description,
            starts_at=parsed,
            rrule=cmd.rrule,
            remind_offsets=cmd.remind_offsets,
            extra_data={},
        )
        await self._events.create(event)
        await self._sync_due_index(event)
        local_time = parsed.astimezone(ZoneInfo(user.timezone)).strftime("%d.%m.%Y %H:%M")
        return f"Напоминание создано: {cmd.title} ({local_time}, {user.timezone})."

    async def update_reminder(self, user: User, cmd: UpdateReminderCommand) -> str:
        event = await self._resolve_event(
            user_id=user.id,
            event_id=cmd.event_id,
            search_text=cmd.search_text,
            allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
        )
        if event is None:
            return "Событие для изменения не найдено."

        if cmd.title:
            event.title = cmd.title

        if cmd.start_at:
            parsed = parse_datetime_input(cmd.start_at, user.timezone, languages=[user.language, "ru", "en"])
            if parsed is None:
                return "Не удалось распознать новую дату/время."
            event.starts_at = parsed

        if cmd.rrule is not None:
            event.rrule = cmd.rrule

        if cmd.remind_offsets is not None:
            event.remind_offsets = sorted(set(cmd.remind_offsets), reverse=True)

        await self._events.update(event)
        await self._sync_due_index(event)
        return "Событие обновлено."

    async def delete_reminder(self, user: User, cmd: DeleteReminderCommand) -> str:
        event = await self._resolve_event(
            user_id=user.id,
            event_id=cmd.event_id,
            search_text=cmd.search_text,
            allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
        )
        if event is None:
            return "Событие для удаления не найдено."

        await self._events.soft_delete(event)
        await self._sync_due_index(event)
        return f"Событие удалено: {event.title}."

    async def create_schedule(self, user: User, cmd: CreateScheduleCommand) -> str:
        slots = cmd.slots
        if not slots and cmd.template:
            slots = self._template_slots(cmd.template)

        if not slots:
            return "Слоты расписания пустые."

        existing_lessons = await self._events.list_active_lessons_for_user(user.id)
        created = 0
        skipped_conflicts: list[str] = []
        for slot in slots:
            starts_at = next_weekday_time(slot.weekday, slot.time, user.timezone)
            ends_at = starts_at + timedelta(minutes=slot.duration_minutes)
            if self._has_lesson_conflict(existing_lessons, starts_at, ends_at, user.min_buffer_minutes):
                skipped_conflicts.append(f"{slot.weekday} {slot.time} {slot.subject}")
                continue
            student_name = (slot.student_name or slot.subject or "Ученик").strip()
            student_id: str | None = None
            if self._students is not None:
                student = await self._students.get_or_create_by_name(user.id, student_name)
                student_id = str(student.id)
            event = Event(
                user_id=user.id,
                event_type=EventType.LESSON.value,
                title=student_name,
                description=slot.location,
                starts_at=starts_at,
                ends_at=ends_at,
                rrule=f"FREQ=WEEKLY;BYDAY={slot.weekday}",
                remind_offsets=slot.remind_offsets,
                extra_data={
                    "weekday": slot.weekday,
                    "time": slot.time,
                    "duration_minutes": slot.duration_minutes,
                    "location": slot.location,
                    "student_name": student_name,
                    "student_id": student_id,
                },
            )
            await self._events.create(event)
            await self._sync_due_index(event)
            existing_lessons.append(event)
            created += 1

        if skipped_conflicts:
            return (
                f"Расписание создано: {created} урок(ов). "
                f"Пропущено из-за конфликтов: {', '.join(skipped_conflicts)}."
            )
        return f"Расписание создано: {created} урок(ов)."

    async def update_schedule(self, user: User, cmd: UpdateScheduleCommand) -> str:
        if cmd.apply_to_all and cmd.shift_weekday and cmd.shift_minutes:
            lessons = await self._events.list_active_lessons_for_user(user.id)
            updated = 0
            for lesson in lessons:
                weekday = str(lesson.extra_data.get("weekday", ""))
                if weekday != cmd.shift_weekday:
                    continue
                lesson.starts_at = lesson.starts_at + timedelta(minutes=cmd.shift_minutes)
                if lesson.ends_at is not None:
                    lesson.ends_at = lesson.ends_at + timedelta(minutes=cmd.shift_minutes)
                hhmm = lesson.starts_at.astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
                lesson.extra_data["time"] = hhmm
                await self._events.update(lesson)
                await self._sync_due_index(lesson)
                updated += 1
            return f"Сдвинуто уроков: {updated}."

        if cmd.bulk_cancel_weekday and cmd.bulk_cancel_scope:
            return await self._bulk_cancel_lessons(user=user, cmd=cmd)

        event = await self._resolve_event(
            user_id=user.id,
            event_id=cmd.event_id,
            search_text=cmd.search_text,
            allowed_types={EventType.LESSON.value},
        )
        if event is None:
            return "Урок для изменения не найден."

        if cmd.apply_scope == "single_week" and (cmd.new_date or cmd.new_time):
            return await self._reschedule_single_week(user=user, event=event, cmd=cmd)

        if cmd.delete:
            await self._events.soft_delete(event)
            await self._sync_due_index(event)
            return f"Урок удален: {event.title}."

        if cmd.title:
            event.title = cmd.title

        duration = cmd.duration_minutes or int(event.extra_data.get("duration_minutes", 60))

        if cmd.weekday or cmd.time:
            weekday = cmd.weekday or str(event.extra_data.get("weekday", "MO"))
            hhmm = cmd.time or str(event.extra_data.get("time", "09:00"))
            starts_at = next_weekday_time(weekday, hhmm, user.timezone)
            ends_at = starts_at + timedelta(minutes=duration)
            existing_lessons = [
                item
                for item in await self._events.list_active_lessons_for_user(user.id)
                if item.id != event.id
            ]
            if self._has_lesson_conflict(existing_lessons, starts_at, ends_at, user.min_buffer_minutes):
                return "Конфликт расписания. Выберите другое время."
            event.starts_at = starts_at
            event.ends_at = ends_at
            event.rrule = f"FREQ=WEEKLY;BYDAY={weekday}"
            event.extra_data["weekday"] = weekday
            event.extra_data["time"] = hhmm

        if cmd.duration_minutes is not None:
            event.extra_data["duration_minutes"] = cmd.duration_minutes
            event.ends_at = event.starts_at + timedelta(minutes=cmd.duration_minutes)

        if cmd.remind_offsets is not None:
            event.remind_offsets = sorted(set(cmd.remind_offsets), reverse=True)

        await self._events.update(event)
        await self._sync_due_index(event)
        return "Урок обновлен."

    async def _reschedule_single_week(self, user: User, event: Event, cmd: UpdateScheduleCommand) -> str:
        tz = ZoneInfo(user.timezone)
        now_utc = datetime.now(tz=UTC)

        source_occurrence: datetime | None = None
        if cmd.occurrence_date:
            source_occurrence = parse_datetime_input(
                cmd.occurrence_date,
                user.timezone,
                languages=[user.language, "ru", "en"],
            )
        if source_occurrence is None:
            source_occurrence = event_next_occurrence(event, now_utc)
        if source_occurrence is None:
            return "Не удалось определить переносимый урок."

        source_local = source_occurrence.astimezone(tz)
        target_text: str
        if cmd.new_date and cmd.new_time:
            target_text = f"{cmd.new_date} {cmd.new_time}"
        elif cmd.new_date:
            target_text = f"{cmd.new_date} {source_local.strftime('%H:%M')}"
        elif cmd.new_time:
            target_text = f"{source_local.strftime('%d.%m.%Y')} {cmd.new_time}"
        else:
            return "Уточните новую дату или время переноса."

        new_start = parse_datetime_input(target_text, user.timezone, languages=[user.language, "ru", "en"])
        if new_start is None:
            return "Не удалось распознать новую дату/время переноса."
        duration = (event.ends_at - event.starts_at) if event.ends_at else timedelta(minutes=60)
        new_end = new_start + duration

        excluded = list(event.extra_data.get("excluded_occurrences", []))
        source_iso = source_occurrence.astimezone(UTC).isoformat()
        if source_iso not in excluded:
            excluded.append(source_iso)
        event.extra_data["excluded_occurrences"] = excluded
        await self._events.update(event)
        await self._sync_due_index(event)

        moved_event = Event(
            user_id=user.id,
            event_type=EventType.LESSON.value,
            title=event.title,
            description=event.description,
            starts_at=new_start,
            ends_at=new_end,
            rrule=None,
            remind_offsets=event.remind_offsets,
            extra_data={
                **event.extra_data,
                "moved_from_event_id": str(event.id),
                "moved_from_occurrence": source_iso,
                "is_reschedule_override": True,
            },
        )
        await self._events.create(moved_event)
        await self._sync_due_index(moved_event)
        local_new = new_start.astimezone(tz).strftime("%d.%m %H:%M")
        return f"Урок перенесен на {local_new} (только для этой недели)."

    async def _bulk_cancel_lessons(self, user: User, cmd: UpdateScheduleCommand) -> str:
        weekday = cmd.bulk_cancel_weekday
        scope = cmd.bulk_cancel_scope
        if weekday is None or scope is None:
            return "Не удалось применить массовую отмену."
        lessons = await self._events.list_active_lessons_for_user(user.id)
        affected = 0
        if scope == "all_future":
            for lesson in lessons:
                if str(lesson.extra_data.get("weekday", "")) != weekday:
                    continue
                await self._events.soft_delete(lesson)
                await self._sync_due_index(lesson)
                affected += 1
            return f"Отменено будущих серий уроков: {affected}."

        tz = ZoneInfo(user.timezone)
        local_now = datetime.now(tz=UTC).astimezone(tz)
        start_next_week = (local_now + timedelta(days=(8 - local_now.isoweekday()))).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        for lesson in lessons:
            if str(lesson.extra_data.get("weekday", "")) != weekday:
                continue
            occ = event_next_occurrence(lesson, start_next_week.astimezone(UTC) - timedelta(minutes=1))
            if occ is None:
                continue
            local_occ = occ.astimezone(tz)
            if not (start_next_week <= local_occ < start_next_week + timedelta(days=7)):
                continue
            excluded = list(lesson.extra_data.get("excluded_occurrences", []))
            occ_iso = occ.astimezone(UTC).isoformat()
            if occ_iso not in excluded:
                excluded.append(occ_iso)
                lesson.extra_data["excluded_occurrences"] = excluded
                await self._events.update(lesson)
                await self._sync_due_index(lesson)
                affected += 1
        return f"Отменено уроков на следующей неделе: {affected}."

    async def create_birthday(self, user: User, cmd: CreateBirthdayCommand) -> str:
        parsed = parse_date_input(cmd.date, user.timezone, languages=[user.language, "ru", "en"])
        if parsed is None:
            return "Не удалось распознать дату дня рождения."

        tz = ZoneInfo(user.timezone)
        local = parsed.astimezone(tz).replace(hour=9, minute=0, second=0, microsecond=0)
        starts_at = local.astimezone(UTC)

        event = Event(
            user_id=user.id,
            event_type=EventType.BIRTHDAY.value,
            title=f"День рождения: {cmd.person}",
            description=None,
            starts_at=starts_at,
            ends_at=None,
            rrule="FREQ=YEARLY",
            remind_offsets=cmd.remind_offsets,
            extra_data={"person": cmd.person},
        )
        await self._events.create(event)
        await self._sync_due_index(event)
        return f"День рождения добавлен: {cmd.person}."

    async def create_note(self, user: User, cmd: CreateNoteCommand) -> str:
        if self._notes is None:
            return "Сервис заметок недоступен."
        from app.db.models import Note

        note = Note(
            user_id=user.id,
            linked_event_id=cmd.linked_event_id,
            title=cmd.title,
            content=cmd.content,
            tags=cmd.tags,
        )
        await self._notes.create(note)
        return f"Заметка создана: {cmd.title}."

    async def update_note(self, user: User, cmd: UpdateNoteCommand) -> str:
        if self._notes is None:
            return "Сервис заметок недоступен."

        note = await self._resolve_note(user_id=user.id, note_id=cmd.note_id, search_text=cmd.search_text)
        if note is None:
            return "Заметка не найдена."

        if cmd.title is not None:
            note.title = cmd.title
        if cmd.content is not None:
            note.content = cmd.content
        if cmd.tags is not None:
            note.tags = cmd.tags
        if cmd.linked_event_id is not None:
            note.linked_event_id = cmd.linked_event_id

        await self._notes.update(note)
        return "Заметка обновлена."

    async def delete_note(self, user: User, cmd: DeleteNoteCommand) -> str:
        if self._notes is None:
            return "Сервис заметок недоступен."
        note = await self._resolve_note(user_id=user.id, note_id=cmd.note_id, search_text=cmd.search_text)
        if note is None:
            return "Заметка не найдена."
        await self._notes.soft_delete(note)
        return f"Заметка удалена: {note.title}."

    async def list_notes(self, user: User, cmd: ListNotesCommand) -> str:
        if self._notes is None:
            return "Сервис заметок недоступен."
        notes = await self._notes.list_for_user(user_id=user.id, search_text=cmd.search_text)
        if not notes:
            return "Заметок пока нет."
        lines = ["Ваши заметки:"]
        for note in notes[:20]:
            tags = f" [#{', #'.join(note.tags)}]" if note.tags else ""
            lines.append(f"- {note.title}{tags}")
        return "\n".join(lines)

    async def list_events(self, user: User, cmd: ListEventsCommand) -> str:
        events = await self._events.list_for_user(user.id, only_active=True)
        if not events:
            return "Событий пока нет."

        now = datetime.now(tz=UTC)
        tz = ZoneInfo(user.timezone)

        if cmd.period == "all":
            lines = ["Ближайшие события:"]
            for event in events:
                if cmd.student_name:
                    student_name = str(event.extra_data.get("student_name", event.title))
                    if cmd.student_name.lower() not in student_name.lower():
                        continue
                next_occurrence = event_next_occurrence(event, now)
                if next_occurrence is None:
                    continue
                local = next_occurrence.astimezone(tz).strftime("%d.%m %H:%M")
                if event.event_type == EventType.LESSON.value:
                    student_name = str(event.extra_data.get("student_name", event.title))
                    lines.append(f"- {local} • {student_name}")
                else:
                    lines.append(f"- [{event.event_type}] {local} {event.title}")
            return "\n".join(lines) if len(lines) > 1 else "Активных будущих событий нет."

        if cmd.period == "today":
            start_utc = start_of_local_day(now, user.timezone)
            end_utc = end_of_local_day(now, user.timezone)
        elif cmd.period == "tomorrow":
            tomorrow = now + timedelta(days=1)
            start_utc = start_of_local_day(tomorrow, user.timezone)
            end_utc = end_of_local_day(tomorrow, user.timezone)
        elif cmd.period == "week":
            local_now = now.astimezone(tz)
            start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = start_local + timedelta(days=7)
            start_utc = start_local.astimezone(UTC)
            end_utc = end_local.astimezone(UTC)
        else:
            if not cmd.date:
                return "Для периода date нужно передать поле date."
            parsed_date = parse_date_input(cmd.date, user.timezone, languages=[user.language, "ru", "en"])
            if parsed_date is None:
                return "Не удалось распознать дату."
            start_utc = start_of_local_day(parsed_date, user.timezone)
            end_utc = start_utc + timedelta(days=1)

        occurrences: list[tuple[datetime, Event]] = []
        for event in events:
            if cmd.student_name:
                student_name = str(event.extra_data.get("student_name", event.title))
                if cmd.student_name.lower() not in student_name.lower():
                    continue
            for occ in event_occurrences_between(event, start_utc, end_utc):
                occurrences.append((occ, event))

        if not occurrences:
            return "На выбранный период событий нет."

        occurrences.sort(key=lambda item: item[0])
        period_title = {
            "today": "Расписание на сегодня:",
            "tomorrow": "Расписание на завтра:",
            "week": "Расписание на неделю:",
            "date": f"Расписание на {cmd.date or 'дату'}:",
            "all": "Ближайшие события:",
        }.get(cmd.period, "Ваши события:")
        lines = [period_title]
        for occ, event in occurrences:
            local = occ.astimezone(tz).strftime("%d.%m %H:%M")
            if event.event_type == EventType.LESSON.value:
                student_name = str(event.extra_data.get("student_name", event.title))
                lines.append(f"- {local} • {student_name}")
            else:
                lines.append(f"- [{event.event_type}] {local} {event.title}")
        return "\n".join(lines)

    async def lessons_for_day(self, user: User, day: date) -> list[tuple[datetime, Event]]:
        lessons = await self._events.list_active_lessons_for_user(user.id)
        tz = ZoneInfo(user.timezone)
        local_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        local_end = local_start + timedelta(days=1)
        start_utc = local_start.astimezone(UTC)
        end_utc = local_end.astimezone(UTC)

        result: list[tuple[datetime, Event]] = []
        for lesson in lessons:
            for occ in event_occurrences_between(lesson, start_utc, end_utc):
                result.append((occ, lesson))
        result.sort(key=lambda x: x[0])
        return result

    async def tutor_day_report(self, user: User, day: date) -> str:
        lessons = await self.lessons_for_day(user=user, day=day)
        if not lessons:
            return "На этот день уроков нет."

        tz = ZoneInfo(user.timezone)
        lines = [f"Расписание на {day.strftime('%d.%m.%Y')}:"]
        total_minutes = 0
        windows: list[int] = []
        prev_end: datetime | None = None

        for occ, lesson in lessons:
            local_start = occ.astimezone(tz)
            local_end = (lesson.ends_at or (occ + timedelta(minutes=60))).astimezone(tz)
            student_name = str(lesson.extra_data.get("student_name", lesson.title))
            lines.append(f"- {local_start.strftime('%H:%M')} - {local_end.strftime('%H:%M')} • {student_name}")
            total_minutes += int((local_end - local_start).total_seconds() // 60)
            if prev_end is not None:
                gap = int((local_start - prev_end).total_seconds() // 60)
                if gap > 0:
                    windows.append(gap)
            prev_end = local_end

        lines.append(f"Нагрузка: {total_minutes // 60}ч {total_minutes % 60}м.")
        if windows:
            lines.append(f"Свободные окна: {', '.join(f'{gap}м' for gap in windows[:5])}.")
        else:
            lines.append("Свободных окон между уроками нет.")
        min_gap = min(windows) if windows else None
        if min_gap is not None and min_gap < user.min_buffer_minutes:
            lines.append(
                f"Внимание: есть короткие окна меньше буфера {user.min_buffer_minutes}м."
            )
        if total_minutes >= 360:
            lines.append("Внимание: высокая загрузка дня.")
        return "\n".join(lines)

    async def tutor_finance_report(self, user: User, period_days: int) -> str:
        from_utc = datetime.now(tz=UTC) - timedelta(days=period_days)
        paid_sum = 0
        if self._payments is not None:
            payments = await self._payments.list_for_user(user_id=user.id, from_utc=from_utc, to_utc=None, limit=1000)
            paid_sum = sum(item.amount for item in payments if item.amount > 0)

        lessons = await self._events.list_active_lessons_for_user(user.id)
        pending_count = 0
        debts: dict[str, int] = {}
        for lesson in lessons:
            paid_at_raw = lesson.extra_data.get("payment_paid_at")
            student_name = str(lesson.extra_data.get("student_name", lesson.title))
            payment_status = str(lesson.extra_data.get("payment_status", "unknown"))
            paid_at: datetime | None = None
            if isinstance(paid_at_raw, str):
                try:
                    paid_at = datetime.fromisoformat(paid_at_raw)
                except ValueError:
                    paid_at = None
            if paid_at is None and lesson.starts_at < datetime.now(tz=UTC) and payment_status != "paid":
                pending_count += 1
                debts[student_name] = debts.get(student_name, 0) + 1

        lines = [f"Финансы за {period_days} дн.:"]
        lines.append(f"- Оплачено: {paid_sum}")
        lines.append(f"- Ожидают оплаты уроков: {pending_count}")
        if debts:
            lines.append("- Долги по ученикам:")
            for name, count in sorted(debts.items(), key=lambda item: item[1], reverse=True)[:10]:
                lines.append(f"  {name}: {count}")
        return "\n".join(lines)

    async def tutor_attendance_log(self, user: User, period_days: int) -> str:
        if self._students is None:
            return "Журнал посещаемости недоступен."
        students = await self._students.list_for_user(user.id)
        if not students:
            return "Учеников пока нет."
        lines = [f"Журнал отмен/пропусков за {period_days} дн.:"]
        for student in students:
            if (
                student.missed_lessons_count <= 0
                and student.canceled_by_student_count <= 0
                and student.canceled_by_tutor_count <= 0
            ):
                continue
            lines.append(
                f"- {student.name}: пропуски {student.missed_lessons_count}, "
                f"отмены учеником {student.canceled_by_student_count}, "
                f"отмены репетитором {student.canceled_by_tutor_count}"
            )
        if len(lines) == 1:
            return "За период отмен и пропусков не отмечено."
        return "\n".join(lines)

    async def operational_digest(self, user: User, now_utc: datetime) -> str:
        tz = ZoneInfo(user.timezone)
        day = now_utc.astimezone(tz).date()
        lessons = await self.lessons_for_day(user=user, day=day)
        total_lessons = len(lessons)
        unpaid = 0
        for _occ, lesson in lessons:
            if str(lesson.extra_data.get("payment_status", "unknown")) != "paid":
                unpaid += 1
        low_balance: list[str] = []
        if self._students is not None:
            students = await self._students.list_for_user(user.id)
            for student in students:
                remaining = student.subscription_remaining_lessons
                if remaining is not None and remaining <= 2:
                    low_balance.append(f"{student.name} ({remaining})")
        load_minutes = sum(
            int(((lesson.ends_at or (occ + timedelta(minutes=60))) - occ).total_seconds() // 60)
            for occ, lesson in lessons
        )
        lines = ["Операционный дайджест:"]
        lines.append(f"- Сегодня уроков: {total_lessons}")
        lines.append(f"- Неотмеченных оплат: {unpaid}")
        lines.append(f"- Нагрузка: {load_minutes // 60}ч {load_minutes % 60}м")
        if low_balance:
            lines.append(f"- На продление: {', '.join(low_balance[:8])}")
        return "\n".join(lines)

    async def tutor_missed_report(self, user: User) -> str:
        if self._students is None:
            return "Отчет недоступен."
        students = await self._students.list_for_user(user.id)
        missed = [s for s in students if s.missed_lessons_count > 0]
        if not missed:
            return "Пропусков не отмечено."
        lines = ["Пропуски по ученикам:"]
        for student in missed:
            lines.append(f"- {student.name}: {student.missed_lessons_count}")
        return "\n".join(lines)

    async def serialize_user_events(self, user_id: int) -> list[dict[str, object]]:
        events = await self._events.list_for_user(user_id, only_active=False)
        return [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "title": event.title,
                "description": event.description,
                "starts_at": event.starts_at.isoformat(),
                "ends_at": event.ends_at.isoformat() if event.ends_at else None,
                "rrule": event.rrule,
                "remind_offsets": event.remind_offsets,
                "extra_data": event.extra_data,
                "is_active": event.is_active,
            }
            for event in events
        ]

    async def serialize_user_notes(self, user_id: int) -> list[dict[str, object]]:
        if self._notes is None:
            return []
        notes = await self._notes.list_for_user(user_id=user_id, search_text=None)
        return [
            {
                "id": str(note.id),
                "linked_event_id": str(note.linked_event_id) if note.linked_event_id else None,
                "title": note.title,
                "content": note.content,
                "tags": note.tags,
                "is_active": note.is_active,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat(),
            }
            for note in notes
        ]

    async def serialize_user_students(self, user_id: int) -> list[dict[str, object]]:
        if self._students is None:
            return []
        students = await self._students.list_for_user(user_id=user_id)
        return [
            {
                "id": str(student.id),
                "name": student.name,
                "phone": student.phone,
                "comment": student.comment,
                "payment_status": student.payment_status,
                "total_paid_amount": student.total_paid_amount,
                "missed_lessons_count": student.missed_lessons_count,
                "canceled_by_tutor_count": student.canceled_by_tutor_count,
                "canceled_by_student_count": student.canceled_by_student_count,
                "subscription_total_lessons": student.subscription_total_lessons,
                "subscription_remaining_lessons": student.subscription_remaining_lessons,
                "subscription_price": student.subscription_price,
                "default_lesson_price": student.default_lesson_price,
                "status": student.status,
                "goal": student.goal,
                "level": student.level,
                "weekly_frequency": student.weekly_frequency,
                "preferred_slots": student.preferred_slots,
                "is_active": student.is_active,
                "created_at": student.created_at.isoformat(),
                "updated_at": student.updated_at.isoformat(),
            }
            for student in students
        ]

    async def serialize_user_payments(self, user_id: int) -> list[dict[str, object]]:
        if self._payments is None:
            return []
        items = await self._payments.list_for_user(user_id=user_id, limit=500)
        return [
            {
                "id": str(item.id),
                "student_id": str(item.student_id) if item.student_id else None,
                "event_id": str(item.event_id) if item.event_id else None,
                "amount": item.amount,
                "prepaid_lessons_delta": item.prepaid_lessons_delta,
                "source": item.source,
                "note": item.note,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]

    async def find_candidates(
        self,
        user_id: int,
        search_text: str,
        allowed_types: set[str],
        limit: int = 5,
    ) -> list[Event]:
        candidates = await self._events.find_many_by_title(user_id=user_id, search_text=search_text, limit=limit)
        return [item for item in candidates if item.event_type in allowed_types and item.is_active]

    async def get_target_event(
        self,
        user_id: int,
        event_id: object | None,
        search_text: str | None,
        allowed_types: set[str],
    ) -> Event | None:
        return await self._resolve_event(
            user_id=user_id,
            event_id=event_id,
            search_text=search_text,
            allowed_types=allowed_types,
        )

    async def snooze_event(self, user: User, event_id: UUID, minutes: int) -> str:
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or not event.is_active:
            return "Событие для snooze не найдено."
        snoozed_at = datetime.now(tz=UTC) + timedelta(minutes=minutes)
        snooze_event = Event(
            user_id=user.id,
            event_type=EventType.REMINDER.value,
            title=f"Snooze: {event.title}",
            description=f"Автосоздано из события {event.id}",
            starts_at=snoozed_at,
            ends_at=None,
            rrule=None,
            remind_offsets=[0],
            extra_data={"source_event_id": str(event.id)},
        )
        await self._events.create(snooze_event)
        await self._sync_due_index(snooze_event)
        return f"Ок, напомню через {minutes} минут."

    async def cancel_lesson(self, user: User, event_id: UUID, canceled_by: str = "tutor") -> str:
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or event.event_type != EventType.LESSON.value:
            return "Урок не найден."
        actor = canceled_by if canceled_by in {"tutor", "student"} else "tutor"
        event.extra_data["canceled_by"] = actor
        event.extra_data["canceled_at"] = datetime.now(tz=UTC).isoformat()
        student_id_raw = event.extra_data.get("student_id")
        if self._students is not None and isinstance(student_id_raw, str):
            try:
                student = await self._students.get_for_user_by_id(user.id, UUID(student_id_raw))
            except ValueError:
                student = None
            if student is not None:
                if actor == "student":
                    student.canceled_by_student_count += 1
                else:
                    student.canceled_by_tutor_count += 1
                await self._students.update(student)
        await self._events.soft_delete(event)
        await self._sync_due_index(event)
        return f"Урок отменен: {event.title}."

    async def shift_lesson(self, user: User, event_id: UUID, shift_minutes: int) -> str:
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or event.event_type != EventType.LESSON.value:
            return "Урок не найден."
        event.starts_at = event.starts_at + timedelta(minutes=shift_minutes)
        if event.ends_at is not None:
            event.ends_at = event.ends_at + timedelta(minutes=shift_minutes)
        event.extra_data["time"] = event.starts_at.astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
        await self._events.update(event)
        await self._sync_due_index(event)
        return f"Урок перенесен на {shift_minutes} минут."

    async def mark_lesson_paid(
        self,
        user: User,
        event_id: UUID | None,
        search_text: str | None = None,
        amount: int = 0,
        prepaid_lessons_add: int | None = None,
        prepaid_lessons_set: int | None = None,
        payment_total: int | None = None,
    ) -> str:
        if event_id is None and search_text and self._students is not None:
            student = await self._students.get_or_create_by_name(user.id, search_text)
            if (
                payment_total is not None
                and prepaid_lessons_add is None
                and prepaid_lessons_set is None
            ):
                lesson_price = self._infer_student_lesson_price(student)
                if lesson_price is None:
                    return (
                        f"Получил сумму {payment_total} для {student.name}, но не знаю цену одного занятия. "
                        "Уточните стоимость одного занятия или количество занятий для зачисления."
                    )
                inferred_lessons = payment_total // lesson_price
                remainder = payment_total % lesson_price
                if inferred_lessons <= 0:
                    return (
                        f"Сумма {payment_total} меньше цены занятия {lesson_price}. "
                        "Уточните, это частичная оплата или нужно установить баланс вручную."
                    )
                prepaid_lessons_add = inferred_lessons
                if remainder > 0:
                    student.comment = (
                        f"{student.comment}\nОстаток после автозачисления: {remainder}"
                        if student.comment
                        else f"Остаток после автозачисления: {remainder}"
                    )
            if prepaid_lessons_set is not None:
                student.subscription_remaining_lessons = max(prepaid_lessons_set, 0)
            if prepaid_lessons_add is not None:
                current = student.subscription_remaining_lessons or 0
                student.subscription_remaining_lessons = current + max(prepaid_lessons_add, 0)
            if payment_total is not None and payment_total > 0:
                student.total_paid_amount += payment_total
                student.payment_status = "paid"
                await self._record_payment_transaction(
                    user_id=user.id,
                    student_id=student.id,
                    event_id=None,
                    amount=payment_total,
                    prepaid_lessons_delta=(prepaid_lessons_add or 0),
                    source="manual_balance",
                    note=f"Пополнение для {student.name}",
                )
            await self._students.update(student)
            remaining = student.subscription_remaining_lessons or 0
            return f"Баланс предоплаченных занятий обновлен для {student.name}: осталось {remaining}."

        if event_id is None:
            return "Уточните, для какого урока отметить оплату."
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or event.event_type != EventType.LESSON.value:
            return "Урок не найден."
        event.extra_data["payment_status"] = "paid"
        event.extra_data["payment_amount"] = max(amount, 0)
        event.extra_data["payment_paid_at"] = datetime.now(tz=UTC).isoformat()
        await self._events.update(event)

        student_id_raw = event.extra_data.get("student_id")
        if self._students is not None and isinstance(student_id_raw, str):
            try:
                student = await self._students.get_for_user_by_id(user.id, UUID(student_id_raw))
            except ValueError:
                student = None
            if student is not None:
                student.payment_status = "paid"
                student.total_paid_amount += max(amount, 0)
                if payment_total is not None and payment_total > 0:
                    student.total_paid_amount += payment_total
                student.last_lesson_at = datetime.now(tz=UTC)
                if amount > 0:
                    student.default_lesson_price = amount
                if prepaid_lessons_set is not None:
                    student.subscription_remaining_lessons = max(prepaid_lessons_set, 0)
                if prepaid_lessons_add is not None:
                    current = student.subscription_remaining_lessons or 0
                    student.subscription_remaining_lessons = current + max(prepaid_lessons_add, 0)
                if student.subscription_remaining_lessons is not None and student.subscription_remaining_lessons > 0:
                    student.subscription_remaining_lessons -= 1
                await self._record_payment_transaction(
                    user_id=user.id,
                    student_id=student.id,
                    event_id=event.id,
                    amount=max(amount, 0) + (payment_total or 0),
                    prepaid_lessons_delta=(prepaid_lessons_add or 0) - 1,
                    source="lesson_payment",
                    note=f"Оплата урока {event.title}",
                )
                await self._students.update(student)
                if student.subscription_remaining_lessons == 1:
                    return (
                        f"Оплата отмечена для {student.name}. "
                        "По абонементу осталось 1 занятие."
                    )
                return f"Оплата отмечена для {student.name}."

        return "Оплата отмечена."

    def _infer_student_lesson_price(self, student: Student) -> int | None:
        if student.default_lesson_price is not None and student.default_lesson_price > 0:
            return student.default_lesson_price
        if (
            student.subscription_price is not None
            and student.subscription_total_lessons is not None
            and student.subscription_total_lessons > 0
        ):
            inferred = student.subscription_price // student.subscription_total_lessons
            if inferred > 0:
                return inferred
        return None

    async def _record_payment_transaction(
        self,
        *,
        user_id: int,
        student_id: UUID | None,
        event_id: UUID | None,
        amount: int,
        prepaid_lessons_delta: int,
        source: str,
        note: str,
    ) -> None:
        if self._payments is None:
            return
        item = PaymentTransaction(
            user_id=user_id,
            student_id=student_id,
            event_id=event_id,
            amount=amount,
            prepaid_lessons_delta=prepaid_lessons_delta,
            source=source,
            note=note,
        )
        await self._payments.create(item)

    async def suggest_reschedule_slots(self, user: User, event: Event, limit: int = 3) -> list[datetime]:
        lessons = [
            item
            for item in await self._events.list_active_lessons_for_user(user.id)
            if item.id != event.id
        ]
        duration = (event.ends_at - event.starts_at) if event.ends_at else timedelta(minutes=60)
        now_local = datetime.now(tz=UTC).astimezone(ZoneInfo(user.timezone))
        candidate = now_local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        result: list[datetime] = []
        attempts = 0
        while len(result) < limit and attempts < 168:
            weekday = candidate.isoweekday()
            if weekday in user.work_days:
                start = candidate.astimezone(UTC)
                end = start + duration
                if not self._has_lesson_conflict(lessons, start, end, user.min_buffer_minutes):
                    result.append(start)
            candidate += timedelta(hours=1)
            attempts += 1
        return result

    async def suggest_reschedule_slots_v2(self, user: User, event: Event) -> list[tuple[str, datetime]]:
        base = await self.suggest_reschedule_slots(user=user, event=event, limit=12)
        if not base:
            return []
        return self._optimizer.choose_reschedule_slots(base, timezone=user.timezone)

    async def mark_lesson_missed(self, user: User, event_id: UUID | None) -> str:
        if event_id is None:
            return "Уточните, для какого урока отметить пропуск."
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or event.event_type != EventType.LESSON.value:
            return "Урок не найден."
        event.extra_data["attendance_status"] = "missed"
        await self._events.update(event)

        student_id_raw = event.extra_data.get("student_id")
        if self._students is not None and isinstance(student_id_raw, str):
            try:
                student = await self._students.get_for_user_by_id(user.id, UUID(student_id_raw))
            except ValueError:
                student = None
            if student is not None:
                student.missed_lessons_count += 1
                student.last_lesson_at = datetime.now(tz=UTC)
                await self._students.update(student)
                return f"Пропуск отмечен для {student.name}."
        return "Пропуск отмечен."

    async def update_student(self, user: User, cmd: UpdateStudentCommand) -> str:
        if self._students is None:
            return "Сервис учеников недоступен."
        student = await self._students.get_or_create_by_name(user.id, cmd.student_name)
        if cmd.lesson_price is not None:
            student.default_lesson_price = cmd.lesson_price
        if cmd.status is not None:
            student.status = cmd.status
        if cmd.goal is not None:
            student.goal = cmd.goal
        if cmd.level is not None:
            student.level = cmd.level
        if cmd.weekly_frequency is not None:
            student.weekly_frequency = cmd.weekly_frequency
        if cmd.preferred_slots is not None:
            student.preferred_slots = cmd.preferred_slots
        await self._students.update(student)
        if cmd.lesson_price is not None:
            return f"Цена занятия для {student.name} обновлена: {cmd.lesson_price}."
        return f"Ученик {student.name} обновлен."

    async def create_student(self, user: User, cmd: CreateStudentCommand) -> str:
        if self._students is None:
            return "Сервис учеников недоступен."
        student = await self._students.get_or_create_by_name(user.id, cmd.student_name)
        student.status = cmd.status
        if cmd.lesson_price is not None:
            student.default_lesson_price = cmd.lesson_price
        if cmd.goal is not None:
            student.goal = cmd.goal
        if cmd.level is not None:
            student.level = cmd.level
        if cmd.weekly_frequency is not None:
            student.weekly_frequency = cmd.weekly_frequency
        if cmd.preferred_slots is not None:
            student.preferred_slots = cmd.preferred_slots
        await self._students.update(student)
        return f"Ученик добавлен/обновлен: {student.name}."

    async def delete_student(self, user: User, cmd: DeleteStudentCommand) -> str:
        if self._students is None:
            return "Сервис учеников недоступен."
        student = await self._students.find_by_name(user.id, cmd.student_name)
        if student is None:
            return f"Ученик {cmd.student_name} не найден."
        student.is_active = False
        await self._students.update(student)
        deleted_lessons = 0
        if cmd.delete_future_lessons:
            lessons = await self._events.list_active_lessons_for_user(user.id)
            for lesson in lessons:
                sid = lesson.extra_data.get("student_id")
                sname = str(lesson.extra_data.get("student_name", lesson.title))
                if sid == str(student.id) or sname.lower() == student.name.lower():
                    await self._events.soft_delete(lesson)
                    await self._sync_due_index(lesson)
                    deleted_lessons += 1
        if deleted_lessons:
            return f"Ученик удален: {student.name}. Также отменено уроков: {deleted_lessons}."
        return f"Ученик удален: {student.name}."

    async def student_card(self, user: User, cmd: StudentCardCommand) -> str:
        if self._students is None:
            return "Сервис учеников недоступен."
        student = await self._students.find_by_name(user.id, cmd.student_name)
        if student is None:
            return f"Ученик {cmd.student_name} не найден."
        if cmd.view == "balance":
            return (
                f"Баланс {student.name}: "
                f"предоплачено занятий {student.subscription_remaining_lessons or 0}, "
                f"цена занятия {student.default_lesson_price or 'не задана'}."
            )

        lessons = await self._events.list_active_lessons_for_user(user.id)
        student_lessons = [x for x in lessons if str(x.extra_data.get("student_name", x.title)).lower() == student.name.lower()]
        student_lessons.sort(key=lambda item: item.starts_at, reverse=True)

        if cmd.view == "history":
            lines = [f"История {student.name}:"]
            for item in student_lessons[:10]:
                lines.append(
                    f"- {item.starts_at.astimezone(ZoneInfo(user.timezone)).strftime('%d.%m %H:%M')} "
                    f"оплата={item.extra_data.get('payment_status', 'unknown')} "
                    f"посещаемость={item.extra_data.get('attendance_status', 'ok')}"
                )
            return "\n".join(lines)

        notes_count = 0
        if self._notes is not None:
            notes = await self._notes.list_for_user(user_id=user.id, search_text=student.name)
            notes_count = len(notes)

        lines = [f"Карточка ученика: {student.name}"]
        lines.append(f"- Статус: {student.status}")
        lines.append(f"- Цена: {student.default_lesson_price or 'не задана'}")
        lines.append(f"- Предоплачено занятий: {student.subscription_remaining_lessons or 0}")
        lines.append(f"- Пропуски: {student.missed_lessons_count}")
        lines.append(f"- Отмены учеником: {student.canceled_by_student_count}")
        lines.append(f"- Отмены репетитором: {student.canceled_by_tutor_count}")
        lines.append(f"- Оплачено всего: {student.total_paid_amount}")
        lines.append(f"- Заметок: {notes_count}")
        if student.goal:
            lines.append(f"- Цель: {student.goal}")
        if student.level:
            lines.append(f"- Уровень: {student.level}")
        if student.preferred_slots:
            lines.append(f"- Предпочтительные слоты: {', '.join(student.preferred_slots)}")
        return "\n".join(lines)

    async def parse_bank_transfer(self, user: User, cmd: ParseBankTransferCommand) -> tuple[str, str | None, int | None]:
        # Command is expected to be prepared by LLM; this method validates execution prerequisites.
        name = cmd.student_name
        amount = cmd.amount
        if not name:
            return "Уточните, от какого ученика перевод.", None, None
        if amount is None or amount <= 0:
            return "Уточните сумму перевода.", None, None
        return (
            f"Нашел перевод: {name}, сумма {amount}. Подтвердить зачисление предоплаты?",
            name,
            amount,
        )

    async def add_note_to_lesson(self, user: User, event_id: UUID, content: str | None = None) -> str:
        event = await self._events.get_for_user(user_id=user.id, event_id=event_id)
        if event is None or event.event_type != EventType.LESSON.value:
            return "Урок не найден."
        if self._notes is None:
            return "Сервис заметок недоступен."
        from app.db.models import Note

        note = Note(
            user_id=user.id,
            linked_event_id=event.id,
            title=f"Заметка к уроку: {event.title}",
            content=content or "Добавьте заметку по уроку.",
            tags=["lesson-note"],
        )
        await self._notes.create(note)
        return "Заметка добавлена к уроку."

    async def _resolve_event(
        self,
        user_id: int,
        event_id: object | None,
        search_text: str | None,
        allowed_types: set[str],
    ) -> Event | None:
        event: Event | None = None
        if event_id is not None:
            from uuid import UUID

            if isinstance(event_id, UUID):
                event = await self._events.get_for_user(user_id=user_id, event_id=event_id)

        if event is None and search_text:
            event = await self._events.find_by_title(user_id=user_id, search_text=search_text)

        if event is None:
            return None

        if event.event_type not in allowed_types or not event.is_active:
            return None
        return event

    async def _resolve_note(self, user_id: int, note_id: object | None, search_text: str | None) -> Note | None:
        if self._notes is None:
            return None

        note: Note | None = None
        if note_id is not None:
            from uuid import UUID

            if isinstance(note_id, UUID):
                note = await self._notes.get_for_user(user_id=user_id, note_id=note_id)
        if note is None and search_text:
            note = await self._notes.find_first(user_id=user_id, search_text=search_text)
        return note

    def _validate_timezone(self, timezone: str) -> None:
        ZoneInfo(timezone)

    async def _sync_due_index(self, event: Event) -> None:
        if self._due_index is None:
            return
        await self._due_index.sync_event(event)

    def _template_slots(self, template: str) -> list[ScheduleSlotInput]:
        if template == "tutor_week_dense":
            return [
                ScheduleSlotInput(weekday="MO", time="09:00", student_name="Ученик 1", duration_minutes=60),
                ScheduleSlotInput(weekday="TU", time="10:00", student_name="Ученик 2", duration_minutes=60),
                ScheduleSlotInput(weekday="WE", time="11:00", student_name="Ученик 3", duration_minutes=60),
                ScheduleSlotInput(weekday="TH", time="12:00", student_name="Ученик 4", duration_minutes=60),
                ScheduleSlotInput(weekday="FR", time="13:00", student_name="Ученик 5", duration_minutes=60),
            ]
        return [
            ScheduleSlotInput(weekday="MO", time="09:00", student_name="Ученик 1", duration_minutes=60),
            ScheduleSlotInput(weekday="WE", time="09:00", student_name="Ученик 2", duration_minutes=60),
            ScheduleSlotInput(weekday="FR", time="09:00", student_name="Ученик 3", duration_minutes=60),
        ]

    def _has_lesson_conflict(
        self,
        lessons: list[Event],
        starts_at: datetime,
        ends_at: datetime,
        min_buffer_minutes: int = 0,
    ) -> bool:
        ranges = [
            (
                lesson.starts_at,
                lesson.ends_at or (lesson.starts_at + timedelta(minutes=60)),
            )
            for lesson in lessons
        ]
        conflict = self._conflicts.detect_schedule_conflicts(
            starts=starts_at,
            ends=ends_at,
            existing=ranges,
            min_buffer_minutes=min_buffer_minutes,
        )
        return conflict.has_conflict
