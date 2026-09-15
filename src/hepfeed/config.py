"""Runtime configuration loaded from environment variables and the .env file.

Secrets never live in the repository: see ``.env.example`` for the template
and ``docs/SECURITY_NOTE.md`` for the policy.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_CRITICAL_SECRETS = ("polza_api_key", "telegram_bot_token")


class Settings(BaseSettings):
    """Pipeline settings. Field names map to UPPER_CASE environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider (note generation, context search)
    polza_api_key: str | None = None
    llm_base_url: str = "https://polza.ai/api/v1"
    llm_model: str = "deepseek/deepseek-v4-flash-0731@provider=open-inference/fp8"

    # Telegram
    telegram_bot_token: str | None = None
    telegram_channel_hep: str | None = None
    telegram_channel_ap: str | None = None
    telegram_moderator_chat_id: str | None = None

    # Storage
    database_url: str = "sqlite:///data/hepfeed.db"

    # Ingestion scheduling (CONCEPT.md section 8: delay target <= 12 hours)
    arxiv_poll_interval_minutes: int = 360
    arxiv_poll_window_hours: float = 24.0
    arxiv_categories: str = "hep-ex,hep-ph,physics.acc-ph"
    notes_interval_minutes: int = 15

    # Misc
    log_level: str = "INFO"

    def missing_critical(self) -> list[str]:
        """Names of secret settings that must be configured before the pipeline runs."""
        return [name for name in _CRITICAL_SECRETS if not getattr(self, name)]

    def ensure_data_dir(self) -> Path:
        """Create the storage directory and return the database file path.

        For SQLite URLs the parent directory of the database file is created;
        otherwise a generic ``data`` directory is used.
        """
        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return db_path
        Path("data").mkdir(parents=True, exist_ok=True)
        return Path("data")
