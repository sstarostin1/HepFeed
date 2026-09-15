"""Process-wide logging configuration (docs/CONCEPT.md, section 6.6)."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once with a compact console format.

    APScheduler executor chatter is demoted to WARNING: per-run summaries
    are emitted by ``hepfeed.scheduler`` itself.
    """
    logging.basicConfig(level=level.upper(), format=_FORMAT)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
