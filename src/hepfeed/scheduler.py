"""Periodic scheduling of ingestion jobs (APScheduler v3, docs/CONCEPT.md, section 7)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from hepfeed.config import Settings
from hepfeed.ingestion.pipeline import poll_arxiv_sync

logger = logging.getLogger(__name__)

_JOB_ID = "arxiv-poll"


def _parse_categories(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()]


def run_poll_job(settings: Settings) -> None:
    """Scheduler job body: one arXiv poll cycle with error logging."""
    try:
        result = poll_arxiv_sync(
            settings,
            hours=settings.arxiv_poll_window_hours,
            max_results=100,
            categories=_parse_categories(settings.arxiv_categories),
        )
    except Exception:
        logger.exception("arXiv poll job failed")
        return
    logger.info(
        "arXiv poll done: fetched=%d recent=%d unique=%d new=%d",
        result.fetched,
        result.recent,
        result.unique,
        result.new,
    )


def build_scheduler(settings: Settings, interval_minutes: int | None = None) -> BlockingScheduler:
    """Create a configured (not yet started) blocking scheduler."""
    minutes = interval_minutes or settings.arxiv_poll_interval_minutes
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_poll_job,
        args=(settings,),
        trigger=IntervalTrigger(minutes=minutes, timezone="UTC"),
        next_run_time=datetime.now(UTC),
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        name="arXiv ingestion poll",
    )
    return scheduler


def run_scheduler(settings: Settings, interval_minutes: int | None = None) -> int:
    """Start the blocking scheduler and block until interrupted."""
    minutes = interval_minutes or settings.arxiv_poll_interval_minutes
    scheduler = build_scheduler(settings, interval_minutes)
    logger.info("scheduler starting: arXiv poll every %d minute(s); Ctrl+C to stop", minutes)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler stopped")
    return 0
