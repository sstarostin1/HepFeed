"""High-level ingestion pipeline: one arXiv poll cycle end to end."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hepfeed.config import Settings
from hepfeed.ingestion.arxiv import ArxivClient, filter_published_within
from hepfeed.ingestion.dedup import deduplicate
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import SeenStore


@dataclass
class PollResult:
    """Counters and newly stored records of a single poll cycle."""

    fetched: int
    recent: int
    unique: int
    new: int
    new_records: list[PaperRecord] = field(default_factory=list)


async def poll_arxiv_once(
    settings: Settings,
    *,
    hours: float,
    max_results: int,
    categories: list[str],
    dry_run: bool = False,
) -> PollResult:
    """Run one ingestion cycle: fetch, window-filter, dedup, persist unseen."""
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("SQLite storage is required for ingestion for now")

    async with ArxivClient() as client:
        fetched = await client.fetch_recent(categories=categories, max_results=max_results)
    recent = filter_published_within(fetched, hours=hours)
    unique = deduplicate(recent)

    if dry_run:
        new_records = list(unique)
    else:
        store = SeenStore(settings.ensure_data_dir())
        try:
            new_records = [record for record in unique if store.mark_seen(record)]
        finally:
            store.close()

    return PollResult(
        fetched=len(fetched),
        recent=len(recent),
        unique=len(unique),
        new=len(new_records),
        new_records=new_records,
    )


def poll_arxiv_sync(
    settings: Settings,
    *,
    hours: float,
    max_results: int,
    categories: list[str],
    dry_run: bool = False,
) -> PollResult:
    """Synchronous wrapper around :func:`poll_arxiv_once` (for CLI and jobs)."""
    return asyncio.run(
        poll_arxiv_once(
            settings,
            hours=hours,
            max_results=max_results,
            categories=categories,
            dry_run=dry_run,
        )
    )
