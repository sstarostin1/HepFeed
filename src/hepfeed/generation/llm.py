"""Minimal async client for an OpenAI-compatible chat completions API.

Targets Polza.AI (docs/CONCEPT.md, section 7): ``POST {base_url}/chat/completions``
with a Bearer token. Transient failures (HTTP 429/5xx and transport errors)
are retried with exponential backoff; hard failures (including truncated
reasoning with empty content) move the request down the model fallback chain.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MODEL_CHAIN: tuple[str, ...] = (
    "deepseek/deepseek-v4-flash-0731@provider=open-inference/fp8",
    "deepseek/deepseek-v4-flash-0731@provider=baidu/fp8",
    "deepseek/deepseek-v4-flash-0731@provider=deepseek/fp8",
)
"""Primary model followed by fallbacks, in decreasing priority."""


class LLMError(RuntimeError):
    """Raised when the LLM API fails after retries or returns an unexpected payload."""


class LLMClient:
    """Async chat-completions client with polite retries and model fallbacks.

    The real key is never hardcoded: it comes from settings/env
    (see .env.example and docs/SECURITY_NOTE.md).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://polza.ai/api/v1",
        models: Sequence[str] = DEFAULT_MODEL_CHAIN,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 300.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 3.0,
    ) -> None:
        self._api_key = api_key
        self._models = list(models)
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
        )

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> str:
        """Run one chat completion across the model chain and return the content.

        Each model gets its own retry budget for transient errors; any hard
        failure (including truncated reasoning with empty content) moves the
        request down the fallback chain.
        """
        base_payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        failures: list[str] = []
        for model in self._models:
            payload: dict[str, object] = {**base_payload, "model": model}
            try:
                return await self._complete_with_retries(payload)
            except LLMError as exc:
                logger.warning("LLM model failed, falling back: %s", exc)
                failures.append(f"{model}: {exc}")
        raise LLMError("all LLM models failed: " + " | ".join(failures))

    async def _complete_with_retries(self, payload: dict[str, object]) -> str:
        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except httpx.TransportError as exc:
                last_error = f"transport error {type(exc).__name__}: {exc}"
                retriable = True
            else:
                if response.status_code == 200:
                    return self._extract_content(response)
                retriable = response.status_code in _RETRYABLE_STATUS_CODES
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if not retriable or attempt == self._max_attempts:
                break
            delay = self._retry_base_seconds * 2 ** (attempt - 1)
            logger.warning(
                "LLM API failure (%s), attempt %d/%d, retrying in %.0fs",
                last_error,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
        raise LLMError(f"LLM request failed after {self._max_attempts} attempt(s): {last_error}")

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"unexpected LLM response payload: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            reasoning = choice.get("message", {}).get("reasoning_content")
            hint = (
                "reasoning present - final answer likely truncated by max_tokens"
                if reasoning
                else "no content in message"
            )
            raise LLMError(
                f"LLM returned empty content ({hint}); payload preview: {str(data)[:300]}"
            )
        return content.strip()
