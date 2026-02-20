from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.llm.base import LLMClient


class HTTPLLMClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 20.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def complete(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 40,
            "max_tokens": 512,
            "repeat_penalty": 1.2,
            "stream": False,
            "use_dynamic_context": False,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}/api/chat", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = data.get("response")
        if not isinstance(content, str):
            msg = "LLM response has no string 'response'"
            raise ValueError(msg)
        return content