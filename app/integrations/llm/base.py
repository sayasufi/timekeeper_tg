from typing import Protocol


class LLMClient(Protocol):
    async def complete(self, prompt: str) -> str:
        ...
