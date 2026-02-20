from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.llm.base import LLMClient
from app.integrations.stt.base import STTClient
from app.integrations.telegram.base import Notifier
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository
from app.repositories.due_notification_repository import DueNotificationRepository
from app.repositories.event_repository import EventRepository
from app.repositories.note_repository import NoteRepository
from app.repositories.notification_log_repository import NotificationLogRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.payment_transaction_repository import PaymentTransactionRepository
from app.repositories.student_repository import StudentRepository
from app.repositories.user_repository import UserRepository
from app.services.assistant_service import AssistantService
from app.services.bot_response_service import BotResponseService
from app.services.command_parser_service import CommandParserService
from app.services.dialog_state_store import DialogStateStore
from app.services.due_index_service import DueIndexService
from app.services.event_service import EventService
from app.services.export_service import ExportService
from app.services.reminder_dispatch_service import ReminderDispatchService


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    llm_client: LLMClient
    stt_client: STTClient
    notifier: Notifier

    def _create_event_service(self, session: AsyncSession) -> EventService:
        event_repo = EventRepository(session)
        note_repo = NoteRepository(session)
        student_repo = StudentRepository(session)
        payment_repo = PaymentTransactionRepository(session)
        due_repo = DueNotificationRepository(session)
        due_index_service = DueIndexService(due_repo)
        return EventService(
            event_repo,
            due_index_service=due_index_service,
            note_repository=note_repo,
            student_repository=student_repo,
            payment_repository=payment_repo,
        )

    def create_bot_response_service(self) -> BotResponseService:
        return BotResponseService(self.llm_client)

    def create_assistant_service(self, session: AsyncSession) -> AssistantService:
        user_repo = UserRepository(session)
        trace_repo = AgentRunTraceRepository(session)
        parser = CommandParserService(self.llm_client, trace_repository=trace_repo)
        event_service = self._create_event_service(session)
        return AssistantService(
            session=session,
            user_repository=user_repo,
            parser_service=parser,
            event_service=event_service,
            response_renderer=self.create_bot_response_service(),
            dialog_state_store=DialogStateStore(self.redis),
        )

    def create_export_service(self, session: AsyncSession) -> ExportService:
        user_repo = UserRepository(session)
        event_service = self._create_event_service(session)
        return ExportService(
            user_repository=user_repo,
            event_service=event_service,
            export_dir=self.settings.export_dir,
        )

    def create_dispatch_service(self, session: AsyncSession) -> ReminderDispatchService:
        user_repo = UserRepository(session)
        event_repo = EventRepository(session)
        due_repo = DueNotificationRepository(session)
        outbox_repo = OutboxRepository(session)
        log_repo = NotificationLogRepository(session)
        due_index_service = DueIndexService(due_repo)
        note_repo = NoteRepository(session)
        student_repo = StudentRepository(session)
        payment_repo = PaymentTransactionRepository(session)
        event_service = EventService(
            event_repo,
            due_index_service=due_index_service,
            note_repository=note_repo,
            student_repository=student_repo,
            payment_repository=payment_repo,
        )
        return ReminderDispatchService(
            user_repository=user_repo,
            event_repository=event_repo,
            due_repository=due_repo,
            outbox_repository=outbox_repo,
            log_repository=log_repo,
            due_index_service=due_index_service,
            event_service=event_service,
            notifier=self.notifier,
            response_renderer=self.create_bot_response_service(),
        )
