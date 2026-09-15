"""Tests for the one-cycle ingestion pipeline (ArxivClient mocked)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hepfeed.config import Settings
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.pipeline import poll_arxiv_sync
from hepfeed.ingestion.store import SeenStore


class _FakeClient:
    """Network-free stand-in for ArxivClient with the same surface."""

    def __init__(self, records: list[PaperRecord]) -> None:
        self._records = records

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def fetch_recent(
        self, categories: list[str], max_results: int = 100
    ) -> list[PaperRecord]:
        return list(self._records)


def _now() -> datetime:
    return datetime.now(UTC)


def test_poll_once_persists_and_dedups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    records = [
        PaperRecord(arxiv_id="2609.00001", title="Fresh", published=_now() - timedelta(hours=1)),
        PaperRecord(arxiv_id="2609.00002", title="Stale", published=_now() - timedelta(hours=30)),
        PaperRecord(
            arxiv_id="2609.00001", title="Fresh duplicate", published=_now() - timedelta(hours=2)
        ),
    ]
    monkeypatch.setattr("hepfeed.ingestion.pipeline.ArxivClient", lambda: _FakeClient(records))
    settings = Settings(_env_file=None)

    result = poll_arxiv_sync(settings, hours=24.0, max_results=100, categories=["hep-ph"])

    assert (result.fetched, result.recent, result.unique, result.new) == (3, 2, 1, 1)
    assert result.new_records[0].arxiv_id == "2609.00001"
    store = SeenStore("data/hepfeed.db")
    try:
        assert store.count() == 1
    finally:
        store.close()


def test_poll_once_dry_run_does_not_persist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fresh = PaperRecord(arxiv_id="2609.00003", title="Fresh", published=_now() - timedelta(hours=1))
    monkeypatch.setattr("hepfeed.ingestion.pipeline.ArxivClient", lambda: _FakeClient([fresh]))
    settings = Settings(_env_file=None)

    result = poll_arxiv_sync(settings, hours=24.0, max_results=10, categories=[], dry_run=True)

    assert result.new == 1
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    try:
        assert store.count() == 0
    finally:
        store.close()


def test_poll_sync_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.ingestion.pipeline.ArxivClient", lambda: _FakeClient([]))
    settings = Settings(_env_file=None)

    result = poll_arxiv_sync(settings, hours=1.0, max_results=5, categories=[])

    assert result.fetched == 0
    assert result.new == 0


def test_poll_once_rejects_non_sqlite() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://localhost/hepfeed")
    with pytest.raises(RuntimeError, match="SQLite"):
        poll_arxiv_sync(settings, hours=1.0, max_results=5, categories=[])
