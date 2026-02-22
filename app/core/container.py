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
from app.services.assistant.assistant_adapters_service import AssistantAdaptersService
from app.services.assistant.assistant_service import AssistantService
from app.services.assistant.assistant_use_cases_service import AssistantUseCasesService
from app.services.assistant.batch_execution_service import BatchExecutionService
from app.services.assistant.bot_response_service import BotResponseService
from app.services.assistant.command_execution_service import CommandExecutionService
from app.services.assistant.confirmation_service import ConfirmationService
from app.services.assistant.conversation_flow_service import ConversationFlowService
from app.services.assistant.conversation_state_service import ConversationStateService
from app.services.assistant.interaction_handlers_service import InteractionHandlersService
from app.services.assistant.pending_reschedule_service import PendingRescheduleService
from app.services.assistant.planning_facade_service import PlanningFacadeService
from app.services.assistant.quick_action_service import QuickActionService
from app.services.assistant.response_orchestration_service import ResponseOrchestrationService
from app.services.events.event_service import EventService
from app.services.exports.export_service import ExportService
from app.services.parser.command_parser_service import CommandParserService
from app.services.reminders.due_index_service import DueIndexService
from app.services.reminders.reminder_dispatch_service import ReminderDispatchService
from app.services.smart_agents import UserMemoryAgent
from app.services.stores.dialog_state_store import DialogStateStore


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
            redis=self.redis,
            schedule_cache_ttl_seconds=self.settings.schedule_cache_ttl_seconds,
            compact_context_cache_ttl_seconds=self.settings.compact_context_cache_ttl_seconds,
        )

    def create_bot_response_service(self) -> BotResponseService:
        return BotResponseService(self.llm_client)

    def create_assistant_service(self, session: AsyncSession) -> AssistantService:
        user_repo = UserRepository(session)
        trace_repo = AgentRunTraceRepository(session)
        parser = CommandParserService(self.llm_client, trace_repository=trace_repo)
        event_service = self._create_event_service(session)
        response_renderer = self.create_bot_response_service()
        dialog_state_store = DialogStateStore(self.redis)
        adapters = AssistantAdaptersService(parser=parser)
        conversation_state = ConversationStateService(
            dialog_state_store=dialog_state_store,
            event_service=event_service,
        )
        memory = UserMemoryAgent()
        command_execution = CommandExecutionService(
            users=user_repo,
            events=event_service,
            ask_clarification=adapters.ask_clarification,
            memory=memory,
        )
        batch_execution = BatchExecutionService(
            session=session,
            parser=parser,
            execute_command=adapters.execute_batch_command,
            ask_clarification=adapters.ask_clarification,
        )
        response_orchestration = ResponseOrchestrationService(
            parser=parser,
            response_renderer=response_renderer,
            memory=memory,
        )
        adapters.bind_services(
            command_execution=command_execution,
            batch_execution=batch_execution,
            response_orchestration=response_orchestration,
        )
        pending_reschedule = PendingRescheduleService(
            parser=parser,
            events=event_service,
            ask_clarification=adapters.ask_clarification,
        )
        planning = PlanningFacadeService(
            parser=parser,
            task_orchestrator=parser.task_orchestrator,
        )
        flow = ConversationFlowService(
            session=session,
            users=user_repo,
            parser=parser,
            planning=planning,
            conversation_state=conversation_state,
            memory=memory,
            finalize_response=adapters.finalize_response,
            execute_with_disambiguation=adapters.execute_with_disambiguation,
            handle_batch_operations=adapters.handle_batch_operations,
            ask_clarification=adapters.ask_clarification,
        )
        quick_actions = QuickActionService(
            events=event_service,
            pending_reschedule=pending_reschedule,
        )
        interactions = InteractionHandlersService(
            session=session,
            users=user_repo,
            parser=parser,
            confirmation_service=ConfirmationService(),
            memory=memory,
            conversation_state=conversation_state,
            pending_reschedule=pending_reschedule,
            quick_actions=quick_actions,
            finalize_response=adapters.finalize_response,
            execute_with_disambiguation=adapters.execute_with_disambiguation,
            execute_batch_with_args=adapters.execute_batch_with_args,
            handle_text=flow.handle_text,
        )
        use_cases = AssistantUseCasesService(
            flow=flow,
            interactions=interactions,
        )
        return AssistantService(
            use_cases=use_cases,
            command_execution=command_execution,
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
            redis=self.redis,
            schedule_cache_ttl_seconds=self.settings.schedule_cache_ttl_seconds,
            compact_context_cache_ttl_seconds=self.settings.compact_context_cache_ttl_seconds,
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
            redis=self.redis,
            outbox_max_attempts=self.settings.outbox_max_attempts,
            outbox_backoff_base_seconds=self.settings.outbox_backoff_base_seconds,
            outbox_backoff_max_seconds=self.settings.outbox_backoff_max_seconds,
            outbox_dedupe_ttl_seconds=self.settings.outbox_dedupe_ttl_seconds,
        )

