from enum import StrEnum


class Intent(StrEnum):
    CREATE_REMINDER = "create_reminder"
    UPDATE_REMINDER = "update_reminder"
    DELETE_REMINDER = "delete_reminder"
    LIST_EVENTS = "list_events"
    CREATE_NOTE = "create_note"
    UPDATE_NOTE = "update_note"
    DELETE_NOTE = "delete_note"
    LIST_NOTES = "list_notes"
    CREATE_SCHEDULE = "create_schedule"
    UPDATE_SCHEDULE = "update_schedule"
    MARK_LESSON_PAID = "mark_lesson_paid"
    MARK_LESSON_MISSED = "mark_lesson_missed"
    CREATE_STUDENT = "create_student"
    DELETE_STUDENT = "delete_student"
    UPDATE_STUDENT = "update_student"
    STUDENT_CARD = "student_card"
    PARSE_BANK_TRANSFER = "parse_bank_transfer"
    UPDATE_SETTINGS = "update_settings"
    TUTOR_REPORT = "tutor_report"
    CREATE_BIRTHDAY = "create_birthday"
    CLARIFY = "clarify"


class EventType(StrEnum):
    REMINDER = "reminder"
    LESSON = "lesson"
    BIRTHDAY = "birthday"
