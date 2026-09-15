"""Deduplication keys with the DOI -> arXiv ID -> title + first author priority
(docs/SOURCES.md, section 9.3)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from hepfeed.ingestion.models import PaperRecord

_DOI_PREFIXES = (
    "https://dx.doi.org/",
    "https://doi.org/",
    "http://dx.doi.org/",
    "http://doi.org/",
    "doi:",
    "doi.org/",
)


def normalize_doi(doi: str | None) -> str | None:
    """Lowercase a DOI and strip URL prefixes; return None when empty."""
    if not doi:
        return None
    value = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value or None


def normalize_title(value: str) -> str:
    """Casefold, drop punctuation and collapse whitespace."""
    collapsed = re.sub(r"[^\w\s]+", " ", value.casefold())
    return re.sub(r"\s+", " ", collapsed).strip()


def _title_author_key(paper: PaperRecord) -> str:
    first_author = paper.authors[0] if paper.authors else ""
    author = normalize_title(first_author)
    return f"title:{normalize_title(paper.title)}|author:{author}"


def dedup_key(paper: PaperRecord) -> str:
    """Canonical identity of a paper, DOI-first."""
    doi = normalize_doi(paper.doi)
    if doi:
        return f"doi:{doi}"
    if paper.arxiv_id:
        return f"arxiv:{paper.arxiv_id.lower()}"
    return _title_author_key(paper)


def deduplicate(records: Sequence[PaperRecord]) -> list[PaperRecord]:
    """Keep the first record per dedup key, preserving input order."""
    seen_keys: set[str] = set()
    result: list[PaperRecord] = []
    for record in records:
        key = dedup_key(record)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(record)
    return result
