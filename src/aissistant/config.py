import json
import os
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Category


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Schedule(Strict):
    timezone: str = "Europe/Moscow"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    start_hour: int = Field(default=0, ge=0, le=23)
    end_hour: int = Field(default=24, ge=1, le=24)

    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.start_hour >= self.end_hour or any(d not in range(7) for d in self.weekdays):
            raise ValueError("Invalid schedule")
        return self


class Telegram(Strict):
    enabled: bool = False
    allowed_chat_ids: list[int] = Field(default_factory=list)
    batch_size: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def valid(self):
        if any(x == 0 for x in self.allowed_chat_ids):
            raise ValueError("Invalid Telegram ID")
        return self


class Gmail(Strict):
    enabled: bool = False
    label_ids: list[str] = Field(default_factory=lambda: ["INBOX"], min_length=1)


class AI(Strict):
    model: str = "gpt-4o-mini"


class Settings(Strict):
    mode: str = "synthetic"
    poll_seconds: int = Field(default=900, ge=10, le=86400)
    max_text_chars: int = Field(default=12000, ge=1, le=30000)
    retention_days: int = Field(default=7, ge=1, le=365)
    notify_categories: list[Category] = Field(default_factory=lambda: list(Category))
    exclude_keywords: list[str] = Field(default_factory=list)
    schedule: Schedule = Field(default_factory=Schedule)
    telegram: Telegram = Field(default_factory=Telegram)
    gmail: Gmail = Field(default_factory=Gmail)
    ai: AI = Field(default_factory=AI)

    @model_validator(mode="after")
    def valid(self):
        if self.mode not in {"synthetic", "live"}:
            raise ValueError("Invalid mode")
        return self

    def database_url(self):
        # Never accidentally insert synthetic fixtures into the personal database.
        if self.mode == "synthetic":
            return os.getenv("SYNTHETIC_DATABASE_URL", "sqlite:///.data/synthetic.db")
        return required("LIVE_DATABASE_URL")

    def validate_live(self):
        if self.mode != "live":
            return
        if os.getenv("ALLOW_LIVE_READS") != "true":
            raise ValueError("ALLOW_LIVE_READS required")
        if os.getenv("ALLOW_AI_PROCESSING") != "true":
            raise ValueError("ALLOW_AI_PROCESSING required")
        if not (self.telegram.enabled or self.gmail.enabled):
            raise ValueError("Enable at least one source")
        if self.telegram.enabled and not self.telegram.allowed_chat_ids:
            raise ValueError("Telegram allowlist required")
        keys = ["OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID"]
        if self.telegram.enabled:
            keys += ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"]
        if self.gmail.enabled:
            keys += ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"]
        for key in keys:
            required(key)
        if int(required("TELEGRAM_OWNER_CHAT_ID")) <= 0:
            raise ValueError("Private owner ID required")
        if self.telegram.enabled and int(required("TELEGRAM_API_ID")) <= 0:
            raise ValueError("Invalid API ID")
        if self.telegram.enabled:
            hashes = json.loads(os.getenv("TELEGRAM_PEER_HASHES", "{}"))
            if not isinstance(hashes, dict):
                raise ValueError("Telegram peer hashes must be an object")
            for chat in self.telegram.allowed_chat_ids:
                if chat > 0 or chat <= -1000000000000:
                    if str(chat) not in hashes or type(hashes[str(chat)]) is not int:
                        raise ValueError("Missing explicit Telegram peer access hash")
        self.database_url()


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing {name}")
    return value


def load(path: str | None = None) -> Settings:
    inline = os.getenv("ASSISTANT_CONFIG_TOML")
    if inline is not None:
        if path is not None:
            raise ValueError("Choose inline TOML or a config path, not both")
        data = tomllib.loads(inline)
    else:
        with Path(path or "config/default.toml").open("rb") as stream:
            data = tomllib.load(stream)
    settings = Settings.model_validate(data)
    settings.validate_live()
    return settings
