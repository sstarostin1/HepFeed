"""Tests for note lifecycle tracking in the seen-store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import STATUS_NOTE_CREATED, STATUS_NOTE_FAILED, SeenStore


def _paper(arxiv_id: str) -> PaperRecord:
    return PaperRecord(arxiv_id=arxiv_id, title=f"Paper {arxiv_id}", abstract="Abs.")


def test_new_paper_is_pending_for_note(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        store.mark_seen(_paper("2609.00001"))
        pending = store.pending_for_note()
        assert [record.arxiv_id for record in pending] == ["2609.00001"]
        assert store.pending_count() == 1
    finally:
        store.close()


def test_created_note_leaves_queue(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        paper = _paper("2609.00002")
        store.mark_seen(paper)
        store.save_note(paper, "note text", "model-x")
        store.mark_note_status(paper, STATUS_NOTE_CREATED)
        assert store.pending_for_note() == []
        assert store.pending_count() == 0
        assert store.count_notes() == 1
    finally:
        store.close()


def test_failed_note_stays_eligible(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        paper = _paper("2609.00003")
        store.mark_seen(paper)
        store.mark_note_status(paper, STATUS_NOTE_FAILED)
        assert [record.arxiv_id for record in store.pending_for_note()] == ["2609.00003"]
        assert store.pending_count() == 1
    finally:
        store.close()


def test_legacy_db_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "hepfeed.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE seen_papers ("
        " dedup_key TEXT PRIMARY KEY, arxiv_id TEXT, title TEXT,"
        " source TEXT NOT NULL DEFAULT 'arxiv', first_seen_utc TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO seen_papers (dedup_key, arxiv_id, title, first_seen_utc)"
        " VALUES ('arxiv:2609.00009', '2609.00009', 'Legacy paper',"
        " '2026-09-15T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    store = SeenStore(db_path)
    try:
        store.mark_seen(_paper("2609.00010"))
        assert store.count() == 2
        # legacy row has no record_json: visible in count, skipped in queue
        ids = [record.arxiv_id for record in store.pending_for_note()]
        assert ids == ["2609.00010"]
        assert store.pending_count() == 1
    finally:
        store.close()


def test_stats_and_full_text_cache(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "hepfeed.sqlite")
    try:
        paper = PaperRecord(arxiv_id="2609.00111", title="T", abstract="A")
        store.mark_seen(paper)
        store.save_note(paper, "note", "m")
        store.set_full_text(paper, "full text body")
        stats = store.stats()
        assert stats["papers"] == 1
        assert stats["papers_pending_note"] == 1
        assert stats["notes_ready"] == 1
        assert stats["notes_published"] == 0
        assert store.get_full_text("arxiv:2609.00111") == "full text body"
    finally:
        store.close()
