from __future__ import annotations

import asyncio

from app.services.assistant.assistant_response import AssistantResponse, QuickAction
from app.services.assistant.bot_response_service import BotResponseService
from app.services.parser.command_parser_service import CommandParserService
from app.services.smart_agents import UserMemoryAgent


class ResponseOrchestrationService:
    def __init__(
        self,
        *,
        parser: CommandParserService,
        response_renderer: BotResponseService | None,
        memory: UserMemoryAgent,
    ) -> None:
        self._parser = parser
        self._response_renderer = response_renderer
        self._memory = memory

    async def finalize_response(
        self,
        *,
        user: object,
        source_text: str | None,
        response: AssistantResponse,
    ) -> AssistantResponse:
        from app.db.models import User

        if not isinstance(user, User):
            return response
        if self._response_renderer is None:
            return response

        response_kind = self._response_kind(response)
        can_offer_choices = (
            source_text is not None
            and response.ambiguity is None
            and response.confirmation is None
            and not response.quick_actions
        )
        if can_offer_choices:
            rendered_text, _ = await asyncio.gather(
                self._response_renderer.render_for_user(
                    user=user,
                    raw_text=response.text,
                    response_kind=response_kind,
                    user_text=source_text,
                ),
                self._maybe_attach_quick_choices(
                    user=user,
                    source_text=source_text,
                    response=response,
                    response_kind=response_kind,
                ),
            )
            response.text = rendered_text
        else:
            response.text = await self._response_renderer.render_for_user(
                user=user,
                raw_text=response.text,
                response_kind=response_kind,
                user_text=source_text,
            )

        if response.confirmation is not None:
            response.confirmation.summary = await self._response_renderer.render_for_user(
                user=user,
                raw_text=response.confirmation.summary,
                response_kind="confirmation_summary",
                user_text=source_text,
            )
        if response.quick_actions:
            labels = await asyncio.gather(
                *[
                    self._response_renderer.render_for_user(
                        user=user,
                        raw_text=item.label,
                        response_kind="button_label",
                        user_text=source_text,
                    )
                    for item in response.quick_actions
                ],
            )
            for item, label in zip(response.quick_actions, labels, strict=False):
                item.label = label
        return response

    async def _maybe_attach_quick_choices(
        self,
        *,
        user: object,
        source_text: str | None,
        response: AssistantResponse,
        response_kind: str,
    ) -> None:
        from app.db.models import User

        if not isinstance(user, User):
            return
        memory = self._memory.build_profile(user)
        context: dict[str, object] | None = None
        if source_text:
            context = {
                "latest_user_text": source_text,
                "response_kind": response_kind,
            }
        options = await self._parser.suggest_quick_replies(
            reply_text=response.text,
            locale=user.language,
            timezone=user.timezone,
            user_memory=memory,
            context=context,
        )
        if len(options) < 2:
            return
        response.quick_actions = [
            QuickAction(
                label=option,
                action="send_text_choice",
                payload={"text": option},
            )
            for option in options[:3]
        ]

    def _response_kind(self, response: AssistantResponse) -> str:
        if response.ambiguity is not None:
            return "ambiguity_choice"
        if response.confirmation is not None:
            return "confirmation_request"
        if "уточните" in response.text.lower():
            return "clarification_question"
        return "regular_reply"
