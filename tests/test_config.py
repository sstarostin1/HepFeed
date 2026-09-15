"""Tests for Settings loading and diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from hepfeed.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.database_url == "sqlite:///data/hepfeed.db"
    assert settings.arxiv_poll_interval_minutes == 360
    assert settings.arxiv_poll_window_hours == 24.0
    assert settings.arxiv_categories == "hep-ex,hep-ph,physics.acc-ph"
    assert settings.notes_interval_minutes == 15
    assert settings.llm_base_url == "https://polza.ai/api/v1"
    assert settings.llm_model.startswith("deepseek/")
    assert settings.log_level == "INFO"
    assert settings.telegram_bot_token is None
    assert settings.polza_api_key is None
    assert set(settings.missing_critical()) == {"polza_api_key", "telegram_bot_token"}


def test_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/other.sqlite")
    settings = Settings(_env_file=None)
    assert settings.polza_api_key == "test-key"
    assert settings.telegram_bot_token == "test-token"
    assert settings.database_url == "sqlite:///data/other.sqlite"
    assert settings.missing_critical() == []


def test_missing_critical_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    settings = Settings(_env_file=None)
    assert settings.missing_critical() == ["polza_api_key"]


def test_ensure_data_dir_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, database_url="sqlite:///data/nested/hepfeed.sqlite")
    db_path = settings.ensure_data_dir()
    assert db_path == Path("data/nested/hepfeed.sqlite")
    assert db_path.parent.is_dir()


def test_ensure_data_dir_non_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, database_url="postgresql://localhost/hepfeed")
    assert settings.ensure_data_dir() == Path("data")
    assert Path("data").is_dir()
