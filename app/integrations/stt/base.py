from typing import Protocol


class STTClient(Protocol):
    async def transcribe(self, audio: bytes, filename: str) -> str:
        ...
