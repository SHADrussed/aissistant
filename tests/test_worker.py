import logging

import pytest
from sqlalchemy import select

from aissistant.classifier import SyntheticClassifier
from aissistant.config import Settings
from aissistant.models import Analysis, Category, Message
from aissistant.notifier import SyntheticNotifier
from aissistant.sources import SyntheticSource
from aissistant.store import Store, outbox
from aissistant.worker import Worker


@pytest.fixture
def store(database_url):
    db = Store(database_url)
    yield db
    db.close()


class Capture:
    def __init__(self, fail=False):
        self.items = []
        self.fail = fail

    async def send(self, text):
        if self.fail:
            raise RuntimeError("SECRET token and private message")
        self.items.append(text)

    async def close(self):
        pass


def worker(store, **kwargs):
    return Worker(
        kwargs.get("settings", Settings()),
        store,
        [SyntheticSource()],
        kwargs.get("classifier", SyntheticClassifier()),
        kwargs.get("notifier", Capture()),
    )


async def test_synthetic_dedup_across_restart(store):
    sink = Capture()
    assert await worker(store, notifier=sink).cycle()
    assert len(sink.items) == 5
    # New worker, same durable database.
    assert await worker(store, notifier=sink).cycle()
    assert len(sink.items) == 5
    assert store.pending() == []
    assert "Черновик (не отправлен)" in sink.items[0]


async def test_failed_delivery_retries_without_reclassification(store, caplog):
    caplog.set_level(logging.INFO, logger="aissistant")
    sink = Capture(fail=True)
    assert not await worker(store, notifier=sink).cycle()
    assert len(store.pending()) == 5
    assert "SECRET" not in caplog.text
    assert "private message" not in caplog.text
    sink.fail = False
    assert await worker(store, notifier=sink).cycle()
    assert len(sink.items) == 5


async def test_classification_failure_does_not_mark_seen(store):
    class Bad:
        async def analyze(self, text):
            raise ValueError("bad model")

    assert not await worker(store, classifier=Bad()).cycle()
    assert not store.contains(Message("synthetic", "fixtures-v1", "0", "", ""))
    assert await worker(store).cycle()


async def test_filters_and_length_limit(store):
    settings = Settings(exclude_keywords=["private"], max_text_chars=5)
    seen_text = []

    class AI:
        async def analyze(self, text):
            seen_text.append(text)
            return await SyntheticClassifier().analyze(text)

    w = worker(store, settings=settings, classifier=AI())
    await w.accept(Message("test", "a", "1", "123456789", ""))
    assert seen_text == ["12345"]
    settings.max_text_chars = 100
    await w.accept(Message("test", "a", "2", "PRIVATE data", ""))
    assert len(store.pending()) == 1
    assert store.contains(Message("test", "a", "2", "", ""))


async def test_category_filter(store):
    settings = Settings(notify_categories=[Category.CLIENT])
    assert await worker(store, settings=settings).cycle()
    with store.engine.connect() as c:
        assert len(c.execute(select(outbox)).all()) == 1


async def test_store_excludes_raw_and_retention(store):
    secret = "raw-private-canary"
    m = Message("test", "a", "3", secret, "https://t.me/c/123/3")
    a = Analysis(
        categories=[Category.CLIENT],
        summary="Запрос",
        recommendation="Уточнить",
        draft="Спасибо",
        agreement="",
    )
    store.save(m, a)
    row = store.pending()[0]
    assert secret not in row["payload"]
    store.prune(0)
    assert len(store.pending()) == 1  # Never prune undelivered.
    store.delivered(row["key"])
    store.prune(0)
    assert store.contains(m)  # Tombstone remains after payload expiry.
    with store.engine.connect() as c:
        assert c.execute(select(outbox)).all() == []


async def test_failure_one_source_does_not_starve_other(store):
    class Bad:
        async def poll(self, store, accept):
            raise RuntimeError("source unavailable")

    sink = Capture()
    w = worker(store, notifier=sink)
    w.sources.insert(0, Bad())
    assert not await w.cycle()
    assert len(sink.items) == 5


async def test_notification_size_unicode():
    a = Analysis(
        categories=list(Category),
        summary="😀" * 240,
        recommendation="😀" * 200,
        draft="😀" * 300,
        agreement="😀" * 200,
    )
    from aissistant.store import render

    await SyntheticNotifier().send(render(Message("test", "a", "1", "", ""), a))


async def test_outbox_survives_database_reopen(tmp_path):
    url = f"sqlite:///{tmp_path}/restart.sqlite"
    first = Store(url)
    assert not await worker(first, notifier=Capture(fail=True)).cycle()
    first.close()
    second = Store(url)
    sink = Capture()
    assert await worker(second, notifier=sink).cycle()
    assert len(sink.items) == 5
    assert second.pending() == []
    second.close()
