"""Tests for scheduler job wiring (no real scheduler loop, no network)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from hepfeed.admin import PauseFlag
from hepfeed.config import Settings
from hepfeed.ingestion.pipeline import PollResult
from hepfeed.scheduler import _parse_categories, build_scheduler, run_poll_job


def test_run_poll_job_handles_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr("hepfeed.scheduler.poll_arxiv_sync", boom)
    with caplog.at_level(logging.ERROR):
        run_poll_job(Settings(_env_file=None), PauseFlag())
    assert "arXiv poll job failed" in caplog.text


def test_run_poll_job_logs_summary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    result = PollResult(fetched=10, recent=3, unique=3, new=1)
    monkeypatch.setattr("hepfeed.scheduler.poll_arxiv_sync", lambda *a, **k: result)
    with caplog.at_level(logging.INFO):
        run_poll_job(Settings(_env_file=None), PauseFlag())
    assert "new=1" in caplog.text


def test_build_scheduler_registers_jobs() -> None:
    settings = Settings(_env_file=None)
    scheduler = build_scheduler(settings, interval_minutes=7)
    poll_job = scheduler.get_job("arxiv-poll")
    assert poll_job is not None
    assert poll_job.trigger.interval == timedelta(minutes=7)
    assert poll_job.next_run_time is not None
    note_job = scheduler.get_job("generate-notes")
    assert note_job is not None
    assert note_job.trigger.interval == timedelta(minutes=settings.notes_interval_minutes)


def test_parse_categories() -> None:
    assert _parse_categories("a, b,,c") == ["a", "b", "c"]


def test_run_publish_job_passes_moderation_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hepfeed.admin import ModerationFlag
    from hepfeed.publishing.pipeline import PublishRunResult
    from hepfeed.scheduler import run_publish_job

    seen: dict[str, object] = {}

    def fake_publish(settings: object, **kwargs: object) -> PublishRunResult:
        seen.update(kwargs)
        return PublishRunResult()

    monkeypatch.setattr("hepfeed.scheduler.publish_notes_sync", fake_publish)
    run_publish_job(
        Settings(_env_file=None, telegram_bot_token="t"),
        PauseFlag(),
        ModerationFlag(True),
    )
    assert seen["moderation"] is True
