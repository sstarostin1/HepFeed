"""Tests for the SQLite-backed seen-store."""

from __future__ import annotations

from pathlib import Path

from hepfeed.ingestion.dedup import dedup_key
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import SeenStore


def _paper(arxiv_id: str) -> PaperRecord:
    return PaperRecord(arxiv_id=arxiv_id, title=f"Paper {arxiv_id}")


def test_mark_seen_returns_true_once(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        paper = _paper("2609.01234")
        key = dedup_key(paper)
        assert store.is_seen(key) is False
        assert store.mark_seen(paper) is True
        assert store.is_seen(key) is True
        assert store.mark_seen(paper) is False
        assert store.count() == 1
    finally:
        store.close()


def test_state_persists_across_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "hepfeed.sqlite"
    first = SeenStore(db_path)
    first.mark_seen(_paper("2609.01234"))
    first.close()

    second = SeenStore(db_path)
    try:
        assert second.is_seen("arxiv:2609.01234") is True
        assert second.count() == 1
    finally:
        second.close()


def test_multiple_papers_tracked(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        for arxiv_id in ("2609.01234", "2609.05678", "2609.09090"):
            assert store.mark_seen(_paper(arxiv_id)) is True
        assert store.count() == 3
    finally:
        store.close()
