"""Command-line interface: diagnostics, arXiv ingestion and scheduling."""

from __future__ import annotations

import argparse

from hepfeed import __version__
from hepfeed.config import Settings
from hepfeed.ingestion.pipeline import poll_arxiv_sync
from hepfeed.logging.setup import setup_logging
from hepfeed.scheduler import run_scheduler

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


def run_poll_arxiv(args: argparse.Namespace) -> int:
    """Fetch fresh arXiv submissions, drop duplicates, persist unseen ones."""
    settings = Settings()
    if not settings.database_url.startswith("sqlite:///"):
        print("! poll-arxiv supports SQLite storage only for now")
        return 2
    raw_categories = args.categories or settings.arxiv_categories
    result = poll_arxiv_sync(
        settings,
        hours=args.hours,
        max_results=args.max_results,
        categories=[c.strip() for c in raw_categories.split(",") if c.strip()],
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("dry-run: seen-store not updated")
    print(
        f"arXiv poll: fetched {result.fetched}, "
        f"within {args.hours:g}h window {result.recent}, "
        f"unique {result.unique}, new {result.new}"
    )
    for record in result.new_records:
        cats = ",".join(record.categories[:3]) or "-"
        print(f"  [{record.arxiv_id}] {record.title} ({cats})")
    return 0


def run_schedule(args: argparse.Namespace) -> int:
    """Run ingestion jobs on a schedule until interrupted."""
    settings = Settings()
    setup_logging(settings.log_level)
    return run_scheduler(settings, interval_minutes=args.interval_minutes)


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
        default=None,
        help="comma-separated arXiv categories (default: ARXIV_CATEGORIES)",
    )
    poll.add_argument("--dry-run", action="store_true", help="do not persist into the seen-store")
    sched = subparsers.add_parser("schedule", help="run ingestion jobs on a schedule until Ctrl+C")
    sched.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="override poll interval from ARXIV_POLL_INTERVAL_MINUTES",
    )
    args = parser.parse_args(argv)

    if args.command == "check":
        return run_check()
    if args.command == "poll-arxiv":
        return run_poll_arxiv(args)
    if args.command == "schedule":
        return run_schedule(args)
    parser.print_help()
    return 0
