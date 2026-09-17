"""Operator console over Telegram: status, pause/resume, manual runs.

The listener is a long-polling daemon thread inside the scheduler process.
Only the configured moderator chat is served; anything else is ignored
(operator interaction per docs/CONCEPT.md, section 6.5 - not a reader bot).
"""

from __future__ import annotations

import logging
import threading

import httpx

from hepfeed.config import Settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_URL = "https://api.telegram.org"

_HELP_TEXT = (
    "Команды администратора HepFeed:\n"
    "/status - состояние конвейера\n"
    "/pause - приостановить периодические задачи\n"
    "/resume - возобновить работу\n"
    "/run poll - опросить arXiv сейчас\n"
    "/run notes - сгенерировать заметки сейчас\n"
    "/run publish - опубликовать готовые заметки\n"
    "/help - эта справка"
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


def handle_update(
    update: dict[str, object], settings: Settings, flag: PauseFlag
) -> tuple[str | None, str | None]:
    """Process one Telegram update; return (reply_text, background_action)."""
    message = update.get("message") or {}
    if not isinstance(message, dict):
        return None, None
    chat = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if chat is None or not text:
        return None, None
    moderator = str(settings.telegram_moderator_chat_id or "")
    if not moderator or str(chat) != moderator:
        logger.warning("admin listener: ignored message from chat %s", chat)
        return None, None
    command = text.split()[0].split("@")[0].lower()
    if command == "/help":
        return _HELP_TEXT, None
    if command == "/status":
        return _status_reply(settings, flag), None
    if command == "/pause":
        flag.set()
        return "Пауза: периодические задачи пропускаются до /resume", None
    if command == "/resume":
        flag.clear()
        return "Работа возобновлена", None
    if command == "/run":
        parts = text.split()
        target = parts[1].lower() if len(parts) > 1 else ""
        if target in ("poll", "notes", "publish"):
            return f"Запущено в фоне: {target}", f"run_{target}"
        return "Укажите задачу: /run poll | notes | publish", None
    return "Неизвестная команда. Доступно: /status, /pause, /resume, /run, /help", None


def _status_reply(settings: Settings, flag: PauseFlag) -> str:
    from hepfeed.ingestion.store import SeenStore  # lazy: avoids import cycles

    paused = "да" if flag.is_set() else "нет"
    try:
        stats = SeenStore(settings.ensure_data_dir()).stats()
    except Exception as exc:  # pragma: no cover - diagnostics only
        logger.warning("status query failed: %s", exc)
        return f"Пауза: {paused}; статистика недоступна ({exc})"
    return (
        f"Пауза: {paused}\n"
        f"Статей в БД: {stats['papers']} (ожидают заметку: {stats['papers_pending_note']})\n"
        f"Заметки: готово к публикации {stats['notes_ready']}, "
        f"на модерации {stats['notes_in_review']}, "
        f"опубликовано {stats['notes_published']}, "
        f"отклонено {stats['notes_rejected']}"
    )


class AdminListener:
    """Long-polling Telegram thread that serves operator commands."""

    def __init__(self, settings: Settings, flag: PauseFlag) -> None:
        self._settings = settings
        self._flag = flag
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
            reply, action = handle_update(update, self._settings, self._flag)
        except Exception:
            logger.exception("admin command processing failed")
            return
        if action:
            threading.Thread(
                target=self._run_action,
                args=(action,),
                name=f"admin-{action}",
                daemon=True,
            ).start()
        if reply:
            self._send(client, reply)

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

                publish_notes_sync(settings, limit=10)
        except Exception:
            logger.exception("admin action %s failed", action)

    def _send(self, client: httpx.Client, text: str) -> None:
        token = self._settings.telegram_bot_token
        chat = self._settings.telegram_moderator_chat_id
        if not token or not chat:
            return
        try:
            client.post(
                f"{_TELEGRAM_API_URL}/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
            )
        except httpx.TransportError as exc:
            logger.warning("admin reply send failed: %s", exc)
