from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aissistant.config import Settings, load
from aissistant.models import Analysis
from aissistant.sources import scheduled


@pytest.fixture
def live_env(monkeypatch):
    for name in [
        "ALLOW_LIVE_READS",
        "ALLOW_AI_PROCESSING",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        "OPENAI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_ID",
        "LIVE_DATABASE_URL",
    ]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_are_offline():
    s = load("config/default.toml")
    assert s.mode == "synthetic" and not s.telegram.enabled and not s.gmail.enabled
    assert s.poll_seconds == 900


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "production"},
        {"poll_seconds": 0},
        {"schedule": {"start_hour": 20, "end_hour": 10}},
        {"schedule": {"weekdays": [7]}},
        {"notify_categories": ["unknown"]},
        {"typo": True},
    ],
)
def test_invalid_settings(data):
    with pytest.raises((ValidationError, ValueError)):
        Settings.model_validate(data)


def test_live_fails_closed(live_env):
    with pytest.raises(ValueError, match="ALLOW_LIVE_READS"):
        Settings(mode="live", telegram={"enabled": True}).validate_live()
    live_env.setenv("ALLOW_LIVE_READS", "true")
    with pytest.raises(ValueError, match="ALLOW_AI_PROCESSING"):
        Settings(mode="live").validate_live()
    live_env.setenv("ALLOW_AI_PROCESSING", "true")
    with pytest.raises(ValueError, match="allowlist"):
        Settings(mode="live", telegram={"enabled": True}).validate_live()


def test_separate_database(monkeypatch):
    monkeypatch.setenv("LIVE_DATABASE_URL", "sqlite:///personal.db")
    monkeypatch.delenv("SYNTHETIC_DATABASE_URL", raising=False)
    assert Settings().database_url() == "sqlite:///.data/synthetic.db"


def test_schedule_timezone():
    s = Settings(
        schedule={"timezone": "Europe/Moscow", "weekdays": [0], "start_hour": 9, "end_hour": 18}
    )
    assert scheduled(s, datetime(2026, 10, 19, 6, tzinfo=UTC))
    assert not scheduled(s, datetime(2026, 10, 19, 15, tzinfo=UTC))
    assert not scheduled(s, datetime(2026, 10, 18, 6, tzinfo=UTC))


@pytest.mark.parametrize(
    "field,value",
    [
        ("categories", ["evil"]),
        ("summary", "x" * 241),
        ("draft", "x" * 301),
        ("categories", []),
        ("tools", ["send_message"]),
    ],
)
def test_untrusted_model_output_rejected(field, value):
    data = dict(categories=["другое"], summary="ok", recommendation="ok", draft="", agreement="")
    data[field] = value
    with pytest.raises(ValidationError):
        Analysis.model_validate(data)


def test_inline_toml_for_browser_settings(monkeypatch):
    monkeypatch.setenv("ASSISTANT_CONFIG_TOML", 'mode="synthetic"\npoll_seconds=15')
    assert load().poll_seconds == 15
    with pytest.raises(ValueError, match="not both"):
        load("config/default.toml")
