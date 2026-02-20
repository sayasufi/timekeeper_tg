from __future__ import annotations

from html import escape
from typing import ClassVar

from app.db.models import User
from app.integrations.llm.base import LLMClient
from app.services.smart_agents import BotReplyAgent, TelegramFormattingAgent, UserMemoryAgent


class BotResponseService:
    _NO_FORMAT_KINDS: ClassVar[frozenset[str]] = frozenset({"button_label", "reminder_notification"})

    def __init__(self, llm_client: LLMClient, min_confidence: float = 0.6) -> None:
        self._agent = BotReplyAgent(llm_client)
        self._formatter = TelegramFormattingAgent(llm_client)
        self._memory = UserMemoryAgent()
        self._min_confidence = min_confidence

    async def render_for_user(
        self,
        *,
        user: User,
        raw_text: str,
        response_kind: str,
        user_text: str | None = None,
    ) -> str:
        memory = self._memory.to_prompt_context(self._memory.build_profile(user))
        return await self.render(
            raw_text=raw_text,
            locale=user.language,
            timezone=user.timezone,
            response_kind=response_kind,
            user_text=user_text,
            user_memory=memory,
        )

    async def render(
        self,
        *,
        raw_text: str,
        locale: str,
        timezone: str,
        response_kind: str,
        user_text: str | None = None,
        user_memory: dict[str, object] | None = None,
    ) -> str:
        # Обход LLM для системных подтверждений с датами — избегаем искажения дат
        if response_kind == "regular_reply" and raw_text.startswith("Напоминание создано:"):
            text = raw_text.strip()
        elif response_kind == "reminder_notification":
            # Готовый HTML-формат из ReminderDispatchService
            text = raw_text.strip()
        else:
            try:
                rendered = await self._agent.render(
                    raw_text=raw_text,
                    user_text=user_text,
                    locale=locale,
                    timezone=timezone,
                    response_kind=response_kind,
                    user_memory=user_memory,
                )
            except Exception:
                return self._safe_plain(raw_text, response_kind=response_kind)

            text = (rendered.text or "").strip()
            if rendered.confidence < self._min_confidence or not text:
                text = raw_text

        if response_kind in self._NO_FORMAT_KINDS:
            return text

        try:
            formatted = await self._formatter.format(
                text=text,
                response_kind=response_kind,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
            )
            formatted_text = (formatted.text or "").strip()
            if formatted.confidence >= self._min_confidence and formatted_text:
                return formatted_text
        except Exception:
            pass

        return self._safe_plain(text, response_kind=response_kind)

    def _safe_plain(self, text: str, *, response_kind: str) -> str:
        if response_kind in self._NO_FORMAT_KINDS:
            return text
        return escape(text, quote=False)
