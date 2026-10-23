import hashlib
import logging
import os
import time
from pathlib import Path

from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    text,
    update,
)

from .database import HEAD, make_engine, upgrade
from .models import Analysis, Message

meta = MetaData()
cursors = Table(
    "cursors", meta, Column("key", String(200), primary_key=True), Column("value", Text)
)
seen = Table("seen", meta, Column("key", String(64), primary_key=True))
outbox = Table(
    "outbox",
    meta,
    Column("key", String(64), primary_key=True),
    Column("payload", Text),
    Column("created", Float),
    Column("delivered", Float, nullable=True),
)


def message_key(m: Message) -> str:
    return hashlib.sha256(f"{m.source}\0{m.scope}\0{m.id}".encode()).hexdigest()


def render(m: Message, a: Analysis) -> str:
    return (
        f"Сводка · {', '.join(a.categories)}\n{a.summary}\n"
        f"Договорённость: {a.agreement or 'не обнаружена'}\n"
        f"Рекомендация: {a.recommendation}\nЧерновик (не отправлен): {a.draft or '—'}\n"
        f"Источник: {m.source} / {m.scope} / {m.id}\n{m.link or 'ссылка недоступна для личного чата'}"
    )


class Store:
    def __init__(self, url: str, *, worker_lock: bool = False):
        self.engine = make_engine(url)
        self._lease = None
        self._lock_file = None
        self._lease_key = None
        self._lease_pid = None
        try:
            if worker_lock:
                self._acquire_worker_lock()
            upgrade(self.engine)
            logging.getLogger("aissistant").info(
                "database_ready backend=%s revision=%s", self.engine.dialect.name, HEAD
            )
        except BaseException:
            self.close()
            raise

    def _acquire_worker_lock(self):
        if self.engine.dialect.name == "postgresql":
            self._lease = self.engine.connect()
            namespace = self._lease.execute(
                text("SELECT current_database() || ':' || current_schema()")
            ).scalar_one()
            self._lease_key = int.from_bytes(
                hashlib.sha256(("aissistant:" + namespace).encode()).digest()[:8], signed=True
            )
            locked = self._lease.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self._lease_key}
            ).scalar_one()
            if not locked:
                raise RuntimeError("Another worker owns this database")
            self._lease_pid = self._lease.execute(text("SELECT pg_backend_pid()")).scalar_one()
            self._lease.commit()
        else:
            import fcntl

            database = self.engine.url.database
            if database in (None, ":memory:") or self.engine.url.query.get("uri") == "true":
                raise ValueError("Worker requires a persistent SQLite database")
            lock_path = Path(str(Path(database).resolve()) + ".worker.lock")
            self._lock_file = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def check_worker_lock(self):
        if self._lease is not None:
            pid = self._lease.execute(text("SELECT pg_backend_pid()")).scalar_one()
            self._lease.commit()
            if pid != self._lease_pid:
                raise RuntimeError("Worker lock connection changed; stopping")

    def cursor(self, key: str) -> str | None:
        with self.engine.connect() as c:
            return c.execute(
                select(cursors.c.value).where(cursors.c.key == key)
            ).scalar_one_or_none()

    def set_cursor(self, key: str, value: str):
        with self.engine.begin() as c:
            if c.execute(select(cursors.c.key).where(cursors.c.key == key)).first():
                c.execute(update(cursors).where(cursors.c.key == key).values(value=value))
            else:
                c.execute(insert(cursors).values(key=key, value=value))

    def contains(self, m: Message):
        with self.engine.connect() as c:
            return (
                c.execute(select(seen.c.key).where(seen.c.key == message_key(m))).first()
                is not None
            )

    def save(self, m: Message, analysis: Analysis | None):
        key = message_key(m)
        with self.engine.begin() as c:
            if c.execute(select(seen.c.key).where(seen.c.key == key)).first():
                return
            c.execute(insert(seen).values(key=key))
            if analysis is not None:
                c.execute(
                    insert(outbox).values(
                        key=key,
                        payload=render(m, analysis),
                        created=time.time(),
                        delivered=None,
                    )
                )

    def pending(self):
        with self.engine.connect() as c:
            return (
                c.execute(
                    select(outbox)
                    .where(outbox.c.delivered.is_(None))
                    .order_by(outbox.c.created, outbox.c.key)
                    .limit(100)
                )
                .mappings()
                .all()
            )

    def delivered(self, key: str):
        with self.engine.begin() as c:
            c.execute(update(outbox).where(outbox.c.key == key).values(delivered=time.time()))

    def prune(self, days: int):
        with self.engine.begin() as c:
            c.execute(delete(outbox).where(outbox.c.delivered < time.time() - days * 86400))

    def close(self):
        try:
            if self._lease is not None:
                try:
                    if self._lease_pid is not None:
                        self._lease.execute(
                            text("SELECT pg_advisory_unlock(:key)"), {"key": self._lease_key}
                        )
                        self._lease.commit()
                finally:
                    self._lease.close()
                    self._lease = None
            if self._lock_file is not None:
                os.close(self._lock_file)
                self._lock_file = None
        finally:
            self.engine.dispose()
