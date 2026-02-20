# TimeKeeper

Production-ready Telegram bot service for reminders, lessons schedule, birthdays, and calendar-like queries.

## Architecture (short)

TimeKeeper uses **clean architecture / light DDD** with strict boundaries:

- `domain`: intent contracts and core command models (`Pydantic v2`), no Telegram/FastAPI dependencies.
- `services`: business use-cases (parse natural language command -> execute CRUD on events -> format response).
- `repositories`: data access via SQLAlchemy 2 async repositories.
- `integrations`: adapters for external LLM/STT and Telegram notifications.
- `bot`: aiogram handlers/middlewares (text + voice), anti-flood protection.
- `api`: FastAPI webhook entrypoint + health/admin endpoints.
- `scheduler`: Celery worker + beat for background reminders and daily lessons digest.
- `db`: ORM models, session, seed script.

### Why Celery + Redis (instead of APScheduler)

Chosen: **Celery + Redis** for production reliability:
- horizontal scaling (separate API and workers)
- retry/task queue semantics and fault isolation
- resilient periodic jobs via Celery Beat
- clear path to future workload growth

APScheduler is simpler, but less robust for distributed deployment with multiple replicas.

## Features

- Natural-language create/update/delete/list reminders.\n- Интерактивное разрешение неоднозначностей через inline-кнопки выбора события.
- Voice handling via STT adapter (`voice -> text -> same pipeline`).
- Weekly teacher schedule (create/update/delete slots).
- Daily lessons digest and pre-lesson reminders.
- Birthdays and date-based reminders.
- Strict LLM JSON contract + schema validation + recovery mode fallback.
- User data isolation by `telegram_id`.
- JSON snapshot export endpoint.
- Idempotent webhook update handling.
- Retry wrappers for external LLM/STT.
- Structured logging (`structlog`), health endpoints, basic anti-flood.\n- Outbox pattern для надежной доставки уведомлений.\n- Due-index (`due_notifications`) для эффективного выбора ближайших уведомлений без полного сканирования всех событий.\n- Персональные quiet/work hours и timezone-команды.

## Project Tree

```text
tg-helper/
  alembic/
    env.py
    script.py.mako
    versions/
      20260219_0001_initial.py
  app/
    api/
      deps.py
      routes.py
    bot/
      factory.py
      handlers.py
      middleware.py
    core/
      config.py
      container.py
      datetime_utils.py
      logging.py
      security.py
    db/
      base.py
      models.py
      seed.py
      session.py
    domain/
      commands.py
      enums.py
    integrations/
      llm/
        base.py
        client.py
      stt/
        base.py
        client.py
      telegram/
        base.py
        notifier.py
    repositories/
      event_repository.py
      notification_log_repository.py
      user_repository.py
    scheduler/
      celery_app.py
      tasks.py
    services/
      assistant_service.py
      command_parser_service.py
      event_service.py
      export_service.py
      occurrence_service.py
      reminder_dispatch_service.py
    main.py
  docs/
    llm_contract.schema.json
  tests/
    conftest.py
    integration/
      test_api_routes.py
      test_reminder_dispatch_service.py
    unit/
      test_command_parser.py
      test_datetime_utils.py
      test_event_service.py
      test_occurrence_service.py
  .env.example
  .env.dev.example
  .env.prod.example
  .gitignore
  .pre-commit-config.yaml
  alembic.ini
  docker-compose.yml
  Dockerfile
  Makefile
  pyproject.toml
  README.md
```

## LLM Contract

JSON schema is stored in `docs/llm_contract.schema.json`.

Supported intents:
- `create_reminder`
- `update_reminder`
- `delete_reminder`
- `list_events`
- `create_schedule`
- `update_schedule`
- `create_birthday`
- `clarify`

Pipeline:
1. LLM adapter returns JSON string.
2. Recovery mode extracts JSON from malformed/fenced response.
3. `Pydantic` strict validation on discriminated union.
4. If invalid/ambiguous -> fallback parser asks clarification.

## Example Dialog Mapping

1) User: `РЅР°РїРѕРјРЅРё РјРЅРµ Р·Р°РІС‚СЂР° РІ 10:00 РїСЂРѕ РѕРїР»Р°С‚Сѓ`
- Intent: `create_reminder`
- Entity: `Event(type=reminder, title="РѕРїР»Р°С‚Сѓ", starts_at=..., rrule=null)`

2) User: `РїРѕРєР°Р¶Рё СЂР°СЃРїРёСЃР°РЅРёРµ РЅР° РЅРµРґРµР»СЋ`
- Intent: `list_events(period="week")`
- Output: recurring lessons expanded into current week occurrences

3) User: `РґРѕР±Р°РІСЊ РґРµРЅСЊ СЂРѕР¶РґРµРЅРёСЏ РђРЅРЅС‹ 14 РјР°СЏ`
- Intent: `create_birthday`
- Entity: `Event(type=birthday, title="Р”РµРЅСЊ СЂРѕР¶РґРµРЅРёСЏ: РђРЅРЅР°", rrule="FREQ=YEARLY")`

4) User voice message: `(voice)`
- STT -> text
- Same parser/command execution path as text

5) User: `СѓРґР°Р»Рё СЃРѕР±С‹С‚РёРµ РїСЂРѕ РѕРїР»Р°С‚Сѓ`
- Intent: `delete_reminder(search_text="РѕРїР»Р°С‚Сѓ")`
- Soft-delete (`is_active=false`)

## Data Model (high level)

- `users`: telegram profile, locale, timezone
- `events`: reminders/lessons/birthdays (one-time and recurring RRULE)
- `notification_logs`: deduplication of sent reminders (`event_id + occurrence_at + offset_minutes`)

All timestamps are stored in UTC; display/rendering uses per-user timezone.

## Run Locally

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
cp .env.example .env
```

### 2. Start PostgreSQL/Redis

```bash
docker compose up -d postgres redis
```

### 3. Apply migrations

```bash
alembic upgrade head
```

### 4. Run API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Run worker and beat (separate terminals)

```bash
celery -A app.scheduler.celery_app.celery_app worker -l info
celery -A app.scheduler.celery_app.celery_app beat -l info
```

## Run With Docker

```bash
cp .env.example .env
docker compose up --build
```

Then run migration inside api container:

```bash
docker compose exec api alembic upgrade head
```

## Quality Commands

```bash
make lint
make test
make run
```

`make lint` runs `ruff` + `mypy`.

## User Settings Commands\n\n- `/timezone Europe/Moscow`\n- `/quiet 22:00 08:00` или `/quiet off`\n- `/work 09:00 18:00` или `/work off`\n\n## Admin/Health Endpoints

- `GET /health/live`
- `GET /health/ready`
- `GET /admin/users/{telegram_id}/export`
- `POST /webhook/telegram`

## Tests

Implemented: **22 tests** (unit + integration).

Coverage areas:
- parser strict/recovery/fallback
- datetime and recurrence logic
- CRUD domain scenarios
- reminder dispatch deduplication
- API health/export integration

## Trade-offs

1. Update/Delete by `search_text` currently uses first match; good UX but can be ambiguous.
2. Reminder dispatcher currently iterates users/events each cycle; easy to reason about, can be optimized with indexed due-queue in v2.
3. LLM adapter contract is generic (`/parse` endpoint); easy to integrate with existing service but may require custom payload mapping for specific providers.
4. Daily lessons digest uses a simple local-time window policy (07:00-07:09 local); robust enough for baseline, can be refined with per-user delivery preferences.

## v2 Improvements

1. Multi-channel notifications (email/push/webhooks) with channel policies.
2. Conflict resolution flow for ambiguous edits (interactive selection buttons).
3. Better recurrence editor (full RRULE wizard).
4. Per-user quiet hours and priority rules.
5. Outbox pattern + exactly-once semantics for external senders.
6. Observability stack integration (Prometheus metrics + tracing).
7. Advanced anti-spam (adaptive limits + abuse scoring).
8. Full OpenAPI admin for bulk operations and analytics.

## Smart Agents Runtime

- Agent graph keeps conditional branching (clarify/recover/resolve) and does not use environment feature flags.
- Routing mode is selected deterministically per user request (`fast` or `precise`) without manual toggles.
- Every parser run stores an audit trace in `agent_run_traces`:
  - selected path through graph
  - per-stage timings and confidence
  - final intent and route mode
- Unified agent contract for LLM stages:
  - `{"result": {...}, "confidence": 0..1, "needs_clarification": bool, "clarify_question": "...", "reasons": []}`
- Added practical agents:
  - `RecurrenceUnderstandingAgent`
  - `EventDisambiguationAgent`
  - `ChangeImpactAgent`
  - `FollowUpPlannerAgent`
  - `UserMemoryAgent`
  - `DigestPrioritizationAgent`
- Free-text and voice-first operation:
  - all key tutor actions (schedule view/edit, reschedule, payments, missed) are supported via NL parsing
  - slash commands are minimized; primary UX is natural language text/voice

## Expanded Functionalities

- Notes CRUD (`create_note`, `update_note`, `delete_note`, `list_notes`) with DB table `notes`.
- Smart snooze actions from reminder notifications (10/30/60 min inline buttons).
- Conflict-aware schedule creation/update with collision checks.
- Bulk natural-language schedule shift support via `update_schedule`:
  - `apply_to_all=true`, `shift_weekday`, `shift_minutes`
- Schedule templates via `create_schedule.template`:
  - `tutor_week_basic`
  - `tutor_week_dense`
- Tutor-first schedule model:
  - lessons are linked to `student_name`
  - optional filter for list queries: `student_name`
  - day/week/date output emphasizes time + student name
  - separate `students` entity for tutor CRM-lite
- Tutor operational tools:
  - day reports and missed reports via natural language:
    - "что у меня сегодня"
    - "что у меня завтра"
    - "кто пропустил"
  - lesson quick actions via inline buttons:
    - reschedule (stateful plain-text follow-up)
    - cancel lesson
    - mark paid
    - mark missed
    - add lesson note
  - one-week reschedule override (default):
    - move lesson to a different day/time only for current week
    - base recurring schedule remains unchanged
