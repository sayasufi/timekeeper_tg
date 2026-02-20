from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.enums import Intent


class BaseCommand(BaseModel):
    intent: Intent


class CreateReminderCommand(BaseCommand):
    intent: Literal[Intent.CREATE_REMINDER]
    title: str
    start_at: str | None = None
    description: str | None = None
    timezone: str | None = None
    rrule: str | None = None
    remind_offsets: list[int] = Field(default_factory=lambda: [0])

    @field_validator("remind_offsets")
    @classmethod
    def validate_offsets(cls, value: list[int]) -> list[int]:
        if not value:
            return [0]
        if any(offset < 0 for offset in value):
            msg = "Offsets must be non-negative"
            raise ValueError(msg)
        return sorted(set(value), reverse=True)


class UpdateReminderCommand(BaseCommand):
    intent: Literal[Intent.UPDATE_REMINDER]
    event_id: UUID | None = None
    search_text: str | None = None
    title: str | None = None
    start_at: str | None = None
    rrule: str | None = None
    remind_offsets: list[int] | None = None


class DeleteReminderCommand(BaseCommand):
    intent: Literal[Intent.DELETE_REMINDER]
    event_id: UUID | None = None
    search_text: str | None = None


class ListEventsCommand(BaseCommand):
    intent: Literal[Intent.LIST_EVENTS]
    period: Literal["today", "tomorrow", "week", "date", "all"] = "today"
    date: str | None = None
    student_name: str | None = None


class ScheduleSlotInput(BaseModel):
    weekday: Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    time: str
    duration_minutes: int = 60
    student_name: str | None = None
    subject: str | None = None
    location: str | None = None
    remind_offsets: list[int] = Field(default_factory=lambda: [60, 15])

    @model_validator(mode="after")
    def validate_student_or_subject(self) -> ScheduleSlotInput:
        if not (self.student_name or self.subject):
            msg = "Either student_name or subject must be provided"
            raise ValueError(msg)
        return self


class CreateScheduleCommand(BaseCommand):
    intent: Literal[Intent.CREATE_SCHEDULE]
    slots: list[ScheduleSlotInput] = Field(default_factory=list)
    template: Literal["tutor_week_basic", "tutor_week_dense"] | None = None


class UpdateScheduleCommand(BaseCommand):
    intent: Literal[Intent.UPDATE_SCHEDULE]
    event_id: UUID | None = None
    search_text: str | None = None
    title: str | None = None
    weekday: Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"] | None = None
    time: str | None = None
    duration_minutes: int | None = None
    remind_offsets: list[int] | None = None
    delete: bool = False
    shift_weekday: Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"] | None = None
    shift_minutes: int | None = None
    apply_to_all: bool = False
    occurrence_date: str | None = None
    new_date: str | None = None
    new_time: str | None = None
    apply_scope: Literal["single_week", "series"] | None = None
    bulk_cancel_weekday: Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"] | None = None
    bulk_cancel_scope: Literal["next_week", "all_future"] | None = None


class CreateBirthdayCommand(BaseCommand):
    intent: Literal[Intent.CREATE_BIRTHDAY]
    person: str
    date: str
    remind_offsets: list[int] = Field(default_factory=lambda: [1440, 0])


class MarkLessonPaidCommand(BaseCommand):
    intent: Literal[Intent.MARK_LESSON_PAID]
    event_id: UUID | None = None
    search_text: str | None = None
    amount: int = 0
    prepaid_lessons_add: int | None = None
    prepaid_lessons_set: int | None = None
    payment_total: int | None = None

    @field_validator("prepaid_lessons_add", "prepaid_lessons_set")
    @classmethod
    def validate_prepaid_non_negative(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            msg = "Prepaid lessons must be non-negative"
            raise ValueError(msg)
        return value


class MarkLessonMissedCommand(BaseCommand):
    intent: Literal[Intent.MARK_LESSON_MISSED]
    event_id: UUID | None = None
    search_text: str | None = None


class CreateStudentCommand(BaseCommand):
    intent: Literal[Intent.CREATE_STUDENT]
    student_name: str
    lesson_price: int | None = None
    status: Literal["active", "paused", "left"] = "active"
    goal: str | None = None
    level: str | None = None
    weekly_frequency: int | None = None
    preferred_slots: list[str] | None = None


class DeleteStudentCommand(BaseCommand):
    intent: Literal[Intent.DELETE_STUDENT]
    student_name: str
    delete_future_lessons: bool = False


class UpdateStudentCommand(BaseCommand):
    intent: Literal[Intent.UPDATE_STUDENT]
    student_name: str
    lesson_price: int | None = None
    status: Literal["active", "paused", "left"] | None = None
    goal: str | None = None
    level: str | None = None
    weekly_frequency: int | None = None
    preferred_slots: list[str] | None = None

    @field_validator("lesson_price")
    @classmethod
    def validate_lesson_price(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value <= 0:
            msg = "Lesson price must be positive"
            raise ValueError(msg)
        return value


class UpdateSettingsCommand(BaseCommand):
    intent: Literal[Intent.UPDATE_SETTINGS]
    timezone: str | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    quiet_off: bool = False
    work_start: str | None = None
    work_end: str | None = None
    work_off: bool = False
    work_days: list[int] | None = None
    min_buffer_minutes: int | None = None


class TutorReportCommand(BaseCommand):
    intent: Literal[Intent.TUTOR_REPORT]
    report_type: Literal[
        "today",
        "tomorrow",
        "missed",
        "finance_week",
        "finance_month",
        "attendance_week",
        "attendance_month",
    ] = "today"


class StudentCardCommand(BaseCommand):
    intent: Literal[Intent.STUDENT_CARD]
    student_name: str
    view: Literal["card", "history", "balance"] = "card"


class ParseBankTransferCommand(BaseCommand):
    intent: Literal[Intent.PARSE_BANK_TRANSFER]
    raw_text: str
    student_name: str | None = None
    amount: int | None = None
    source: str = "bank_text"


class CreateNoteCommand(BaseCommand):
    intent: Literal[Intent.CREATE_NOTE]
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    linked_event_id: UUID | None = None


class UpdateNoteCommand(BaseCommand):
    intent: Literal[Intent.UPDATE_NOTE]
    note_id: UUID | None = None
    search_text: str | None = None
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    linked_event_id: UUID | None = None


class DeleteNoteCommand(BaseCommand):
    intent: Literal[Intent.DELETE_NOTE]
    note_id: UUID | None = None
    search_text: str | None = None


class ListNotesCommand(BaseCommand):
    intent: Literal[Intent.LIST_NOTES]
    search_text: str | None = None


class ClarifyCommand(BaseCommand):
    intent: Literal[Intent.CLARIFY]
    question: str


ParsedCommand = Annotated[
    (
        CreateReminderCommand
        | UpdateReminderCommand
        | DeleteReminderCommand
        | ListEventsCommand
        | CreateScheduleCommand
        | UpdateScheduleCommand
        | MarkLessonPaidCommand
        | MarkLessonMissedCommand
        | CreateStudentCommand
        | DeleteStudentCommand
        | UpdateStudentCommand
        | StudentCardCommand
        | ParseBankTransferCommand
        | UpdateSettingsCommand
        | TutorReportCommand
        | CreateBirthdayCommand
        | CreateNoteCommand
        | UpdateNoteCommand
        | DeleteNoteCommand
        | ListNotesCommand
        | ClarifyCommand
    ),
    Field(discriminator="intent"),
]
