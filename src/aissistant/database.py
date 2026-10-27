"""Versioned schema and offline SQLite → PostgreSQL copy; no credential logging."""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.engine import make_url

HEAD = "0002_monitoring"


def make_engine(url):
    parsed = make_url(url)
    if parsed.drivername in {"postgres", "postgresql"}:
        parsed = parsed.set(drivername="postgresql+psycopg")
    if parsed.get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("Unsupported database backend")
    if parsed.get_backend_name() == "sqlite":
        if parsed.database not in (None, ":memory:") and parsed.query.get("uri") != "true":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connect_args = {"timeout": 20}
    else:
        connect_args = {"connect_timeout": 10}
    return create_engine(
        parsed, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args
    )


def revision(engine):
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def fingerprint(connection, table):
    result = hashlib.sha256()
    count = 0
    for row in connection.execute(select(table).order_by(table.c.key)).mappings():
        result.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode())
        result.update(b"\n")
        count += 1
    return count, result.digest()


def copy_offline(source_url, target_url):
    """Caller must stop all workers. Preserve state, including delivered and pending rows."""
    from .store import meta

    if make_url(source_url).get_backend_name() != "sqlite":
        raise ValueError("Source must be SQLite")
    if make_url(target_url).get_backend_name() not in {"postgres", "postgresql"}:
        raise ValueError("Target must be PostgreSQL")
    parsed = make_url(source_url)
    path = Path(parsed.database or "").resolve(strict=True)
    # Open source read-only: neither schema migration nor accidental writes are possible.
    readonly = parsed.set(database=path.as_uri()).update_query_dict({"mode": "ro", "uri": "true"})
    source = make_engine(readonly)
    target = make_engine(target_url)
    try:
        if revision(source) != HEAD:
            raise ValueError("Upgrade source schema first, after taking a backup")
        upgrade(target)
        counts = {}
        with source.connect() as src, target.begin() as dst:
            src.exec_driver_sql("BEGIN")  # SQLite snapshot even for read-only SELECTs.
            # Refuse all data before writing any rows. Never merge/overwrite a target.
            for table in meta.sorted_tables:
                if dst.scalar(select(func.count()).select_from(table)):
                    raise ValueError("Target is not empty")
            for table in meta.sorted_tables:
                rows = src.execute(select(table).order_by(table.c.key)).mappings()
                while batch := rows.fetchmany(500):
                    dst.execute(insert(table), [dict(row) for row in batch])
                src_fingerprint = fingerprint(src, table)
                if fingerprint(dst, table) != src_fingerprint:
                    raise RuntimeError("Copy verification failed; transaction rolled back")
                counts[table.name] = src_fingerprint[0]
            src.rollback()
        return counts
    finally:
        source.dispose()
        target.dispose()


def main():
    import os

    from .config import required
    from .main import setup_logging

    os.umask(0o077)
    setup_logging()
    parser = argparse.ArgumentParser(description="Schema and offline database migration")
    parser.add_argument("action", choices=["upgrade", "check", "copy", "usage"])
    parser.add_argument("--confirm-offline", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "copy":
            if not args.confirm_offline:
                raise ValueError("Stop workers and confirm offline migration")
            counts = copy_offline(
                required("MIGRATION_SOURCE_URL"), required("MIGRATION_TARGET_URL")
            )
            print(json.dumps({"copied_rows": counts}))
        else:
            # This CLI never selects a personal DB implicitly.
            engine = make_engine(required("MIGRATION_DATABASE_URL"))
            try:
                if args.action == "upgrade":
                    upgrade(engine)
                if revision(engine) != HEAD:
                    raise ValueError("Database schema is not current")
                if args.action == "usage":
                    from datetime import UTC, datetime

                    from .store import usage

                    day = datetime.now(UTC).date().isoformat()
                    with engine.connect() as c:
                        row = c.execute(select(usage).where(usage.c.key == day)).mappings().first()
                        print(json.dumps(dict(row) if row else {"key": day, "requests": 0}))
                else:
                    print(json.dumps({"revision": HEAD}))
            finally:
                engine.dispose()
    except Exception:
        logging.getLogger("aissistant").error("database_operation_failed")
        raise SystemExit(1) from None
