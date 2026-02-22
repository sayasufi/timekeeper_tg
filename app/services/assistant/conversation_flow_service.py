from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository
from app.services.assistant.assistant_response import AssistantResponse, ConfirmationRequest
from app.services.assistant.conversation_state_service import ConversationStateService
from app.services.assistant.planning_facade_service import PlanningFacadeService
from app.services.parser.command_parser_service import CommandParserService
from app.services.smart_agents import UserMemoryAgent

logger = structlog.get_logger(__name__)

FinalizeFn = Callable[[object, str | None, AssistantResponse], Awaitable[AssistantResponse]]
ExecuteFn = Callable[[object, object], Awaitable[AssistantResponse]]
BatchFn = Callable[[object, list[str], object, str, bool], Awaitable[AssistantResponse]]
AskClarifyFn = Callable[..., Awaitable[str]]


class ConversationFlowService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        users: UserRepository,
        parser: CommandParserService,
        planning: PlanningFacadeService,
        conversation_state: ConversationStateService,
        memory: UserMemoryAgent,
        finalize_response: FinalizeFn,
        execute_with_disambiguation: ExecuteFn,
        handle_batch_operations: BatchFn,
        ask_clarification: AskClarifyFn,
    ) -> None:
        self._session = session
        self._users = users
        self._parser = parser
        self._planning = planning
        self._conversation_state = conversation_state
        self._memory = memory
        self._finalize_response = finalize_response
        self._execute_with_disambiguation = execute_with_disambiguation
        self._handle_batch_operations = handle_batch_operations
        self._ask_clarification = ask_clarification

    async def handle_text(self, *, telegram_id: int, text: str, language: str) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        user_memory = self._memory.build_profile(user)
        dialog_state = await self._conversation_state.get_state(telegram_id)
        context_package = await self._conversation_state.build_context_package(
            user=user,
            state=dialog_state,
            latest_text=text,
        )

        try:
            mode, operations, answer, question, execution_strategy, stop_on_error = await self._planning.route_conversation(
                text=text,
                locale=user.language,
                timezone=user.timezone,
                user_memory=user_memory,
                context=context_package,
            )
            if mode == "clarify":
                clarify_text = question or await self._ask_clarification(
                    user=user,
                    source_text=text,
                    reason="РќРµ С…РІР°С‚Р°РµС‚ РґР°РЅРЅС‹С… РґР»СЏ Р±РµР·РѕРїР°СЃРЅРѕРіРѕ РІС‹РїРѕР»РЅРµРЅРёСЏ Р·Р°РїСЂРѕСЃР°.",
                    fallback="РЈС‚РѕС‡РЅРёС‚Рµ, РїРѕР¶Р°Р»СѓР№СЃС‚Р°, Р·Р°РїСЂРѕСЃ.",
                    user_memory=user_memory,
                    context=context_package,
                )
                dialog_state.pending_question = clarify_text
                dialog_state.pending_reason = "missing_required_data"
                self._conversation_state.activate_clarification_scenario(state=dialog_state, source_text=text)
                await self._conversation_state.save_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=clarify_text,
                )
                response = AssistantResponse(clarify_text)
                await self._session.commit()
                return await self._finalize_response(user, text, response)
            if mode == "answer":
                await self._session.commit()
                answer_text = answer or await self._parser.render_policy_text(
                    kind="answer_fallback",
                    source_text=text,
                    reason="assistant_answer_missing",
                    locale=user.language,
                    timezone=user.timezone,
                    fallback="РњРѕРіСѓ РїРѕРјРѕС‡СЊ РїРѕ С„СѓРЅРєС†РёСЏРј Р±РѕС‚Р° Рё СЂР°СЃРїРёСЃР°РЅРёСЋ. РЎС„РѕСЂРјСѓР»РёСЂСѓР№С‚Рµ РІРѕРїСЂРѕСЃ С‡СѓС‚СЊ С‚РѕС‡РЅРµРµ.",
                    user_memory=user_memory,
                    context=context_package,
                )
                response = AssistantResponse(answer_text, metadata={"handled_by": "conversation_manager"})
                dialog_state.pending_question = None
                dialog_state.pending_reason = None
                self._conversation_state.clear_scenario(dialog_state)
                await self._conversation_state.save_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=answer_text,
                )
                return await self._finalize_response(user, text, response)

            if len(operations) > 1:
                requires_confirmation, risk_level, plan_preview = await self._planning.assess_plan_risk(
                    text=text,
                    operations=operations,
                    locale=user.language,
                    timezone=user.timezone,
                    user_memory=user_memory,
                    context=context_package,
                )
                if requires_confirmation and risk_level == "high":
                    summary = plan_preview or "РџР°РєРµС‚ СЃРѕРґРµСЂР¶РёС‚ РїРѕС‚РµРЅС†РёР°Р»СЊРЅРѕ СЂРёСЃРєРѕРІР°РЅРЅС‹Рµ РёР·РјРµРЅРµРЅРёСЏ."
                    response = AssistantResponse(
                        "РџРѕРґС‚РІРµСЂРґРёС‚Рµ РІС‹РїРѕР»РЅРµРЅРёРµ РїР°РєРµС‚Р° РѕРїРµСЂР°С†РёР№.",
                        confirmation=ConfirmationRequest(
                            action="batch_execute",
                            command_payload={
                                "__kind": "batch_plan",
                                "operations": operations,
                                "execution_strategy": execution_strategy,
                                "stop_on_error": stop_on_error,
                                "source_text": text,
                            },
                            event_id=None,
                            summary=summary,
                        ),
                    )
                    await self._conversation_state.save_state(
                        telegram_id=telegram_id,
                        state=dialog_state,
                        user_text=text,
                        assistant_text=response.text,
                    )
                    return await self._finalize_response(user, text, response)
                batch_response = await self._handle_batch_operations(
                    user,
                    operations,
                    user_memory,
                    execution_strategy,
                    stop_on_error,
                )
                if plan_preview:
                    risk_badge = {
                        "high": "Р’С‹СЃРѕРєРёР№ СЂРёСЃРє",
                        "medium": "РЎСЂРµРґРЅРёР№ СЂРёСЃРє",
                        "low": "РќРёР·РєРёР№ СЂРёСЃРє",
                    }.get(risk_level, "РќРёР·РєРёР№ СЂРёСЃРє")
                    note = f"РџР»Р°РЅ РёР·РјРµРЅРµРЅРёР№ ({risk_badge}):\n{plan_preview}"
                    if requires_confirmation:
                        note += "\n\nР’РЅРёРјР°РЅРёРµ: РїР»Р°РЅ СЃРѕРґРµСЂР¶РёС‚ РїРѕС‚РµРЅС†РёР°Р»СЊРЅРѕ СЂРёСЃРєРѕРІР°РЅРЅС‹Рµ РёР·РјРµРЅРµРЅРёСЏ."
                    batch_response.text = f"{note}\n\n{batch_response.text}"
                await self._session.commit()
                self._conversation_state.clear_scenario(dialog_state)
                await self._conversation_state.save_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=batch_response.text,
                )
                return await self._finalize_response(user, text, batch_response)

            requires_confirmation, risk_level, plan_preview = await self._planning.assess_plan_risk(
                text=text,
                operations=operations,
                locale=user.language,
                timezone=user.timezone,
                user_memory=user_memory,
                context=context_package,
            )
            if requires_confirmation and risk_level == "high":
                summary = plan_preview or "Подтвердите выполнение операции."
                response = AssistantResponse(
                    "Подтвердите выполнение операции.",
                    confirmation=ConfirmationRequest(
                        action="batch_execute",
                        command_payload={
                            "__kind": "batch_plan",
                            "operations": operations,
                            "execution_strategy": execution_strategy,
                            "stop_on_error": stop_on_error,
                            "source_text": text,
                        },
                        event_id=None,
                        summary=summary,
                    ),
                )
                await self._conversation_state.save_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=response.text,
                )
                return await self._finalize_response(user, text, response)

            command = await self._parser.parse(
                text=text,
                locale=user.language,
                timezone=user.timezone,
                user_id=user.id,
                user_memory=user_memory,
                context=context_package,
            )
            response = await self._execute_with_disambiguation(user, command)
            await self._session.commit()
            if response.confirmation is None and response.ambiguity is None:
                self._conversation_state.clear_scenario(dialog_state)
            await self._conversation_state.save_state(
                telegram_id=telegram_id,
                state=dialog_state,
                user_text=text,
                assistant_text=response.text,
            )
            return await self._finalize_response(user, text, response)
        except Exception as exc:
            await self._session.rollback()
            logger.exception("assistant.handle_text_failed", telegram_id=telegram_id, error=str(exc))
            response = AssistantResponse(
                await self._ask_clarification(
                    user=user,
                    source_text=text,
                    reason=f"Р’РЅСѓС‚СЂРµРЅРЅСЏСЏ РѕС€РёР±РєР°: {exc}",
                    fallback="РџСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР° РѕР±СЂР°Р±РѕС‚РєРё Р·Р°РїСЂРѕСЃР°. РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р·.",
                    user_memory=user_memory,
                    context=context_package,
                )
            )
            self._conversation_state.activate_clarification_scenario(state=dialog_state, source_text=text)
            await self._conversation_state.save_state(
                telegram_id=telegram_id,
                state=dialog_state,
                user_text=text,
                assistant_text=response.text,
            )
            return await self._finalize_response(user, text, response)

