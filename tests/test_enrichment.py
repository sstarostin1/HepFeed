"""Tests for arXiv HTML full-text extraction (no network)."""

from __future__ import annotations

import asyncio

import httpx

from hepfeed.enrichment.arxiv_html import extract_text, fetch_full_text

_LONG = (
    "Measurements of the differential cross section were performed and the results "
    "are consistent with the standard model expectations over the whole kinematic "
    "range, while the observed limits exclude the previously allowed parameter "
    "region at the ninety-five percent confidence level. "
) * 12

FIXTURE = f"""
<html><head><style>.x{{}}</style></head><body>
<p>From CMB to LHC: A hybrid inflation model with gauged scale symmetry</p>
<div><p>We propose a hybrid inflation model based on gauged scale symmetry with
axion-like inflaton and SM Higgs waterfall field, tested against CMB and LHC
data over the full parameter space.</p></div>
<p>{_LONG}</p>
<p>Report GitHub Issuex</p>
<p>Short</p>
<p>License: arXiv.org perpetual non-exclusive license</p>
<p>Have a free development cycle? Help support accessibility at arXiv!</p>
</body></html>
"""


def test_extract_text_drops_furniture() -> None:
    text = extract_text(FIXTURE)
    assert "hybrid inflation model" in text
    assert "Report GitHub" not in text
    assert "License:" not in text
    assert "Short" not in text


def test_fetch_full_text_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FIXTURE)

    async def scenario() -> str | None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://arxiv.org") as client:
            return await fetch_full_text("2609.14318", client=client)

    text = asyncio.run(scenario())
    assert text is not None and "hybrid inflation" in text


def test_fetch_full_text_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def scenario() -> str | None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://arxiv.org") as client:
            return await fetch_full_text("9999.99999", client=client)

    assert asyncio.run(scenario()) is None


def test_fetch_full_text_too_short_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body><p>tiny page</p></body></html>")

    async def scenario() -> str | None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="https://arxiv.org") as client:
            return await fetch_full_text("1234.00001", client=client)

    assert asyncio.run(scenario()) is None
