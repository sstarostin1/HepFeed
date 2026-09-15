"""One note-generation cycle over papers that lack notes (docs/CONCEPT.md, section 6.4)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from hepfeed.config import Settings
from hepfeed.generation.llm import LLMClient, LLMError
from hepfeed.generation.notes import NoteValidationError, generate_note
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import (
    STATUS_NOTE_CREATED,
    STATUS_NOTE_FAILED,
    SeenStore,
)

logger = logging.getLogger(__name__)


@dataclass
class NoteRunResult:
    """Counters and fresh notes of a single generation cycle."""

    generated: int
    failed: int
    pending_left: int
    notes: list[tuple[PaperRecord, str]] = field(default_factory=list)


async def generate_notes_once(
    settings: Settings,
    *,
    limit: int = 5,
    dry_run: bool = False,
) -> NoteRunResult:
    """Generate notes for stored papers without one; persist and track status."""
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("SQLite storage is required for note generation for now")
    if not settings.polza_api_key:
        raise RuntimeError("POLZA_API_KEY is not configured")

    store = SeenStore(settings.ensure_data_dir())
    try:
        pending = store.pending_for_note(limit=limit)
        generated = 0
        failed = 0
        notes: list[tuple[PaperRecord, str]] = []
        if pending:
            async with LLMClient(
                settings.polza_api_key,
                base_url=settings.llm_base_url,
                model=settings.llm_model,
            ) as llm:
                for record in pending:
                    try:
                        note = await generate_note(record, llm)
                    except (LLMError, NoteValidationError) as exc:
                        failed += 1
                        logger.warning("note generation failed for %s: %s", record.arxiv_id, exc)
                        if not dry_run:
                            store.mark_note_status(record, STATUS_NOTE_FAILED)
                        continue
                    generated += 1
                    notes.append((record, note))
                    if not dry_run:
                        store.save_note(record, note, settings.llm_model)
                        store.mark_note_status(record, STATUS_NOTE_CREATED)
        return NoteRunResult(
            generated=generated,
            failed=failed,
            pending_left=store.pending_count(),
            notes=notes,
        )
    finally:
        store.close()


def generate_notes_sync(
    settings: Settings,
    *,
    limit: int = 5,
    dry_run: bool = False,
) -> NoteRunResult:
    """Synchronous wrapper around :func:`generate_notes_once` (for CLI and jobs)."""
    return asyncio.run(generate_notes_once(settings, limit=limit, dry_run=dry_run))
