from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from aissistant.config import Settings
from aissistant.models import Analysis, Message
from aissistant.store import Store, outbox
from aissistant.worker import BudgetExhausted, Worker


def analysis(priority="important"):
    return Analysis(
        categories=["клиент"],
        priority=priority,
        summary="Запрос",
        recommendation="Проверить",
        agreement="",
        draft="",
    )


class AI:
    metered = True
    last_usage = None

    def __init__(self, priority="important", fail=False):
        self.calls = []
        self.priority = priority
        self.fail = fail

    def reservation_tokens(self, text):
        return 1000, 100

    async def analyze(self, text):
        self.calls.append(text)
        self.last_usage = None
        if self.fail:
            raise ValueError("provider failed")
        self.last_usage = (100, 10)
        return analysis(self.priority)

    async def close(self):
        pass


class Sink:
    def __init__(self):
        self.items = []

    async def send(self, text):
        self.items.append(text)

    async def close(self):
        pass


@pytest.fixture
def db(database_url):
    store = Store(database_url)
    yield store
    store.close()


def worker(db, settings=None, ai=None):
    return Worker(settings or Settings(), db, [], ai or AI(), Sink())


def msg(id="1", text="Нужен заказ", **kwargs):
    return Message("telegram", "-42", id, text, "", sender="99", chat_type="group", **kwargs)


@pytest.mark.parametrize(
    "text",
    [
        "Код подтверждения: 123456",
        "OTP: 4321",
        "password=not-a-real-secret",
        "мой пароль synthetic-password",
        "api_key: sk-syntheticabcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----",
        "refresh_token=fake",
    ],
)
async def test_sensitive_is_dropped_before_ai(db, text):
    w = worker(db)
    m = msg(text=text)
    await w.accept(m)
    assert not w.classifier.calls
    assert db.contains(m) and not db.pending()


@pytest.mark.parametrize(
    "settings",
    [
        Settings(exclude_keywords=["заказ"]),
        Settings(privacy={"excluded_senders": ["99"]}),
        Settings(privacy={"excluded_chats": ["telegram:-42"]}),
    ],
)
async def test_local_exclusions(db, settings):
    w = worker(db, settings)
    await w.accept(msg())
    assert not w.classifier.calls and not db.pending()


@pytest.mark.parametrize(
    "policy,requests,pending", [("local_only", 0, 1), ("skip", 0, 0), ("allow_ai", 1, 1)]
)
async def test_private_policy_default_is_local(db, policy, requests, pending):
    w = worker(db, Settings(privacy={"private_chat_policy": policy}))
    m = Message("telegram", "123", "1", "PERSONAL_CANARY", "", "123", "private")
    await w.accept(m)
    assert len(w.classifier.calls) == requests
    assert len(db.pending()) == pending
    if policy == "local_only":
        assert db.pending()[0]["priority"] == "low"
        assert "PERSONAL_CANARY" not in db.pending()[0]["payload"]
    assert not w.notifier.items


async def test_budget_and_usage_survive_restart(db, database_url):
    settings = Settings(ai={"max_requests_per_day": 1})
    w = worker(db, settings)
    await w.accept(msg())
    day = datetime.now(UTC).date().isoformat()
    before = db.api_usage(day)
    assert before["requests"] == 1 and before["input_tokens"] == 100
    assert before["output_tokens"] == 10 and before["actual_micro_usd"] > 0
    assert before["reserved_micro_usd"] >= before["actual_micro_usd"]
    reopened = Store(database_url)
    try:
        other = worker(reopened, settings)
        await other.accept(msg())  # Dedup does not spend API budget.
        with pytest.raises(BudgetExhausted):
            await other.accept(msg("2"))
        assert not other.classifier.calls
        assert not reopened.contains(msg("2"))
        assert reopened.api_usage(day) == before
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "limits",
    [{"max_requests_per_cycle": 0}, {"max_requests_per_day": 0}, {"daily_budget_usd": "0"}],
)
async def test_zero_budget_defers_without_marking_seen(db, limits):
    w = worker(db, Settings(ai=limits))
    with pytest.raises(BudgetExhausted):
        await w.accept(msg())
    assert not db.contains(msg()) and not w.classifier.calls


async def test_failed_api_attempt_keeps_conservative_reservation(db):
    w = worker(db, Settings(ai={"max_requests_per_day": 1}), AI(fail=True))
    with pytest.raises(ValueError):
        await w.accept(msg())
    usage = db.api_usage(datetime.now(UTC).date().isoformat())
    assert usage["requests"] == 1 and usage["reserved_micro_usd"] > 0
    assert usage["actual_micro_usd"] == 0 and not db.contains(msg())
    with pytest.raises(BudgetExhausted):
        await w.accept(msg())


async def test_critical_sent_during_accept_and_low_requires_request(db):
    w = worker(db, ai=AI("critical"))
    await w.accept(msg())
    assert len(w.notifier.items) == 1 and not db.pending()
    w.classifier.priority = "low"
    await w.accept(msg("2"))
    assert len(w.notifier.items) == 1
    assert await w.cycle()
    assert len(w.notifier.items) == 1
    assert await w.request_low()
    assert len(w.notifier.items) == 2
    assert not db.pending()


async def test_important_digest_interval_and_restart_rate_limit(db, database_url, monkeypatch):
    import aissistant.worker as module

    clock = [10000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    settings = Settings(notifications={"max_per_hour": 1, "digest_seconds": 60})
    w = worker(db, settings)
    await w.accept(msg())
    await w.accept(msg("2"))
    assert not w.notifier.items
    await w.cycle()
    assert len(w.notifier.items) == 1 and not db.pending()
    assert w.notifier.items[0].count("Сводка") == 2
    await w.accept(msg("3"))
    clock[0] += 61
    reopened = Store(database_url)
    try:
        other = worker(reopened, settings)
        await other.cycle()
        assert not other.notifier.items and len(reopened.pending()) == 1
        clock[0] += 3600
        await other.cycle()
        assert len(other.notifier.items) == 1 and not reopened.pending()
    finally:
        reopened.close()


async def test_low_payload_expiry_preserves_dedup(db, monkeypatch):
    w = worker(db, ai=AI("low"))
    await w.accept(msg())
    with db.engine.begin() as c:
        c.execute(outbox.update().values(created=1))
    db.prune(1)
    assert not db.pending() and db.contains(msg())
    with db.engine.connect() as c:
        assert not c.execute(select(outbox)).all()


async def test_budget_deferral_preserves_source_cursor(db):
    class Source:
        async def poll(self, store, accept):
            if store.cursor("synthetic:position") == "0":
                await accept(msg())
                store.set_cursor("synthetic:position", "1")

    db.set_cursor("synthetic:position", "0")
    settings = Settings(ai={"max_requests_per_day": 0})
    w = worker(db, settings)
    w.sources = [Source()]
    assert not await w.cycle()
    assert db.cursor("synthetic:position") == "0" and not db.contains(msg())
    settings.ai.max_requests_per_day = 1
    assert await w.cycle()
    assert db.cursor("synthetic:position") == "1" and db.contains(msg())
    assert len(w.classifier.calls) == 1


async def test_critical_flood_cap_and_failed_send_do_not_loop(db):
    w = worker(db, Settings(notifications={"max_per_cycle": 1}), AI("critical"))
    for i in range(5):
        await w.accept(msg(str(i)))
    assert len(w.notifier.items) == 1 and len(db.pending()) == 4
    attempts = []

    async def fail(text):
        attempts.append(text)
        raise RuntimeError("do not retry within cycle")

    w.notifier.send = fail
    await w.cycle()
    assert len(attempts) == 1 and len(db.pending()) == 4
