"""Tests for the OpenAI-compatible LLM client (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from hepfeed.generation.llm import LLMClient, LLMError

_BASE_URL = "https://llm.test/v1"


def _client(handler, **kwargs: object) -> LLMClient:
    transport = httpx.MockTransport(handler)
    return LLMClient(
        "test-key",
        base_url=_BASE_URL,
        client=httpx.AsyncClient(transport=transport, base_url=_BASE_URL),
        **kwargs,  # type: ignore[arg-type]
    )


def _ok_body(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_complete_returns_content() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["path"] = request.url.path
        seen["model"] = json.loads(request.content)["model"]
        return _ok_body(" готовая заметка ")

    async def scenario() -> str:
        async with _client(handler) as llm:
            return await llm.complete("system", "user")

    assert asyncio.run(scenario()) == "готовая заметка"
    assert seen["auth"] == "Bearer test-key"
    assert seen["path"] == "/v1/chat/completions"
    assert isinstance(seen["model"], str) and "deepseek" in seen["model"]


def test_complete_retries_on_429() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            return httpx.Response(429)
        return _ok_body("ok")

    async def scenario() -> str:
        async with _client(handler, max_attempts=3, retry_base_seconds=0.0) as llm:
            return await llm.complete("s", "u")

    assert asyncio.run(scenario()) == "ok"
    assert calls["count"] == 2


def test_complete_raises_on_non_retryable_status() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, text="bad request")

    async def scenario() -> str:
        async with _client(handler) as llm:
            return await llm.complete("s", "u")

    with pytest.raises(LLMError, match="HTTP 400"):
        asyncio.run(scenario())
    assert calls["count"] == 1


def test_complete_raises_after_exhausted_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def scenario() -> str:
        async with _client(handler, max_attempts=2, retry_base_seconds=0.0) as llm:
            return await llm.complete("s", "u")

    with pytest.raises(LLMError, match="after 2 attempt"):
        asyncio.run(scenario())


def test_complete_raises_on_empty_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_body("   ")

    async def scenario() -> str:
        async with _client(handler) as llm:
            return await llm.complete("s", "u")

    with pytest.raises(LLMError, match="empty content"):
        asyncio.run(scenario())
