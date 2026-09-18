"""Tests for the operator console commands (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hepfeed.admin import (
    ModerationFlag,
    PauseFlag,
    handle_callback,
    handle_update,
    moderation_keyboard,
    parse_moderation_callback,
)
from hepfeed.config import Settings
from hepfeed.ingestion.models import PaperRecord
from hepfeed.ingestion.store import SeenStore


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    return Settings(
        _env_file=None,
        telegram_moderator_chat_id="12345",
        database_url="sqlite:///data/hepfeed.db",
    )


def _update(text: str, chat: str = "12345") -> dict[str, object]:
    chat_id: object = int(chat) if chat.isdigit() else chat
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_help(env: Settings) -> None:
    messages, action = handle_update(_update("/help"), env, PauseFlag())
    assert action is None
    assert "/run_poll" in messages[0][0]
    assert "/prompt_set" in messages[0][0]


def test_status_reports_counts(env: Settings) -> None:
    Path("data").mkdir()
    store = SeenStore("data/hepfeed.db")
    paper = PaperRecord(arxiv_id="2609.00001", title="T", abstract="A")
    store.mark_seen(paper)
    store.save_note(paper, "note", "m")
    store.close()

    messages, action = handle_update(_update("/status"), env, PauseFlag())
    assert action is None
    assert "Статей в БД: 1" in messages[0][0]
    assert "Модерация: выкл" in messages[0][0]


def test_pause_resume(env: Settings) -> None:
    flag = PauseFlag()
    messages, action = handle_update(_update("/pause"), env, flag)
    assert flag.is_set() and messages and action is None
    handle_update(_update("/resume"), env, flag)
    assert not flag.is_set()


def test_run_dispatch(env: Settings) -> None:
    for command, target in (
        ("/run_poll", "poll"),
        ("/run_notes", "notes"),
        ("/run_publish", "publish"),
    ):
        messages, action = handle_update(_update(command), env, PauseFlag())
        assert action == f"run_{target}"
        assert messages and target in messages[0][0]


def test_unknown_chat_ignored(env: Settings) -> None:
    messages, action = handle_update(_update("/status", chat="999"), env, PauseFlag())
    assert not messages and action is None


def test_unknown_command(env: Settings) -> None:
    messages, _ = handle_update(_update("/whatever"), env, PauseFlag())
    assert messages and "Неизвестная команда" in messages[0][0]


# --- Moderation mode switch ---


def test_moderation_switch(env: Settings) -> None:
    moderation = ModerationFlag(False)
    messages, action = handle_update(_update("/moderation on"), env, PauseFlag(), moderation)
    assert action is None and moderation.is_set()
    assert "включена" in messages[0][0]

    messages, _ = handle_update(_update("/moderation off"), env, PauseFlag(), moderation)
    assert not moderation.is_set()
    assert "выключена" in messages[0][0]

    messages, _ = handle_update(_update("/moderation"), env, PauseFlag(), moderation)
    assert "Модерация: выкл" in messages[0][0]


def test_moderation_state_shown_in_status(env: Settings) -> None:
    moderation = ModerationFlag(True)
    messages, _ = handle_update(_update("/status"), env, PauseFlag(), moderation)
    assert "Модерация: вкл" in messages[0][0]


# --- System prompt viewing and replacement ---


def test_prompt_shows_builtin_prompt(env: Settings) -> None:
    messages, action = handle_update(_update("/prompt"), env, PauseFlag())
    assert action is None
    text, parse_mode = messages[0]
    assert parse_mode == "HTML"
    assert "встроенный" in text
    assert "<pre>" in text and "</pre>" in text


def test_prompt_set_via_reply(env: Settings) -> None:
    from hepfeed.generation.prompt import load_system_prompt

    update = _update("/prompt_set")
    message = update["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {"from": {"is_bot": True}, "text": "Новый промпт"}

    messages, action = handle_update(update, env, PauseFlag())
    assert action is None
    assert "обновлён (12 символов)" in messages[0][0]

    prompt, is_custom = load_system_prompt(env)
    assert is_custom and prompt == "Новый промпт"


def test_prompt_set_wraps_code_fences(env: Settings) -> None:
    from hepfeed.generation.prompt import load_system_prompt

    update = _update("/prompt_set")
    message = update["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {"from": {"is_bot": True}, "text": "```\nПромпт\n```"}

    messages, _ = handle_update(update, env, PauseFlag())
    assert "обновлён" in messages[0][0]

    prompt, _ = load_system_prompt(env)
    assert prompt == "Промпт"


def test_prompt_set_requires_reply(env: Settings) -> None:
    messages, _ = handle_update(_update("/prompt_set"), env, PauseFlag())
    assert "reply" in messages[0][0]


def test_prompt_set_reply_to_human_ignored(env: Settings) -> None:
    update = _update("/prompt_set")
    message = update["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {"from": {"is_bot": False}, "text": "подделка"}

    messages, _ = handle_update(update, env, PauseFlag())
    assert "reply" in messages[0][0]


def test_prompt_reset(env: Settings) -> None:
    from hepfeed.generation.prompt import save_system_prompt

    save_system_prompt(env, "временный")
    messages, _ = handle_update(_update("/prompt_reset"), env, PauseFlag())
    assert "удалён" in messages[0][0]
    messages, _ = handle_update(_update("/prompt_reset"), env, PauseFlag())
    assert "не был задан" in messages[0][0]


# --- Inline moderation buttons (callback_query) ---


def _callback_update(data: str, from_id: str = "12345", message_id: int = 10) -> dict[str, object]:
    sender: object = int(from_id) if from_id.isdigit() else from_id
    return {
        "callback_query": {
            "id": "cbq1",
            "from": {"id": sender},
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": sender}},
        }
    }


def test_moderation_keyboard_structure() -> None:
    keyboard = moderation_keyboard(5)
    buttons = keyboard["inline_keyboard"][0]
    assert [button["text"] for button in buttons] == ["Опубликовать", "Отклонить"]
    assert [button["callback_data"] for button in buttons] == ["note:5:approve", "note:5:reject"]


def test_parse_moderation_callback_variants() -> None:
    assert parse_moderation_callback("note:7:approve") == (True, 7)
    assert parse_moderation_callback("note:7:reject") == (False, 7)
    assert parse_moderation_callback("note:7:whatever") is None
    assert parse_moderation_callback("note:x:approve") is None
    assert parse_moderation_callback("other:7:approve") is None
    assert parse_moderation_callback(None) is None


def test_callback_approved(env: Settings) -> None:
    callback_id, answer, action = handle_callback(_callback_update("note:7:approve"), env)
    assert callback_id == "cbq1"
    assert action == "moderate:approve:7"
    assert answer is not None and "7" in answer


def test_callback_rejected(env: Settings) -> None:
    _, _, action = handle_callback(_callback_update("note:8:reject"), env)
    assert action == "moderate:reject:8"


def test_callback_from_stranger_denied(env: Settings) -> None:
    _, answer, action = handle_callback(_callback_update("note:7:approve", from_id="999"), env)
    assert action is None
    assert answer == "Недостаточно прав"


def test_callback_malformed_data(env: Settings) -> None:
    _, _, action = handle_callback(_callback_update("bogus"), env)
    assert action is None
