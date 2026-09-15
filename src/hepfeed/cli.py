"""Command-line interface: configuration diagnostics and arXiv ingestion."""

from __future__ import annotations

import argparse
import asyncio

from hepfeed import __version__
from hepfeed.config import Settings
from hepfeed.ingestion.arxiv import (
    DEFAULT_CATEGORIES,
    ArxivClient,
    filter_published_within,
)
from hepfeed.ingestion.dedup import deduplicate
from hepfeed.ingestion.store import SeenStore

_CHECKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("POLZA_API_KEY", "LLM provider key (note generation, web search)"),
    ("TELEGRAM_BOT_TOKEN", "bot token from @BotFather"),
    ("TELEGRAM_CHANNEL_HEP", "HEP publication channel"),
    ("TELEGRAM_CHANNEL_AP", "accelerator physics channel"),
    ("TELEGRAM_MODERATOR_CHAT_ID", "operator chat for manual moderation"),
    ("DATABASE_URL", "state storage connection string"),
)


def run_check() -> int:
    """Report which settings are visible to the pipeline and prepare storage."""
    settings = Settings()
    print(f"HepFeed {__version__} - configuration check")
    for env_name, purpose in _CHECKED_FIELDS:
        value = getattr(settings, env_name.lower(), None)
        state = "set    " if value else "missing"
        print(f"  [{state}] {env_name:<28} {purpose}")
    settings.ensure_data_dir()

    missing = settings.missing_critical()
    print()
    if missing:
        print("! critical variables not set: " + ", ".join(m.upper() for m in missing))
        print("  fill .env (template: .env.example) before running the pipeline")
    else:
        print("ok: all critical variables are set")
    return 0


async def run_poll_arxiv(args: argparse.Namespace) -> int:
    """Fetch fresh arXiv submissions, drop duplicates, persist unseen ones."""
    settings = Settings()
    if not settings.database_url.startswith("sqlite:///"):
        print("! poll-arxiv supports SQLite storage only for now")
        return 2
    db_path = settings.ensure_data_dir()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    async with ArxivClient() as client:
        fetched = await client.fetch_recent(categories=categories, max_results=args.max_results)
    recent = filter_published_within(fetched, hours=args.hours)
    unique = deduplicate(recent)

    if args.dry_run:
        new_records = unique
        print("dry-run: seen-store not updated")
    else:
        store = SeenStore(db_path)
        try:
            new_records = [record for record in unique if store.mark_seen(record)]
        finally:
            store.close()

    print(
        f"arXiv poll: fetched {len(fetched)}, "
        f"within {args.hours:g}h window {len(recent)}, "
        f"unique {len(unique)}, new {len(new_records)}"
    )
    for record in new_records:
        cats = ",".join(record.categories[:3]) or "-"
        print(f"  [{record.arxiv_id}] {record.title} ({cats})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="hepfeed",
        description="HepFeed - HEP/AP publication feed pipeline control utility",
    )
    parser.add_argument("--version", action="version", version=f"hepfeed {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("check", help="verify configuration and storage paths")
    poll = subparsers.add_parser(
        "poll-arxiv", help="fetch fresh arXiv papers and update the seen-store"
    )
    poll.add_argument("--hours", type=float, default=24.0, help="publication-date window in hours")
    poll.add_argument("--max-results", type=int, default=100, help="max entries per API request")
    poll.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="comma-separated arXiv categories",
    )
    poll.add_argument("--dry-run", action="store_true", help="do not persist into the seen-store")
    args = parser.parse_args(argv)

    if args.command == "check":
        return run_check()
    if args.command == "poll-arxiv":
        return asyncio.run(run_poll_arxiv(args))
    parser.print_help()
    return 0
