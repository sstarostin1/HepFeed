"""arXiv Atom API client, feed parsing and freshness filtering.

Specification: docs/SOURCES.md, section 2.1. The API needs no authentication
but expects polite pacing (roughly one request per 3 seconds).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import httpx

from hepfeed.ingestion.models import PaperRecord

ARXIV_API_URL = "https://export.arxiv.org/api/query"
"""Atom endpoint of the arXiv API (docs/SOURCES.md, section 2.1)."""

DEFAULT_CATEGORIES: tuple[str, ...] = ("hep-ex", "hep-ph", "physics.acc-ph")
"""Categories monitored in Phase 1 of the roadmap (docs/CONCEPT.md, section 12)."""

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_ARXIV_ID_RE = re.compile(
    r"^(?P<base>[a-z-]+/\d{7}|\d{4}\.\d{4,5})(?:v(?P<ver>\d+))?$",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def build_search_query(categories: Sequence[str]) -> str:
    """Join categories into an arXiv API ``search_query`` expression."""
    return " OR ".join(f"cat:{category}" for category in categories)


def split_arxiv_id(value: str) -> tuple[str, str | None]:
    """Extract ``(base_id, version)`` from an arXiv id or an abs/pdf URL."""
    stripped = value.strip()
    candidates = (
        stripped,
        stripped.rstrip("/").split("/")[-1],
    )
    for candidate in candidates:
        match = _ARXIV_ID_RE.match(candidate)
        if match is not None:
            base = match.group("base").lower()
            version = f"v{match.group('ver')}" if match.group("ver") else None
            return base, version
    msg = f"cannot parse arXiv id from {value!r}"
    raise ValueError(msg)


def parse_atom_feed(xml_text: str) -> list[PaperRecord]:
    """Parse an arXiv Atom feed into paper records."""
    root = ET.fromstring(xml_text)
    return [_parse_entry(entry) for entry in root.findall(f"{_ATOM}entry")]


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _parse_entry(entry: ET.Element) -> PaperRecord:
    base_id, version = split_arxiv_id(_text(entry.find(f"{_ATOM}id")))

    categories: list[str] = []
    for element in entry.findall(f"{_ATOM}category"):
        term = element.get("term")
        if term and term not in categories:
            categories.append(term)

    primary = entry.find(f"{_ARXIV_NS}primary_category")
    primary_category = primary.get("term") if primary is not None else None

    pdf_url: str | None = None
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("title") == "pdf" and link.get("href"):
            pdf_url = link.get("href")

    return PaperRecord(
        arxiv_id=base_id,
        arxiv_version=version,
        title=_text(entry.find(f"{_ATOM}title")),
        abstract=_text(entry.find(f"{_ATOM}summary")),
        authors=[_text(author.find(f"{_ATOM}name")) for author in entry.findall(f"{_ATOM}author")],
        primary_category=primary_category,
        categories=categories,
        doi=_text(entry.find(f"{_ARXIV_NS}doi")) or None,
        published=_parse_datetime(_text(entry.find(f"{_ATOM}published"))),
        updated=_parse_datetime(_text(entry.find(f"{_ATOM}updated"))),
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{base_id}",
        abs_url=f"https://arxiv.org/abs/{base_id}{version or ''}",
    )


def filter_published_within(
    records: Sequence[PaperRecord],
    hours: float,
    now: datetime | None = None,
) -> list[PaperRecord]:
    """Keep records whose publication timestamp falls into the last ``hours``.

    arXiv ``published`` is the v1 submission timestamp; replacements and
    cross-lists may need ``updated``-based handling later (docs/SOURCES.md,
    section 2.1).
    """
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(hours=hours)
    return [
        record for record in records if record.published is not None and record.published >= cutoff
    ]


class ArxivClient:
    """Thin async client for the arXiv Atom API with polite pacing."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        request_interval_seconds: float = 3.0,
        timeout_seconds: float = 30.0,
        max_attempts: int = 4,
        retry_base_seconds: float = 3.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "hepfeed/0.1 (arXiv ingestion)"},
        )
        self._request_interval = request_interval_seconds
        self._last_request: float | None = None
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds

    async def __aenter__(self) -> ArxivClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def _throttle(self) -> None:
        now = time.monotonic()
        if self._last_request is not None:
            wait = self._request_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def fetch_recent(
        self,
        categories: Sequence[str] = DEFAULT_CATEGORIES,
        max_results: int = 100,
    ) -> list[PaperRecord]:
        """Fetch the newest submissions in the given categories.

        Transient failures (HTTP 429/5xx and transport errors) are retried
        with exponential backoff: arXiv throttles clients that burst requests.
        """
        for attempt in range(1, self._max_attempts + 1):
            await self._throttle()
            try:
                response = await self._client.get(
                    ARXIV_API_URL,
                    params={
                        "search_query": build_search_query(categories),
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                        "max_results": str(max_results),
                    },
                )
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    raise
                delay = self._retry_base_seconds * 2 ** (attempt - 1)
                logger.warning(
                    "arXiv API transport error: %s (attempt %d/%d), retrying in %.0fs",
                    exc,
                    attempt,
                    self._max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return parse_atom_feed(response.text)
            if attempt == self._max_attempts:
                break
            delay = self._retry_base_seconds * 2 ** (attempt - 1)
            logger.warning(
                "arXiv API returned HTTP %d (attempt %d/%d), retrying in %.0fs",
                response.status_code,
                attempt,
                self._max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
        response.raise_for_status()
        return parse_atom_feed(response.text)
