"""Tests for the note-generation cycle (LLM mocked, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hepfeed.config import Settings
from hepfeed.generation.pipeline import generate_notes_sync
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import SeenStore


def _valid_note(arxiv_id: str) -> str:
    return (
        f"Заметка про {arxiv_id}\n\n"
        f"📄 arXiv:{arxiv_id}\n\n"
        f"🔗 Ссылки:\n• arXiv: https://arxiv.org/abs/{arxiv_id}\n"
    )


class _FakeLLM:
    last_user: str = ""

    def __init__(self, api_key: str, **kwargs: object) -> None:
        assert api_key, "api key must be passed through"

    async def __aenter__(self) -> _FakeLLM:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def complete(self, system: str, user: str, **kwargs: object) -> str:
        _FakeLLM.last_user = user
        arxiv_id = user.split("arXiv ID: ", 1)[1].split(" ", 1)[0]
        return _valid_note(arxiv_id)


class _FailingLLM(_FakeLLM):
    async def complete(self, system: str, user: str, **kwargs: object) -> str:
        from hepfeed.generation.notes import NoteValidationError

        raise NoteValidationError("simulated validation failure")


def _seed(store: SeenStore, arxiv_id: str) -> None:
    store.mark_seen(PaperRecord(arxiv_id=arxiv_id, title=f"P {arxiv_id}", abstract="Abs."))


def _settings() -> Settings:
    return Settings(_env_file=None, polza_api_key="test-key")


def test_generate_notes_once_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.generation.pipeline.LLMClient", _FakeLLM)
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    _seed(store, "2609.00001")
    store.close()

    result = generate_notes_sync(_settings(), limit=5)

    assert (result.generated, result.failed) == (1, 0)
    assert result.pending_left == 0
    assert result.notes[0][0].arxiv_id == "2609.00001"
    assert "arXiv ID: 2609.00001" in _FakeLLM.last_user
    store = SeenStore("data/hepfeed.db")
    try:
        assert store.count_notes() == 1
        assert store.pending_count() == 0
    finally:
        store.close()


def test_generate_notes_once_failure_marks_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.generation.pipeline.LLMClient", _FailingLLM)
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    _seed(store, "2609.00002")
    store.close()

    result = generate_notes_sync(_settings(), limit=5)

    assert (result.generated, result.failed) == (0, 1)
    assert result.pending_left == 1
    store = SeenStore("data/hepfeed.db")
    try:
        assert store.count_notes() == 0
    finally:
        store.close()


def test_generate_notes_once_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.generation.pipeline.LLMClient", _FakeLLM)
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    _seed(store, "2609.00003")
    store.close()

    result = generate_notes_sync(_settings(), limit=5, dry_run=True)

    assert result.generated == 1
    store = SeenStore("data/hepfeed.db")
    try:
        assert store.count_notes() == 0
        assert store.pending_count() == 1
    finally:
        store.close()


def test_generate_notes_once_requires_api_key(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("POLZA_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="POLZA_API_KEY"):
            generate_notes_sync(Settings(_env_file=None), limit=1)
    finally:
        monkeypatch.undo()


def test_generate_notes_sync_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.generation.pipeline.LLMClient", _FakeLLM)
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    _seed(store, "2609.00004")
    store.close()

    result = generate_notes_sync(_settings(), limit=5)

    assert result.generated == 1
