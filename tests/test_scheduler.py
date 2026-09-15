"""Tests for scheduler job wiring (no real scheduler loop, no network)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

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
        run_poll_job(Settings(_env_file=None))
    assert "arXiv poll job failed" in caplog.text


def test_run_poll_job_logs_summary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    result = PollResult(fetched=10, recent=3, unique=3, new=1)
    monkeypatch.setattr("hepfeed.scheduler.poll_arxiv_sync", lambda *a, **k: result)
    with caplog.at_level(logging.INFO):
        run_poll_job(Settings(_env_file=None))
    assert "new=1" in caplog.text


def test_build_scheduler_registers_job() -> None:
    settings = Settings(_env_file=None)
    scheduler = build_scheduler(settings, interval_minutes=7)
    job = scheduler.get_job("arxiv-poll")
    assert job is not None
    assert job.trigger.interval == timedelta(minutes=7)
    assert job.next_run_time is not None


def test_parse_categories() -> None:
    assert _parse_categories("a, b,,c") == ["a", "b", "c"]
