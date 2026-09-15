"""SQLite-backed store of publications, note lifecycle and generated notes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hepfeed.ingestion.dedup import dedup_key
from hepfeed.ingestion.models import PaperRecord

STATUS_SEEN = "seen"
"""Paper was discovered but has no note yet."""
STATUS_NOTE_CREATED = "note_created"
"""A note was generated and stored."""
STATUS_NOTE_FAILED = "note_failed"
"""A note attempt failed; the paper stays eligible for retry."""

_PENDING_FOR_NOTE = (STATUS_SEEN, STATUS_NOTE_FAILED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_papers (
    dedup_key        TEXT PRIMARY KEY,
    arxiv_id         TEXT,
    title            TEXT,
    source           TEXT NOT NULL DEFAULT 'arxiv',
    first_seen_utc   TEXT NOT NULL,
    record_json      TEXT,
    note_status      TEXT NOT NULL DEFAULT 'seen',
    note_updated_utc TEXT
)
"""

_SCHEMA_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key   TEXT NOT NULL,
    note_text   TEXT NOT NULL,
    model       TEXT,
    created_utc TEXT NOT NULL
)
"""


class SeenStore:
    """Persistent store of papers, their note lifecycle and generated notes.

    A paper is not "done" until its note exists (``note_created``); papers
    without a note — including failed attempts — stay eligible for the
    generation cycle (docs/CONCEPT.md, section 6.1).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_SCHEMA)
        self._conn.execute(_SCHEMA_NOTES)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first release, if missing."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(seen_papers)")}
        if "record_json" not in columns:
            self._conn.execute("ALTER TABLE seen_papers ADD COLUMN record_json TEXT")
        if "note_status" not in columns:
            self._conn.execute(
                "ALTER TABLE seen_papers ADD COLUMN note_status TEXT NOT NULL DEFAULT 'seen'"
            )
        if "note_updated_utc" not in columns:
            self._conn.execute("ALTER TABLE seen_papers ADD COLUMN note_updated_utc TEXT")

    def is_seen(self, key: str) -> bool:
        """Return True when the dedup key was stored before."""
        row = self._conn.execute("SELECT 1 FROM seen_papers WHERE dedup_key = ?", (key,)).fetchone()
        return row is not None

    def mark_seen(self, paper: PaperRecord) -> bool:
        """Record the paper; return True when it was new, False when already stored."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO seen_papers"
            " (dedup_key, arxiv_id, title, source, first_seen_utc, record_json, note_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dedup_key(paper),
                paper.arxiv_id,
                paper.title,
                paper.source,
                datetime.now(UTC).isoformat(),
                paper.model_dump_json(),
                STATUS_SEEN,
            ),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def pending_for_note(self, limit: int = 10) -> list[PaperRecord]:
        """Papers without a note yet, oldest first (failed attempts included)."""
        rows = self._conn.execute(
            "SELECT record_json FROM seen_papers"
            " WHERE note_status IN (?, ?) AND record_json IS NOT NULL"
            " ORDER BY first_seen_utc LIMIT ?",
            (*_PENDING_FOR_NOTE, limit),
        )
        records: list[PaperRecord] = []
        for (raw,) in rows:
            try:
                records.append(PaperRecord.model_validate_json(raw))
            except ValueError:
                continue
        return records

    def pending_count(self) -> int:
        """Number of papers still waiting for a note."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM seen_papers"
            " WHERE note_status IN (?, ?) AND record_json IS NOT NULL",
            _PENDING_FOR_NOTE,
        ).fetchone()
        return int(row[0])

    def mark_note_status(self, paper: PaperRecord, status: str) -> None:
        """Update the note lifecycle status of a paper."""
        self._conn.execute(
            "UPDATE seen_papers SET note_status = ?, note_updated_utc = ? WHERE dedup_key = ?",
            (status, datetime.now(UTC).isoformat(), dedup_key(paper)),
        )
        self._conn.commit()

    def save_note(self, paper: PaperRecord, note_text: str, model: str) -> None:
        """Persist a generated note."""
        self._conn.execute(
            "INSERT INTO notes (dedup_key, note_text, model, created_utc) VALUES (?, ?, ?, ?)",
            (dedup_key(paper), note_text, model, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def count(self) -> int:
        """Number of stored papers (handy for tests and diagnostics)."""
        row = self._conn.execute("SELECT COUNT(*) FROM seen_papers").fetchone()
        return int(row[0])

    def count_notes(self) -> int:
        """Number of stored notes (handy for tests and diagnostics)."""
        row = self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
