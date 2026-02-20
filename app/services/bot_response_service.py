from __future__ import annotations

from app.db.models import User
from app.integrations.llm.base import LLMClient
from app.services.smart_agents import BotReplyAgent, UserMemoryAgent


class BotResponseService:
    def __init__(self, llm_client: LLMClient, min_confidence: float = 0.6) -> None:
        self._agent = BotReplyAgent(llm_client)
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
            return raw_text

        text = (rendered.text or "").strip()
        if rendered.confidence < self._min_confidence or not text:
            return raw_text
        return text
