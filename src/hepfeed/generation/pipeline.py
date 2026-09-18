"""One note-generation cycle over papers that lack notes (docs/CONCEPT.md, section 6.4)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from hepfeed.config import Settings
from hepfeed.enrichment.arxiv_html import fetch_full_text
from hepfeed.generation.llm import LLMClient, LLMError
from hepfeed.generation.notes import NoteValidationError, generate_note
from hepfeed.generation.prompt import load_system_prompt
from hepfeed.ingestion.dedup import dedup_key
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


def _model_chain(settings: Settings) -> list[str]:
    """Primary model followed by configured fallbacks, in decreasing priority."""
    chain = [settings.llm_model.strip()]
    chain += [m.strip() for m in settings.llm_model_fallbacks.split(",") if m.strip()]
    return chain


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
        system_prompt, _ = load_system_prompt(settings)
        pending = store.pending_for_note(limit=limit)
        generated = 0
        failed = 0
        notes: list[tuple[PaperRecord, str]] = []
        if pending:
            extra_body = (
                {"reasoning_effort": settings.llm_reasoning_effort}
                if settings.llm_reasoning_effort.strip()
                else None
            )
            async with LLMClient(
                settings.polza_api_key,
                base_url=settings.llm_base_url,
                models=_model_chain(settings),
                extra_body=extra_body,
            ) as llm:
                for record in pending:
                    full_text: str | None = None
                    if settings.llm_use_full_text:
                        key = dedup_key(record)
                        full_text = store.get_full_text(key)
                        if full_text is None:
                            try:
                                full_text = await fetch_full_text(
                                    record.arxiv_id,
                                    version=record.arxiv_version,
                                    max_chars=settings.llm_full_text_max_chars,
                                )
                            except Exception:
                                logger.warning(
                                    "full-text fetch failed for %s",
                                    record.arxiv_id,
                                    exc_info=True,
                                )
                                full_text = None
                            if full_text:
                                store.set_full_text(record, full_text)
                    if not record.abstract.strip() and not full_text:
                        # Empty input makes the task unsatisfiable: reasoning
                        # models loop on it. Leave the paper pending so a
                        # future enrichment pass can fill the data in.
                        logger.warning(
                            "skip %s: no abstract and no full text, waiting for enrichment",
                            record.arxiv_id,
                        )
                        continue
                    try:
                        note = await generate_note(
                            record, llm, full_text=full_text, system_prompt=system_prompt
                        )
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
