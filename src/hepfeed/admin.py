"""Operator console over Telegram: status, pause/resume, manual runs,
moderation mode and runtime system-prompt editing.

The listener is a long-polling daemon thread inside the scheduler process.
Only the configured moderator chat is served; anything else is ignored
(operator interaction per docs/CONCEPT.md, section 6.5 - not a reader bot).
Commands use underscores instead of spaces so they stay clickable links in
the Telegram client (help list, quick-command menus).
"""

from __future__ import annotations

import html
import logging
import threading

import httpx

from hepfeed.config import Settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_URL = "https://api.telegram.org"
_HTML_PARSE_MODE = "HTML"
"""Telegram parse mode for <pre> blocks (copy button above the block)."""
_PROMPT_CHUNK_CHARS = 3400
"""Raw characters per <pre> block, safely under the 4096 message limit."""

_HELP_TEXT = (
    "Команды администратора HepFeed:\n"
    "/status - состояние конвейера\n"
    "/pause - приостановить периодические задачи\n"
    "/resume - возобновить работу\n"
    "/run_poll - опросить arXiv сейчас\n"
    "/run_notes - сгенерировать заметки сейчас\n"
    "/run_publish - опубликовать готовые заметки\n"
    "/moderation on|off - ручное одобрение заметок перед публикацией\n"
    "/prompt - показать системный промпт (копируется кнопкой над блоком)\n"
    "/prompt_set - заменить промпт: ответьте (reply) этой командой на сообщение "
    "с промптом, приложив его новую версию\n"
    "/prompt_reset - вернуть встроенный системный промпт\n"
    "/help - эта справка\n\n"
    "Заметки на модерации приходят с кнопками «Опубликовать / Отклонить» - "
    "решение принимается прямо в чате."
)

_UNKNOWN_COMMAND_TEXT = (
    "Неизвестная команда. Доступно: /status, /pause, /resume, /run_poll, "
    "/run_notes, /run_publish, /moderation, /prompt, /prompt_set, /prompt_reset, /help"
)


class PauseFlag:
    """Thread-safe pause switch shared by jobs and the admin listener."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()


class ModerationFlag:
    """Thread-safe moderation switch shared by jobs and the admin listener.

    Initial value comes from ``PUBLISH_MODERATION``; ``/moderation on|off``
    changes it at runtime without touching the .env or restarting.
    """

    def __init__(self, enabled: bool = False) -> None:
        self._event = threading.Event()
        if enabled:
            self._event.set()

    def set(self, enabled: bool) -> None:
        if enabled:
            self._event.set()
        else:
            self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()


def handle_update(
    update: dict[str, object],
    settings: Settings,
    flag: PauseFlag,
    moderation: ModerationFlag | None = None,
) -> tuple[list[tuple[str, str | None]], str | None]:
    """Process one message update.

    Return (messages, background_action); each message is (text, parse_mode).
    """
    message = update.get("message") or {}
    if not isinstance(message, dict):
        return [], None
    chat = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if chat is None or not text:
        return [], None
    moderator = str(settings.telegram_moderator_chat_id or "")
    if not moderator or str(chat) != moderator:
        logger.warning("admin listener: ignored message from chat %s", chat)
        return [], None
    parts = text.split()
    command = parts[0].split("@")[0].lower()
    arg = parts[1].lower() if len(parts) > 1 else ""
    if command == "/help":
        return [(_HELP_TEXT, None)], None
    if command == "/status":
        return [(_status_reply(settings, flag, moderation), None)], None
    if command == "/pause":
        flag.set()
        return [("Пауза: периодические задачи пропускаются до /resume", None)], None
    if command == "/resume":
        flag.clear()
        return [("Работа возобновлена", None)], None
    if command in ("/run_poll", "/run_notes", "/run_publish"):
        target = command.removeprefix("/run_")
        return [(f"Запущено в фоне: {target}", None)], f"run_{target}"
    if command == "/moderation":
        return _moderation_reply(settings, moderation, arg), None
    if command == "/prompt":
        return _prompt_messages(settings), None
    if command == "/prompt_set":
        return [_prompt_set_reply(message, settings)], None
    if command == "/prompt_reset":
        return [_prompt_reset_reply(settings)], None
    return [(_UNKNOWN_COMMAND_TEXT, None)], None


def _moderation_reply(
    settings: Settings, moderation: ModerationFlag | None, arg: str
) -> list[tuple[str, str | None]]:
    current = moderation.is_set() if moderation is not None else settings.publish_moderation
    if arg not in ("on", "off"):
        state = "вкл" if current else "выкл"
        return [
            (
                f"Модерация: {state}. Включить: /moderation on, выключить: /moderation off.",
                None,
            )
        ]
    if moderation is None:
        return [("Runtime-переключение недоступно: moderation-флаг не передан.", None)]
    moderation.set(arg == "on")
    if arg == "on":
        return [("Модерация включена: заметки уходят вам с кнопками решения.", None)]
    return [("Модерация выключена: заметки публикуются в каналы сразу.", None)]


def _prompt_messages(settings: Settings) -> list[tuple[str, str | None]]:
    """Current system prompt as copyable <pre> blocks (chunked, HTML-escaped)."""
    from hepfeed.generation.prompt import load_system_prompt  # lazy: import cycles

    prompt, is_custom = load_system_prompt(settings)
    source = "пользовательский (system_prompt.txt)" if is_custom else "встроенный"
    header = f"Системный промпт ({source}, {len(prompt)} символов):"
    chunks = [
        prompt[i : i + _PROMPT_CHUNK_CHARS] for i in range(0, len(prompt), _PROMPT_CHUNK_CHARS)
    ] or [prompt]
    messages: list[tuple[str, str | None]] = []
    for index, chunk in enumerate(chunks):
        text = f"{header}\n<pre>{html.escape(chunk)}</pre>" if index == 0 else None
        messages.append(
            (text or f"(продолжение)\n<pre>{html.escape(chunk)}</pre>", _HTML_PARSE_MODE)
        )
    return messages


def _prompt_set_reply(message: dict[str, object], settings: Settings) -> tuple[str, str | None]:
    """Replace the system prompt with the text of the replied-to message."""
    from hepfeed.generation.prompt import save_system_prompt  # lazy: import cycles

    reply = message.get("reply_to_message") or {}
    if not ((reply.get("from") or {}).get("is_bot")):
        return (
            "Отправьте /prompt_set как ответ (reply) на сообщение с промптом, "
            "приложив его новую версию текстом.",
            None,
        )
    new_text = str(reply.get("text") or "").strip()
    if new_text.startswith("```"):  # unwrap optional code fences
        new_text = new_text.split("\n", 1)[1] if "\n" in new_text else ""
        if new_text.endswith("```"):
            new_text = new_text[:-3]
        new_text = new_text.strip()
    if not new_text:
        return ("В ответном сообщении нет текста промпта.", None)
    save_system_prompt(settings, new_text)
    return (
        f"Системный промпт обновлён ({len(new_text)} символов); "
        "применяется со следующего цикла генерации.",
        None,
    )


def _prompt_reset_reply(settings: Settings) -> tuple[str, str | None]:
    from hepfeed.generation.prompt import reset_system_prompt  # lazy: import cycles

    if reset_system_prompt(settings):
        return ("Пользовательский промпт удалён, снова действует встроенный.", None)
    return ("Пользовательский промпт не был задан.", None)


_MODERATION_APPROVE = "approve"
_MODERATION_REJECT = "reject"
_MODERATION_PREFIX = "note"


def moderation_keyboard(note_id: int) -> dict[str, object]:
    """Inline keyboard with the approve/reject decision for a note (moderator chat)."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Опубликовать",
                    "callback_data": f"{_MODERATION_PREFIX}:{note_id}:{_MODERATION_APPROVE}",
                },
                {
                    "text": "Отклонить",
                    "callback_data": f"{_MODERATION_PREFIX}:{note_id}:{_MODERATION_REJECT}",
                },
            ]
        ]
    }


def parse_moderation_callback(data: object) -> tuple[bool, int] | None:
    """Parse ``note:<id>:approve`` / ``note:<id>:reject``; None when malformed."""
    if not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != _MODERATION_PREFIX:
        return None
    try:
        note_id = int(parts[1])
    except ValueError:
        return None
    if parts[2] == _MODERATION_APPROVE:
        return True, note_id
    if parts[2] == _MODERATION_REJECT:
        return False, note_id
    return None


def handle_callback(
    update: dict[str, object], settings: Settings
) -> tuple[str | None, str | None, str | None]:
    """Process a callback_query update; return (callback_query_id, answer, background_action)."""
    callback = update.get("callback_query")
    if not isinstance(callback, dict):
        return None, None, None
    callback_id = str(callback.get("id") or "") or None
    sender = (callback.get("from") or {}).get("id")
    moderator = str(settings.telegram_moderator_chat_id or "")
    if not moderator or sender is None or str(sender) != moderator:
        logger.warning("admin listener: callback from unauthorized user %s", sender)
        return callback_id, "Недостаточно прав", None
    parsed = parse_moderation_callback(callback.get("data"))
    if parsed is None:
        return callback_id, "Неизвестное действие кнопки", None
    approve, note_id = parsed
    action = f"moderate:{_MODERATION_APPROVE if approve else _MODERATION_REJECT}:{note_id}"
    answer = f"Публикую заметку {note_id}..." if approve else f"Отклоняю заметку {note_id}..."
    return callback_id, answer, action


def _callback_message_ref(update: dict[str, object]) -> tuple[object, object] | None:
    """(chat_id, message_id) of the message the moderation buttons are attached to."""
    callback = update.get("callback_query")
    message = (callback or {}).get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return chat_id, message_id


def _status_reply(
    settings: Settings, flag: PauseFlag, moderation: ModerationFlag | None = None
) -> str:
    from hepfeed.ingestion.store import SeenStore  # lazy: avoids import cycles

    paused = "да" if flag.is_set() else "нет"
    moderation_state = (
        moderation.is_set() if moderation is not None else settings.publish_moderation
    )
    try:
        stats = SeenStore(settings.ensure_data_dir()).stats()
    except Exception as exc:  # pragma: no cover - diagnostics only
        logger.warning("status query failed: %s", exc)
        return f"Пауза: {paused}; статистика недоступна ({exc})"
    return (
        f"Пауза: {paused}\n"
        f"Модерация: {'вкл' if moderation_state else 'выкл'} (/moderation on|off)\n"
        f"Статей в БД: {stats['papers']} (ожидают заметку: {stats['papers_pending_note']})\n"
        f"Заметки: готово к публикации {stats['notes_ready']}, "
        f"на модерации {stats['notes_in_review']}, "
        f"опубликовано {stats['notes_published']}, "
        f"отклонено {stats['notes_rejected']}"
    )


class AdminListener:
    """Long-polling Telegram thread that serves operator commands."""

    def __init__(
        self, settings: Settings, flag: PauseFlag, moderation: ModerationFlag | None = None
    ) -> None:
        self._settings = settings
        self._flag = flag
        self._moderation = moderation or ModerationFlag(settings.publish_moderation)
        self._stop = threading.Event()

    def start(self) -> None:
        """Start the daemon listener thread (no-op when unconfigured)."""
        if not self._settings.telegram_bot_token:
            logger.warning("admin listener disabled: TELEGRAM_BOT_TOKEN not set")
            return
        if not self._settings.telegram_moderator_chat_id:
            logger.warning("admin listener disabled: TELEGRAM_MODERATOR_CHAT_ID not set")
            return
        threading.Thread(target=self._loop, name="admin-listener", daemon=True).start()
        logger.info("admin listener started (moderator chat only)")

    def _loop(self) -> None:
        token = self._settings.telegram_bot_token
        url = f"{_TELEGRAM_API_URL}/bot{token}/getUpdates"
        offset = 0
        with httpx.Client(timeout=60.0) as client:
            while not self._stop.is_set():
                try:
                    response = client.post(url, json={"offset": offset, "timeout": 25})
                    data = response.json()
                except (httpx.TransportError, ValueError) as exc:
                    logger.warning("admin listener poll failed: %s", exc)
                    self._stop.wait(5.0)
                    continue
                updates = data.get("result") or []
                for update in updates:
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    self._process(update, client)

    def _process(self, update: dict[str, object], client: httpx.Client) -> None:
        try:
            if update.get("callback_query") is not None:
                callback_id, answer, action = handle_callback(update, self._settings)
                messages: list[tuple[str, str | None]] = []
            else:
                callback_id = answer = None
                messages, action = handle_update(
                    update, self._settings, self._flag, self._moderation
                )
        except Exception:
            logger.exception("admin command processing failed")
            return
        if action:
            if action.startswith("moderate:"):
                threading.Thread(
                    target=self._run_moderation,
                    args=(action, _callback_message_ref(update)),
                    name=f"admin-{action}",
                    daemon=True,
                ).start()
            else:
                threading.Thread(
                    target=self._run_action,
                    args=(action,),
                    name=f"admin-{action}",
                    daemon=True,
                ).start()
        if callback_id and answer:
            self._answer_callback(client, callback_id, answer)
        for text, parse_mode in messages:
            self._send(client, text, parse_mode)

    def _run_action(self, action: str) -> None:
        settings = self._settings
        try:
            if action == "run_poll":
                from hepfeed.ingestion.pipeline import poll_arxiv_sync

                categories = [c.strip() for c in settings.arxiv_categories.split(",") if c.strip()]
                poll_arxiv_sync(
                    settings,
                    hours=settings.arxiv_poll_window_hours,
                    max_results=100,
                    categories=categories,
                )
            elif action == "run_notes":
                from hepfeed.generation.pipeline import generate_notes_sync

                generate_notes_sync(settings, limit=5)
            elif action == "run_publish":
                from hepfeed.publishing.pipeline import publish_notes_sync

                publish_notes_sync(settings, limit=10, moderation=self._moderation.is_set())
        except Exception:
            logger.exception("admin action %s failed", action)

    def _send(self, client: httpx.Client, text: str, parse_mode: str | None = None) -> None:
        token = self._settings.telegram_bot_token
        chat = self._settings.telegram_moderator_chat_id
        if not token or not chat:
            return
        payload: dict[str, object] = {
            "chat_id": chat,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            client.post(f"{_TELEGRAM_API_URL}/bot{token}/sendMessage", json=payload)
        except httpx.TransportError as exc:
            logger.warning("admin reply send failed: %s", exc)

    def _answer_callback(self, client: httpx.Client, callback_query_id: str, text: str) -> None:
        token = self._settings.telegram_bot_token
        if not token:
            return
        try:
            client.post(
                f"{_TELEGRAM_API_URL}/bot{token}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text},
            )
        except httpx.TransportError as exc:
            logger.warning("admin callback answer failed: %s", exc)

    def _clear_reply_markup(
        self, client: httpx.Client, chat_id: object, message_id: object
    ) -> None:
        token = self._settings.telegram_bot_token
        if not token:
            return
        try:
            client.post(
                f"{_TELEGRAM_API_URL}/bot{token}/editMessageReplyMarkup",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except httpx.TransportError as exc:
            logger.warning("admin clear reply markup failed: %s", exc)

    def _run_moderation(self, action: str, message_ref: tuple[object, object] | None) -> None:
        """Apply a moderation decision from inline buttons and update the UI."""
        try:
            status_line, applied = self._apply_moderation_decision(action)
        except Exception:
            logger.exception("moderation action %s failed", action)
            return
        with httpx.Client(timeout=30.0) as client:
            if applied and message_ref is not None:
                self._clear_reply_markup(client, message_ref[0], message_ref[1])
            self._send(client, status_line)

    def _apply_moderation_decision(self, action: str) -> tuple[str, bool]:
        """Run the decision in the store and publishing pipeline; return (status_line, applied)."""
        from hepfeed.ingestion.store import (  # lazy: avoids import cycles
            NOTE_STATUS_IN_REVIEW,
            NOTE_STATUS_READY,
            SeenStore,
        )

        _, decision, raw_id = action.split(":")
        note_id = int(raw_id)
        store = SeenStore(self._settings.ensure_data_dir())
        try:
            status = store.note_status(note_id)
        finally:
            store.close()
        if status not in (NOTE_STATUS_READY, NOTE_STATUS_IN_REVIEW):
            logger.warning("moderation skipped for note %d (status %s)", note_id, status)
            return f"Заметка {note_id}: уже обработана (статус: {status})", False

        from hepfeed.publishing.pipeline import apply_moderation_decision_sync

        approve = decision == "approve"
        result = apply_moderation_decision_sync(self._settings, note_id=note_id, approve=approve)
        if approve and result.published == 1:
            return f"Заметка {note_id}: опубликована", True
        if not approve and result.rejected == 1:
            return f"Заметка {note_id}: отклонена", True
        return f"Заметка {note_id}: действие не удалось (см. логи)", False
