"""SQLite-backed store of publications, note lifecycle and generated notes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
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

NOTE_STATUS_READY = "ready"
"""Note is generated but not published yet."""
NOTE_STATUS_IN_REVIEW = "in_review"
"""Note was sent to the moderator and awaits a decision."""
NOTE_STATUS_PUBLISHED = "published"
"""Note was published to a channel."""
NOTE_STATUS_REJECTED = "rejected"
"""Note was rejected by the moderator."""

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
    note_updated_utc TEXT,
    full_text        TEXT
)
"""

_SCHEMA_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key     TEXT NOT NULL,
    note_text     TEXT NOT NULL,
    model         TEXT,
    created_utc   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'ready',
    channel       TEXT,
    published_utc TEXT
)
"""


@dataclass
class ReadyNote:
    """A generated note bundled with its paper for publishing."""

    note_id: int
    paper: PaperRecord
    note_text: str


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
        if "full_text" not in columns:
            self._conn.execute("ALTER TABLE seen_papers ADD COLUMN full_text TEXT")
        note_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(notes)")}
        if "status" not in note_columns:
            self._conn.execute("ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'")
        if "channel" not in note_columns:
            self._conn.execute("ALTER TABLE notes ADD COLUMN channel TEXT")
        if "published_utc" not in note_columns:
            self._conn.execute("ALTER TABLE notes ADD COLUMN published_utc TEXT")

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

    def notes_ready(self, limit: int = 10) -> list[ReadyNote]:
        """Generated notes that are not published yet, oldest first."""
        rows = self._conn.execute(
            "SELECT n.id, n.note_text, p.record_json FROM notes n"
            " JOIN seen_papers p ON p.dedup_key = n.dedup_key"
            " WHERE n.status = ? AND p.record_json IS NOT NULL"
            " ORDER BY n.id LIMIT ?",
            (NOTE_STATUS_READY, limit),
        )
        result: list[ReadyNote] = []
        for note_id, note_text, raw in rows:
            try:
                paper = PaperRecord.model_validate_json(raw)
            except ValueError:
                continue
            result.append(ReadyNote(note_id=int(note_id), paper=paper, note_text=note_text))
        return result

    def notes_in_review(self, limit: int = 50) -> list[tuple[int, str]]:
        """Notes awaiting a moderator decision as (id, text) pairs."""
        rows = self._conn.execute(
            "SELECT id, note_text FROM notes WHERE status = ? ORDER BY id LIMIT ?",
            (NOTE_STATUS_IN_REVIEW, limit),
        ).fetchall()
        return [(int(row[0]), row[1]) for row in rows]

    def note_by_id(self, note_id: int) -> ReadyNote | None:
        """Fetch a single note with its paper; None when missing or broken."""
        row = self._conn.execute(
            "SELECT n.note_text, p.record_json FROM notes n"
            " JOIN seen_papers p ON p.dedup_key = n.dedup_key WHERE n.id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            paper = PaperRecord.model_validate_json(row[1])
        except ValueError:
            return None
        return ReadyNote(note_id=note_id, paper=paper, note_text=row[0])

    def mark_note_published(self, note_id: int, channel: str) -> None:
        """Mark a note as published to the given channel."""
        self._conn.execute(
            "UPDATE notes SET status = ?, channel = ?, published_utc = ? WHERE id = ?",
            (NOTE_STATUS_PUBLISHED, channel, datetime.now(UTC).isoformat(), note_id),
        )
        self._conn.commit()

    def mark_note_publish_status(self, note_id: int, status: str) -> None:
        """Update the publishing status of a note (in_review / rejected)."""
        self._conn.execute(
            "UPDATE notes SET status = ? WHERE id = ?",
            (status, note_id),
        )
        self._conn.commit()

    def set_full_text(self, paper: PaperRecord, full_text: str) -> None:
        """Cache the extracted full article text for later note runs."""
        self._conn.execute(
            "UPDATE seen_papers SET full_text = ? WHERE dedup_key = ?",
            (full_text, dedup_key(paper)),
        )
        self._conn.commit()

    def get_full_text(self, key: str) -> str | None:
        """Cached full article text for the paper, if it was fetched before."""
        row = self._conn.execute(
            "SELECT full_text FROM seen_papers WHERE dedup_key = ?", (key,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def stats(self) -> dict[str, int]:
        """Aggregate counters for the operator console."""

        def one(sql: str) -> int:
            return int(self._conn.execute(sql).fetchone()[0])

        return {
            "papers": one("SELECT COUNT(*) FROM seen_papers"),
            "papers_pending_note": one(
                "SELECT COUNT(*) FROM seen_papers"
                " WHERE note_status IN ('seen', 'note_failed')"
                " AND record_json IS NOT NULL"
            ),
            "notes_ready": one("SELECT COUNT(*) FROM notes WHERE status = 'ready'"),
            "notes_in_review": one("SELECT COUNT(*) FROM notes WHERE status = 'in_review'"),
            "notes_published": one("SELECT COUNT(*) FROM notes WHERE status = 'published'"),
            "notes_rejected": one("SELECT COUNT(*) FROM notes WHERE status = 'rejected'"),
        }

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
