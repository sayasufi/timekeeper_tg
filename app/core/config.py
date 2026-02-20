from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TimeKeeper"
    app_env: str = "dev"
    debug: bool = False
    log_level: str = "INFO"

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_base_url: str = Field(default="", alias="TELEGRAM_WEBHOOK_BASE_URL")
    telegram_webhook_path: str = Field(default="/webhook/telegram", alias="TELEGRAM_WEBHOOK_PATH")
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/timekeeper",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    llm_base_url: str = Field(default="http://localhost:8100", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    stt_base_url: str = Field(default="http://localhost:8200", alias="STT_BASE_URL")
    stt_api_key: str = Field(default="", alias="STT_API_KEY")

    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    export_dir: Path = Field(default=Path("exports"), alias="EXPORT_DIR")

    celery_broker_url: str = Field(default="redis://localhost:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2",
        alias="CELERY_RESULT_BACKEND",
    )

    scheduler_poll_seconds: int = Field(default=60, alias="SCHEDULER_POLL_SECONDS")
    max_reminder_lookahead_minutes: int = Field(default=120, alias="MAX_REMINDER_LOOKAHEAD_MINUTES")
    outbox_max_attempts: int = Field(default=5, alias="OUTBOX_MAX_ATTEMPTS")
    outbox_backoff_base_seconds: int = Field(default=30, alias="OUTBOX_BACKOFF_BASE_SECONDS")
    outbox_backoff_max_seconds: int = Field(default=1800, alias="OUTBOX_BACKOFF_MAX_SECONDS")
    outbox_dedupe_ttl_seconds: int = Field(default=86400, alias="OUTBOX_DEDUPE_TTL_SECONDS")
    schedule_cache_ttl_seconds: int = Field(default=90, alias="SCHEDULE_CACHE_TTL_SECONDS")

    @property
    def telegram_webhook_url(self) -> str:
        if not self.telegram_webhook_base_url:
            return ""
        return f"{self.telegram_webhook_base_url.rstrip('/')}{self.telegram_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
