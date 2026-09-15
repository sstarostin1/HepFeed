"""Tests for deduplication keys and in-run dedup (docs/SOURCES.md, section 9.3)."""

from __future__ import annotations

import pytest

from hepfeed.ingestion.dedup import (
    dedup_key,
    deduplicate,
    normalize_doi,
    normalize_title,
)
from hepfeed.ingestion.models import PaperRecord


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1103/PhysRevD.114.012345", "10.1103/physrevd.114.012345"),
        ("https://doi.org/10.1103/A.B", "10.1103/a.b"),
        ("http://dx.doi.org/10.1103/A.B", "10.1103/a.b"),
        ("doi:10.1103/A.B", "10.1103/a.b"),
        ("DOI.org/10.1103/A.B", "10.1103/a.b"),
        ("  10.1103/A.B  ", "10.1103/a.b"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw: str | None, expected: str | None) -> None:
    assert normalize_doi(raw) == expected


def test_normalize_title_strips_punctuation_and_case() -> None:
    value = "  Deep  Inelastic -- Scattering (at) HERA!  "
    assert normalize_title(value) == "deep inelastic scattering at hera"


def test_doi_takes_priority_over_arxiv_id() -> None:
    paper = PaperRecord(arxiv_id="2609.01234", title="T", doi="10.1103/PhysRevD.114.012345")
    assert dedup_key(paper) == "doi:10.1103/physrevd.114.012345"


def test_arxiv_id_fallback() -> None:
    paper = PaperRecord(arxiv_id="2609.01234", title="T")
    assert dedup_key(paper) == "arxiv:2609.01234"


def test_arxiv_id_fallback_is_case_insensitive() -> None:
    paper = PaperRecord(arxiv_id="HEP-EX/0601001", title="T")
    assert dedup_key(paper) == "arxiv:hep-ex/0601001"


def test_title_author_fallback() -> None:
    first = PaperRecord(arxiv_id=None, title="Search for X: part I", authors=["Alpha, A."])
    second = PaperRecord(arxiv_id=None, title="search  for x part i", authors=["alpha a"])
    assert dedup_key(first) == dedup_key(second)
    assert dedup_key(first).startswith("title:search for x part i|author:alpha a")


def test_deduplicate_keeps_first_occurrence() -> None:
    base = PaperRecord(arxiv_id="2609.01234", title="Same title", doi="10.1/x")
    clone = PaperRecord(arxiv_id="2609.99999", title="Same title", doi="https://doi.org/10.1/x")
    other = PaperRecord(arxiv_id="2609.05678", title="Other paper")
    result = deduplicate([base, other, clone])
    assert [paper.arxiv_id for paper in result] == ["2609.01234", "2609.05678"]
