"""Tests for note validation and generation (all offline)."""

from __future__ import annotations

import asyncio

import pytest

from hepfeed.generation.notes import NoteValidationError, generate_note, validate_note
from hepfeed.ingestion.models import PaperRecord

_ARXIV_ID = "2609.01234"


def _record() -> PaperRecord:
    return PaperRecord(arxiv_id=_ARXIV_ID, title="T", abstract="A")


def _note() -> str:
    return (
        "Измерение сечения (A measurement)\n\n"
        f"📄 arXiv:{_ARXIV_ID}\n"
        "🏷 Теги: #test #qcd\n\n"
        "Основной текст заметки.\n\n"
        f"🔗 Ссылки:\n• arXiv: https://arxiv.org/abs/{_ARXIV_ID}\n"
    )


def test_validate_note_accepts_correct_note() -> None:
    assert validate_note(_note(), _record()) == []


def test_validate_note_rejects_overlong_note() -> None:
    issues = validate_note(_note() + "x" * 4200, _record())
    assert any("4096" in issue for issue in issues)


def test_validate_note_rejects_missing_arxiv_link() -> None:
    broken = _note().replace("https://arxiv.org/abs/2609.01234", "http://example.com")
    issues = validate_note(broken, _record())
    assert any("missing arXiv link" in issue for issue in issues)


def test_validate_note_rejects_forbidden_emoji() -> None:
    issues = validate_note(_note() + " 🚀🔥", _record())
    assert any("forbidden emoji" in issue for issue in issues)


def test_generate_note_returns_valid_note() -> None:
    class FakeLLM:
        async def complete(self, system: str, user: str, **kwargs: object) -> str:
            return _note()

    note = asyncio.run(generate_note(_record(), FakeLLM()))  # type: ignore[arg-type]
    assert "Измерение сечения" in note


def test_generate_note_raises_on_invalid_note() -> None:
    class FakeLLM:
        async def complete(self, system: str, user: str, **kwargs: object) -> str:
            return _note().replace("https://arxiv.org/abs/2609.01234", "http://example.com")

    with pytest.raises(NoteValidationError, match="missing arXiv link"):
        asyncio.run(generate_note(_record(), FakeLLM()))  # type: ignore[arg-type]
