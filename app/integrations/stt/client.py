from __future__ import annotations

import mimetypes

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.stt.base import STTClient


class HTTPSTTClient(STTClient):
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 40.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def transcribe(self, audio: bytes, filename: str) -> str:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        files = {"file": (filename, audio, content_type)}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}/transcribe", headers=headers, files=files)
            if response.status_code in {400, 413, 500}:
                detail = response.json().get("detail", "STT service error")
                raise ValueError(f"STT error {response.status_code}: {detail}")
            response.raise_for_status()
            data = response.json()

        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            msg = "STT response has no non-empty string 'text'"
            raise ValueError(msg)
        return text.strip()