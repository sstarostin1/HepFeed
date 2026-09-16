"""Tests for LLMClient extra_body merging (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx

from hepfeed.generation.llm import LLMClient

_BASE_URL = "https://llm.test/v1"


def _client(handler, **kwargs: object) -> LLMClient:
    transport = httpx.MockTransport(handler)
    params: dict[str, object] = {"models": ("test-model",)}
    params.update(kwargs)
    return LLMClient(
        "test-key",
        base_url=_BASE_URL,
        client=httpx.AsyncClient(transport=transport, base_url=_BASE_URL),
        **params,  # type: ignore[arg-type]
    )


def _ok_body(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_complete_merges_extra_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok_body("ok")

    async def scenario() -> str:
        async with _client(handler, extra_body={"reasoning_effort": "low"}) as llm:
            return await llm.complete("s", "u")

    assert asyncio.run(scenario()) == "ok"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body.get("reasoning_effort") == "low"
    assert body.get("model") == "test-model"
    assert body.get("max_tokens") == 2000


def test_complete_without_extra_body_has_no_reasoning_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok_body("ok")

    async def scenario() -> str:
        async with _client(handler) as llm:
            return await llm.complete("s", "u")

    assert asyncio.run(scenario()) == "ok"
    body = seen["body"]
    assert isinstance(body, dict)
    assert "reasoning_effort" not in body
