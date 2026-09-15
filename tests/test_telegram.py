"""Tests for the Telegram Bot API client (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from hepfeed.publishing.telegram import TelegramClient, TelegramError


def _client(handler, **kwargs: object) -> TelegramClient:
    transport = httpx.MockTransport(handler)
    return TelegramClient(
        "test-token",
        client=httpx.AsyncClient(transport=transport, base_url="https://tg.test"),
        **kwargs,  # type: ignore[arg-type]
    )


def test_send_message_returns_message_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["chat_id"] = json.loads(request.content)["chat_id"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async def scenario() -> int:
        async with _client(handler) as tg:
            return await tg.send_message("@channel", "hello")

    assert asyncio.run(scenario()) == 42
    assert seen["path"] == "/bottest-token/sendMessage"
    assert seen["chat_id"] == "@channel"


def test_send_message_respects_retry_after() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async def scenario() -> int:
        async with _client(handler, max_attempts=3) as tg:
            return await tg.send_message("-100123", "hello")

    assert asyncio.run(scenario()) == 7
    assert calls["count"] == 2


def test_send_message_raises_on_non_retryable() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"ok": False, "description": "chat not found"})

    async def scenario() -> int:
        async with _client(handler) as tg:
            return await tg.send_message("@nowhere", "hello")

    with pytest.raises(TelegramError, match="HTTP 400"):
        asyncio.run(scenario())
    assert calls["count"] == 1


def test_send_message_raises_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False})

    async def scenario() -> int:
        async with _client(handler, max_attempts=2, retry_base_seconds=0.0) as tg:
            return await tg.send_message("@channel", "hello")

    with pytest.raises(TelegramError, match="HTTP 500"):
        asyncio.run(scenario())
