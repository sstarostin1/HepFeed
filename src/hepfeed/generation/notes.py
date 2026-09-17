"""Note text generation and hard format post-checks.

Anti-hallucination contract (docs/CONCEPT.md, section 10.2): the model gets
only the collected article data; format violations fail the note instead of
being published.
"""

from __future__ import annotations

import asyncio

from hepfeed.generation.llm import LLMClient
from hepfeed.generation.prompt import SYSTEM_PROMPT, build_user_prompt
from hepfeed.ingestion.models import PaperRecord

MAX_NOTE_LENGTH = 4096
"""Hard Telegram limit; notes above it are rejected (CONCEPT.md, section 5)."""

_FORBIDDEN_EMOJI = frozenset("🚀🔥😱💥⚡🤯👏💯🎉")


class NoteValidationError(ValueError):
    """Raised when a generated note violates hard format requirements."""


def build_note_messages(record: PaperRecord, full_text: str | None = None) -> tuple[str, str]:
    """Return the (system, user) prompt pair for a paper."""
    return SYSTEM_PROMPT, build_user_prompt(record, full_text=full_text)


def validate_note(note: str, record: PaperRecord) -> list[str]:
    """Hard post-checks; an empty list means the note is publishable."""
    issues: list[str] = []
    if not note.strip():
        issues.append("note is empty")
    if len(note) > MAX_NOTE_LENGTH:
        issues.append(f"note length {len(note)} exceeds {MAX_NOTE_LENGTH} characters")
    if record.arxiv_id and f"arxiv.org/abs/{record.arxiv_id}" not in note:
        issues.append(f"missing arXiv link for {record.arxiv_id}")
    banned = sorted({char for char in note if char in _FORBIDDEN_EMOJI})
    if banned:
        issues.append("forbidden emoji present: " + " ".join(banned))
    return issues


async def generate_note(record: PaperRecord, llm: LLMClient, full_text: str | None = None) -> str:
    """Generate a note for the paper and enforce the hard format checks."""
    system, user = build_note_messages(record, full_text=full_text)
    # generous budget: reasoning models spend tokens before the final answer
    note = await llm.complete(system, user, max_tokens=10000, temperature=0.3)
    issues = validate_note(note, record)
    if issues:
        raise NoteValidationError("; ".join(issues))
    return note


def generate_note_sync(record: PaperRecord, llm: LLMClient, full_text: str | None = None) -> str:
    """Synchronous wrapper around :func:`generate_note`."""
    return asyncio.run(generate_note(record, llm, full_text=full_text))
