"""Tests for the publishing cycle (Telegram mocked, no network)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import ClassVar

import pytest

from hepfeed.config import Settings
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import SeenStore
from hepfeed.publishing.pipeline import (
    apply_moderation_decision_sync,
    publish_notes_sync,
    route_channel,
)


def _settings(moderation: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="test-token",
        telegram_channel_hep="@hep_test",
        telegram_channel_ap="@ap_test",
        telegram_moderator_chat_id="@mod_test",
        publish_moderation=moderation,
    )


def _paper(arxiv_id: str, acc: bool = False) -> PaperRecord:
    cats = ["physics.acc-ph"] if acc else ["hep-ph"]
    return PaperRecord(
        arxiv_id=arxiv_id,
        title=f"P {arxiv_id}",
        abstract="Abs.",
        categories=cats,
        primary_category=cats[0],
    )


class FakeTelegram:
    sent: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = []

    def __init__(self, api_token: str, **kwargs: object) -> None:
        assert api_token, "bot token must be passed through"

    async def __aenter__(self) -> FakeTelegram:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def send_message(
        self, chat_id: str, text: str, reply_markup: dict[str, object] | None = None
    ) -> int:
        FakeTelegram.sent.append((chat_id, text, reply_markup))
        return len(FakeTelegram.sent)


@pytest.fixture(autouse=True)
def _reset_sent() -> None:
    FakeTelegram.sent = []


def _seed_note(arxiv_id: str, acc: bool = False) -> None:
    Path("data").mkdir(exist_ok=True)
    store = SeenStore("data/hepfeed.db")
    paper = _paper(arxiv_id, acc)
    store.mark_seen(paper)
    store.save_note(paper, f"note for {arxiv_id}", "model-x")
    store.close()


def _note_status(note_id: int) -> str:
    conn = sqlite3.connect("data/hepfeed.db")
    try:
        row = conn.execute("SELECT status FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        conn.close()


def test_route_channel() -> None:
    settings = _settings()
    assert route_channel(_paper("1"), settings) == "@hep_test"
    assert route_channel(_paper("2", acc=True), settings) == "@ap_test"


def test_publish_auto_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00101")

    result = publish_notes_sync(_settings(), limit=5)

    assert (result.published, result.sent_for_review, result.failed) == (1, 0, 0)
    assert FakeTelegram.sent[0][0] == "@hep_test"
    assert "note for 2609.00101" in FakeTelegram.sent[0][1]
    assert _note_status(1) == "published"


def test_publish_moderation_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00102")

    result = publish_notes_sync(_settings(moderation=True), limit=5)

    assert (result.published, result.sent_for_review) == (0, 1)
    assert FakeTelegram.sent[0][0] == "@mod_test"
    assert "ID: 1" in FakeTelegram.sent[0][1]
    keyboard = FakeTelegram.sent[0][2]
    assert keyboard is not None
    buttons = keyboard["inline_keyboard"][0]
    assert [button["text"] for button in buttons] == ["Опубликовать", "Отклонить"]
    assert [button["callback_data"] for button in buttons] == ["note:1:approve", "note:1:reject"]
    assert _note_status(1) == "in_review"


def test_publish_moderation_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00107")

    # /moderation off must win over PUBLISH_MODERATION=true in settings
    result = publish_notes_sync(_settings(moderation=True), limit=5, moderation=False)

    assert (result.published, result.sent_for_review) == (1, 0)
    assert FakeTelegram.sent[0][0] == "@hep_test"
    assert _note_status(1) == "published"


def test_publish_approve_and_reject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00103")
    _seed_note("2609.00104")

    rejected = publish_notes_sync(_settings(), reject=1)
    assert rejected.rejected == 1
    approved = publish_notes_sync(_settings(), approve=2)
    assert approved.published == 1
    assert FakeTelegram.sent[0][0] == "@hep_test"

    conn = sqlite3.connect("data/hepfeed.db")
    try:
        statuses = dict(conn.execute("SELECT id, status FROM notes").fetchall())
    finally:
        conn.close()
    assert statuses == {1: "rejected", 2: "published"}


def test_apply_moderation_decision_sync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00106")

    result = apply_moderation_decision_sync(_settings(), note_id=1, approve=True)

    assert result.published == 1
    assert FakeTelegram.sent[0][0] == "@hep_test"
    assert _note_status(1) == "published"


def test_publish_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hepfeed.publishing.pipeline.TelegramClient", FakeTelegram)
    _seed_note("2609.00105")

    result = publish_notes_sync(_settings(), limit=5, dry_run=True)

    assert result.published == 1
    assert FakeTelegram.sent == []
    assert _note_status(1) == "ready"


def test_publish_requires_token(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            publish_notes_sync(Settings(_env_file=None), limit=1)
    finally:
        monkeypatch.undo()
