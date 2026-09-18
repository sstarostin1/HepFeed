"""Telegram Bot API client (sendMessage) with polite retries."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org"
"""Root of the Telegram Bot API."""


class TelegramError(RuntimeError):
    """Raised when the Telegram API fails after retries."""


class TelegramClient:
    """Async client for the Telegram Bot API with 429-aware retries.

    Transport-only: payloads are composed by the publishing cycle, and the
    operator UI (inline moderation buttons + callback answers) lives in the
    admin listener.
    """

    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = TELEGRAM_API_URL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 2.0,
    ) -> None:
        self._api_token = api_token
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, object] | None = None,
        parse_mode: str | None = None,
    ) -> int:
        """Send a message and return the created message id."""
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = await self._post_with_retries(f"/bot{self._api_token}/sendMessage", payload)
        return int(data["result"]["message_id"])

    async def _post_with_retries(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        """POST a Bot API call with 429/5xx-aware retries; return the parsed result."""
        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(url, json=payload)
            except httpx.TransportError as exc:
                last_error = f"transport error {type(exc).__name__}: {exc}"
                retriable = True
                retry_after: float | None = None
            else:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                if response.status_code == 200 and data.get("ok"):
                    return data
                retriable = response.status_code == 429 or response.status_code >= 500
                parameters = data.get("parameters") or {}
                retry_after = (
                    float(parameters["retry_after"])
                    if response.status_code == 429 and "retry_after" in parameters
                    else None
                )
                last_error = f"HTTP {response.status_code}: {str(data)[:200]}"
            if not retriable or attempt == self._max_attempts:
                break
            delay = (
                retry_after
                if retry_after is not None
                else self._retry_base_seconds * 2 ** (attempt - 1)
            )
            logger.warning(
                "Telegram API failure (%s), attempt %d/%d, retrying in %.0fs",
                last_error,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
        raise TelegramError(f"Telegram request failed: {last_error}")
