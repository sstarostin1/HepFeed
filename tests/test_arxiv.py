"""Tests for arXiv Atom parsing, id handling, window filter and API client."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from hepfeed.ingestion.arxiv import (
    ArxivClient,
    build_search_query,
    filter_published_within,
    parse_atom_feed,
    split_arxiv_id,
)
from hepfeed.ingestion.models import PaperRecord

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arxiv_atom.xml"


def test_build_search_query() -> None:
    assert build_search_query(["hep-ex", "hep-ph"]) == "cat:hep-ex OR cat:hep-ph"


@pytest.mark.parametrize(
    ("value", "base", "version"),
    [
        ("2609.01234", "2609.01234", None),
        ("2609.01234v2", "2609.01234", "v2"),
        ("http://arxiv.org/abs/2609.01234v2", "2609.01234", "v2"),
        ("https://arxiv.org/pdf/2609.01234", "2609.01234", None),
        ("hep-ex/0601001", "hep-ex/0601001", None),
        ("hep-ex/0601001v3", "hep-ex/0601001", "v3"),
    ],
)
def test_split_arxiv_id(value: str, base: str, version: str | None) -> None:
    assert split_arxiv_id(value) == (base, version)


def test_split_arxiv_id_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        split_arxiv_id("not-an-id")


def test_parse_atom_feed() -> None:
    records = parse_atom_feed(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(records) == 2

    first = records[0]
    assert first.arxiv_id == "2609.01234"
    assert first.arxiv_version == "v2"
    assert first.title == "Measurement of something"
    assert first.abstract == "We present a measurement."
    assert first.authors == ["Alice Alpha", "Bob Beta"]
    assert first.primary_category == "hep-ex"
    assert first.categories == ["hep-ex", "hep-ph"]
    assert first.doi == "10.1103/PhysRevD.114.012345"
    assert first.pdf_url == "http://arxiv.org/pdf/2609.01234v2"
    assert first.abs_url == "https://arxiv.org/abs/2609.01234v2"
    assert first.published == datetime(2026, 9, 13, 18, 0, 0, tzinfo=UTC)

    second = records[1]
    assert second.arxiv_id == "2609.05678"
    assert second.arxiv_version is None
    assert second.doi is None
    assert second.pdf_url == "https://arxiv.org/pdf/2609.05678"
    assert second.abs_url == "https://arxiv.org/abs/2609.05678"


def test_filter_published_within() -> None:
    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    fresh = PaperRecord(arxiv_id="1", title="t", published=now - timedelta(hours=1))
    stale = PaperRecord(arxiv_id="2", title="t", published=now - timedelta(hours=30))
    undated = PaperRecord(arxiv_id="3", title="t")
    result = filter_published_within([fresh, stale, undated], hours=24.0, now=now)
    assert [record.arxiv_id for record in result] == ["1"]


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.url.params["search_query"] == "cat:hep-ph"
    assert request.url.params["sortBy"] == "submittedDate"
    assert request.url.params["sortOrder"] == "descending"
    assert request.url.params["max_results"] == "2"
    return httpx.Response(200, text=FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fetch_recent_parses_response() -> None:
    async def scenario() -> list[PaperRecord]:
        transport = httpx.MockTransport(_handler)
        async with ArxivClient(
            client=httpx.AsyncClient(transport=transport),
            request_interval_seconds=0.0,
        ) as client:
            return await client.fetch_recent(categories=["hep-ph"], max_results=2)

    records = asyncio.run(scenario())
    assert len(records) == 2
    assert records[0].arxiv_id == "2609.01234"


def test_fetch_recent_retries_on_429() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, text=FIXTURE_PATH.read_text(encoding="utf-8"))

    async def scenario() -> list[PaperRecord]:
        transport = httpx.MockTransport(handler)
        async with ArxivClient(
            client=httpx.AsyncClient(transport=transport),
            request_interval_seconds=0.0,
            max_attempts=3,
            retry_base_seconds=0.0,
        ) as client:
            return await client.fetch_recent(categories=["hep-ph"], max_results=2)

    records = asyncio.run(scenario())
    assert calls["count"] == 3
    assert len(records) == 2


def test_fetch_recent_raises_after_final_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async def scenario() -> list[PaperRecord]:
        transport = httpx.MockTransport(handler)
        async with ArxivClient(
            client=httpx.AsyncClient(transport=transport),
            request_interval_seconds=0.0,
            max_attempts=2,
            retry_base_seconds=0.0,
        ) as client:
            return await client.fetch_recent(categories=["hep-ph"], max_results=2)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(scenario())


def test_fetch_recent_retries_on_transport_error() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ReadTimeout("simulated slow response", request=request)
        return httpx.Response(200, text=FIXTURE_PATH.read_text(encoding="utf-8"))

    async def scenario() -> list[PaperRecord]:
        transport = httpx.MockTransport(handler)
        async with ArxivClient(
            client=httpx.AsyncClient(transport=transport),
            request_interval_seconds=0.0,
            max_attempts=3,
            retry_base_seconds=0.0,
        ) as client:
            return await client.fetch_recent(categories=["hep-ph"], max_results=2)

    records = asyncio.run(scenario())
    assert calls["count"] == 2
    assert len(records) == 2
