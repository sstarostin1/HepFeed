"""Command-line interface. For now: configuration diagnostics only."""

from __future__ import annotations

import argparse

from hepfeed import __version__
from hepfeed.config import Settings

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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="hepfeed",
        description="HepFeed - HEP/AP publication feed pipeline control utility",
    )
    parser.add_argument("--version", action="version", version=f"hepfeed {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("check", help="verify configuration and storage paths")
    args = parser.parse_args(argv)

    if args.command == "check":
        return run_check()
    parser.print_help()
    return 0
