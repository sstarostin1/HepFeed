"""SQLite-backed store of already processed publications."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hepfeed.ingestion.dedup import dedup_key
from hepfeed.ingestion.models import PaperRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_papers (
    dedup_key      TEXT PRIMARY KEY,
    arxiv_id       TEXT,
    title          TEXT,
    source         TEXT NOT NULL DEFAULT 'arxiv',
    first_seen_utc TEXT NOT NULL
)
"""


class SeenStore:
    """Persistent deduplication store backed by SQLite.

    Uses the standard library driver on purpose: the SQLAlchemy stack
    (``db`` extra) arrives with the enrichment modules and a fuller schema.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def is_seen(self, key: str) -> bool:
        """Return True when the dedup key was stored before."""
        row = self._conn.execute("SELECT 1 FROM seen_papers WHERE dedup_key = ?", (key,)).fetchone()
        return row is not None

    def mark_seen(self, paper: PaperRecord) -> bool:
        """Record the paper; return True when it was new, False when already stored."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO seen_papers"
            " (dedup_key, arxiv_id, title, source, first_seen_utc)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                dedup_key(paper),
                paper.arxiv_id,
                paper.title,
                paper.source,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def count(self) -> int:
        """Number of stored keys (handy for tests and diagnostics)."""
        row = self._conn.execute("SELECT COUNT(*) FROM seen_papers").fetchone()
        return int(row[0])

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
