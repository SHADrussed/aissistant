import pytest
from alembic.util.exc import CommandError
from sqlalchemy import MetaData, Table, event, insert, inspect, select

from aissistant.database import HEAD, copy_offline, make_engine, revision, upgrade
from aissistant.models import Analysis, Message
from aissistant.store import Store, cursors, outbox, seen


def sample():
    return Message("synthetic", "test", "1", "never persist raw body", ""), Analysis(
        categories=["клиент"],
        summary="Тест",
        recommendation="Уточнить",
        draft="Спасибо",
        agreement="",
    )


def test_fresh_migration_and_idempotence(database_url):
    store = Store(database_url)
    message, analysis = sample()
    store.save(message, analysis)
    store.set_cursor("probe", "42")
    assert revision(store.engine) == HEAD
    upgrade(store.engine)
    assert store.cursor("probe") == "42"
    assert store.contains(message) and len(store.pending()) == 1
    store.close()
    reopened = Store(database_url)
    assert reopened.contains(message) and len(reopened.pending()) == 1
    assert reopened.cursor("probe") == "42"
    reopened.close()


def test_legacy_schema_adopted_without_rewriting(database_url):
    engine = make_engine(database_url)
    import importlib

    legacy = importlib.import_module("aissistant.migrations.versions.0001_initial")
    legacy.schema().create_all(engine)  # Original MVP, before Alembic existed.
    with engine.begin() as c:
        c.execute(insert(cursors).values(key="old", value="123"))
        c.execute(insert(seen).values(key="k"))
        c.execute(
            insert(outbox).values(
                key="k", payload="sensitive synthetic result", created=100.25, delivered=None
            )
        )
    assert revision(engine) is None
    upgrade(engine)
    assert revision(engine) == HEAD
    with engine.connect() as c:
        assert c.execute(select(outbox)).mappings().one()["payload"] == "sensitive synthetic result"
    store = Store(database_url)
    assert store.cursor("old") == "123" and len(store.pending()) == 1
    store.close()
    engine.dispose()


def test_incomplete_legacy_schema_is_not_silently_stamped(database_url):
    engine = make_engine(database_url)
    cursors.create(engine)
    with pytest.raises(RuntimeError, match="Incomplete"):
        upgrade(engine)
    assert revision(engine) is None
    assert "outbox" not in inspect(engine).get_table_names()
    engine.dispose()


def test_future_revision_is_refused(database_url):
    store = Store(database_url)
    versions = Table("alembic_version", MetaData(), autoload_with=store.engine)
    with store.engine.begin() as c:
        c.execute(versions.update().values(version_num="unknown_future"))
    store.close()
    with pytest.raises(CommandError):
        Store(database_url)


def test_seen_and_outbox_are_atomic(database_url):
    store = Store(database_url)
    message, analysis = sample()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox"):
            raise RuntimeError("simulated storage failure")

    event.listen(store.engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError):
        store.save(message, analysis)
    event.remove(store.engine, "before_cursor_execute", fail)
    assert not store.contains(message)
    assert store.pending() == []
    store.save(message, analysis)
    assert store.contains(message)
    store.close()


def test_one_worker_per_database(database_url):
    first = Store(database_url, worker_lock=True)
    try:
        first.check_worker_lock()
        with pytest.raises((RuntimeError, BlockingIOError)):
            Store(database_url, worker_lock=True)
    finally:
        first.close()
    next_worker = Store(database_url, worker_lock=True)
    next_worker.check_worker_lock()
    next_worker.close()


def test_sqlite_to_postgres_preserves_queue_and_refuses_merge(tmp_path, postgres_url):
    source_url = f"sqlite:///{tmp_path}/source.db"
    source = Store(source_url)
    m, a = sample()
    source.save(m, a)
    source.save(Message("synthetic", "test", "2", "", ""), a)
    delivered_key = source.pending()[0]["key"]
    source.delivered(delivered_key)
    source.set_cursor("telegram:123", "50")
    assert source.reserve_api("2026-10-27", 100, 10, 1000)
    source.cache_peer(123, 456, "private", 50)
    source.close()
    counts = copy_offline(source_url, postgres_url)
    assert counts == {"cursors": 1, "outbox": 2, "seen": 2, "api_usage": 1, "telegram_peers": 1}
    target = Store(postgres_url)
    assert target.cursor("telegram:123") == "50"
    assert target.api_usage("2026-10-27")["reserved_micro_usd"] == 100
    assert target.cached_peers()[0]["access_hash"] == "456"
    assert target.contains(m) and len(target.pending()) == 1
    assert target.pending()[0]["key"] != delivered_key
    before = target.pending()
    with pytest.raises(ValueError, match="not empty"):
        copy_offline(source_url, postgres_url)
    assert target.pending() == before
    target.close()
    # Source retained exactly, including its pending row.
    original = Store(source_url)
    assert len(original.pending()) == 1 and original.cursor("telegram:123") == "50"
    original.close()


def test_upgrade_versioned_v1_preserves_all_state(database_url):
    import importlib

    from sqlalchemy import Column, String

    engine = make_engine(database_url)
    legacy = importlib.import_module("aissistant.migrations.versions.0001_initial").schema()
    legacy.create_all(engine)
    versions = Table(
        "alembic_version", MetaData(), Column("version_num", String(32), primary_key=True)
    )
    versions.create(engine)
    with engine.begin() as c:
        c.execute(insert(versions).values(version_num="0001_initial"))
        c.execute(insert(legacy.tables["cursors"]).values(key="gmail:history", value="retained"))
        c.execute(insert(legacy.tables["seen"]).values(key="dedup"))
        c.execute(
            insert(legacy.tables["outbox"]),
            [
                dict(key="pending", payload="preserved", created=123.0, delivered=None),
                dict(key="delivered", payload="delivered", created=124.0, delivered=125.0),
            ],
        )
    upgrade(engine)
    assert revision(engine) == HEAD
    with engine.connect() as c:
        rows = c.execute(select(outbox).order_by(outbox.c.key)).mappings().all()
        assert [(r["key"], r["payload"], r["delivered"], r["priority"]) for r in rows] == [
            ("delivered", "delivered", 125.0, "important"),
            ("pending", "preserved", None, "important"),
        ]
        assert c.execute(select(cursors.c.value)).scalar_one() == "retained"
        assert c.execute(select(seen.c.key)).scalar_one() == "dedup"
    engine.dispose()
