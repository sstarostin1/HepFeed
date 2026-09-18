"""Publishing cycle: ready notes to Telegram channels (docs/CONCEPT.md, section 6.5).

MVP moderation: ready notes go to the operator chat (``PUBLISH_MODERATION=true``)
with inline buttons; the decision arrives through the admin listener callback
or via ``publish --approve ID`` / ``publish --reject ID``.
Channel routing: accelerator-physics notes go to the AP channel, the rest to HEP.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hepfeed.admin import moderation_keyboard
from hepfeed.config import Settings
from hepfeed.ingestion.store import (
    NOTE_STATUS_IN_REVIEW,
    NOTE_STATUS_REJECTED,
    ReadyNote,
    SeenStore,
)
from hepfeed.publishing.markdown import to_telegram_html
from hepfeed.publishing.telegram import TelegramClient, TelegramError

if TYPE_CHECKING:
    from hepfeed.ingestion.models import PaperRecord

logger = logging.getLogger(__name__)


@dataclass
class PublishRunResult:
    """Counters of a single publishing cycle."""

    published: int = 0
    sent_for_review: int = 0
    failed: int = 0
    approved: int = 0
    rejected: int = 0


def route_channel(record: PaperRecord, settings: Settings) -> str | None:
    """Pick the target channel for a paper: AP or HEP."""
    categories = set(record.categories)
    if record.primary_category:
        categories.add(record.primary_category)
    if any(cat.startswith("physics.acc") for cat in categories):
        return settings.telegram_channel_ap or None
    return settings.telegram_channel_hep or None


async def publish_notes_once(
    settings: Settings,
    *,
    limit: int = 10,
    dry_run: bool = False,
    approve: int | None = None,
    reject: int | None = None,
    moderation: bool | None = None,
) -> PublishRunResult:
    """Publish ready notes; or apply a moderator decision to a single note.

    ``moderation`` overrides ``PUBLISH_MODERATION`` for this run (the admin
    console ``/moderation on|off`` switch); None keeps the configured value.
    """
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("SQLite storage is required for publishing for now")
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    store = SeenStore(settings.ensure_data_dir())
    try:
        async with TelegramClient(settings.telegram_bot_token) as tg:
            if approve is not None:
                return await _apply_decision(
                    store, tg, settings, note_id=approve, approve=True, dry_run=dry_run
                )
            if reject is not None:
                return await _apply_decision(
                    store, tg, settings, note_id=reject, approve=False, dry_run=dry_run
                )
            return await _publish_ready(
                store, tg, settings, limit=limit, dry_run=dry_run, moderation=moderation
            )
    finally:
        store.close()


def _resolve_channel(settings: Settings, note: ReadyNote) -> str | None:
    channel = route_channel(note.paper, settings)
    if channel is None:
        logger.warning(
            "no channel configured for note %d (paper %s)",
            note.note_id,
            note.paper.arxiv_id,
        )
    return channel


async def _publish_ready(
    store: SeenStore,
    tg: TelegramClient,
    settings: Settings,
    *,
    limit: int,
    dry_run: bool,
    moderation: bool | None = None,
) -> PublishRunResult:
    published = 0
    sent_for_review = 0
    failed = 0
    use_moderation = settings.publish_moderation if moderation is None else moderation
    for note in store.notes_ready(limit=limit):
        if use_moderation:
            chat = settings.telegram_moderator_chat_id
            if not chat:
                logger.warning(
                    "moderation is on but TELEGRAM_MODERATOR_CHAT_ID is not set; "
                    "note %d stays ready",
                    note.note_id,
                )
                failed += 1
                continue
            if dry_run:
                logger.info("[dry-run] would send note %d to moderator chat", note.note_id)
                sent_for_review += 1
                continue
            text = f"ID: {note.note_id} — на модерацию\n\n{to_telegram_html(note.note_text)}"
            try:
                await tg.send_message(
                    chat,
                    text,
                    reply_markup=moderation_keyboard(note.note_id),
                    parse_mode="HTML",
                )
            except TelegramError as exc:
                failed += 1
                logger.warning("moderator send failed for note %d: %s", note.note_id, exc)
                continue
            store.mark_note_publish_status(note.note_id, NOTE_STATUS_IN_REVIEW)
            sent_for_review += 1
            continue

        channel = _resolve_channel(settings, note)
        if channel is None:
            failed += 1
            continue
        if dry_run:
            logger.info("[dry-run] would publish note %d to %s", note.note_id, channel)
            published += 1
            continue
        try:
            message_id = await tg.send_message(
                channel, to_telegram_html(note.note_text), parse_mode="HTML"
            )
        except TelegramError as exc:
            failed += 1
            logger.warning("publish failed for note %d: %s", note.note_id, exc)
            continue
        store.mark_note_published(note.note_id, channel)
        logger.info("note %d published to %s (message %d)", note.note_id, channel, message_id)
        published += 1
    return PublishRunResult(published=published, sent_for_review=sent_for_review, failed=failed)


async def _apply_decision(
    store: SeenStore,
    tg: TelegramClient,
    settings: Settings,
    *,
    note_id: int,
    approve: bool,
    dry_run: bool,
) -> PublishRunResult:
    note = store.note_by_id(note_id)
    if note is None:
        logger.warning("note %d not found", note_id)
        return PublishRunResult(failed=1)
    if not approve:
        if not dry_run:
            store.mark_note_publish_status(note_id, NOTE_STATUS_REJECTED)
        logger.info("note %d rejected", note_id)
        return PublishRunResult(rejected=1)
    channel = _resolve_channel(settings, note)
    if channel is None:
        return PublishRunResult(failed=1)
    if dry_run:
        logger.info("[dry-run] would publish note %d to %s", note_id, channel)
        return PublishRunResult(published=1)
    try:
        message_id = await tg.send_message(
            channel, to_telegram_html(note.note_text), parse_mode="HTML"
        )
    except TelegramError as exc:
        logger.warning("publish failed for note %d: %s", note_id, exc)
        return PublishRunResult(failed=1)
    store.mark_note_published(note_id, channel)
    logger.info("note %d published to %s (message %d)", note_id, channel, message_id)
    return PublishRunResult(published=1, approved=1)


def publish_notes_sync(
    settings: Settings,
    *,
    limit: int = 10,
    dry_run: bool = False,
    approve: int | None = None,
    reject: int | None = None,
    moderation: bool | None = None,
) -> PublishRunResult:
    """Synchronous wrapper around :func:`publish_notes_once` (for CLI and jobs)."""
    return asyncio.run(
        publish_notes_once(
            settings,
            limit=limit,
            dry_run=dry_run,
            approve=approve,
            reject=reject,
            moderation=moderation,
        )
    )


def apply_moderation_decision_sync(
    settings: Settings, *, note_id: int, approve: bool
) -> PublishRunResult:
    """Apply a moderator decision to one note (inline-button path, no CLI)."""
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("SQLite storage is required for publishing for now")
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    store = SeenStore(settings.ensure_data_dir())

    async def scenario() -> PublishRunResult:
        async with TelegramClient(settings.telegram_bot_token) as tg:
            return await _apply_decision(
                store, tg, settings, note_id=note_id, approve=approve, dry_run=False
            )

    try:
        return asyncio.run(scenario())
    finally:
        store.close()
