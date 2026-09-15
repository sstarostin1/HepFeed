"""Tests for publishing statuses in the notes table."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import NOTE_STATUS_REJECTED, SeenStore


def _paper(arxiv_id: str) -> PaperRecord:
    return PaperRecord(arxiv_id=arxiv_id, title=f"P {arxiv_id}", abstract="Abs.")


def _seed(db_path: Path, arxiv_id: str) -> None:
    store = SeenStore(db_path)
    paper = _paper(arxiv_id)
    store.mark_seen(paper)
    store.save_note(paper, f"note {arxiv_id}", "model-x")
    store.close()


def test_notes_ready_and_publish(tmp_path: Path) -> None:
    db = tmp_path / "hepfeed.sqlite"
    _seed(db, "2609.00101")
    _seed(db, "2609.00102")

    store = SeenStore(db)
    try:
        ready = store.notes_ready()
        assert [note.note_id for note in ready] == [1, 2]
        assert ready[0].paper.arxiv_id == "2609.00101"
        store.mark_note_published(1, "@hep_test")
        assert [note.note_id for note in store.notes_ready()] == [2]
        note = store.note_by_id(1)
        assert note is not None and note.note_text == "note 2609.00101"
    finally:
        store.close()


def test_reject_and_review(tmp_path: Path) -> None:
    db = tmp_path / "hepfeed.sqlite"
    _seed(db, "2609.00103")
    store = SeenStore(db)
    try:
        store.mark_note_publish_status(1, "in_review")
        assert store.notes_in_review() == [(1, "note 2609.00103")]
        store.mark_note_publish_status(1, NOTE_STATUS_REJECTED)
        assert store.notes_ready() == []
        assert store.notes_in_review() == []
    finally:
        store.close()


def test_notes_table_migration(tmp_path: Path) -> None:
    db = tmp_path / "hepfeed.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE notes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL,"
        " note_text TEXT NOT NULL, model TEXT, created_utc TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = SeenStore(db)
    try:
        _seed(db, "2609.00104")
        ready = store.notes_ready()
        assert ready and ready[0].note_text == "note 2609.00104"
    finally:
        store.close()
