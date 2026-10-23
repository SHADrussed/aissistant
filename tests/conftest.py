import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from aissistant.database import make_engine


@pytest.fixture
def postgres_url():
    raw = os.getenv("TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("PostgreSQL not configured; use tools/test_postgres.py")
    engine = make_engine(raw)
    # Never remove existing application tables; each test owns only its new random schema.
    namespace = "test_aissistant_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
    url = make_url(raw).update_query_dict({"options": f"-csearch_path={namespace}"})
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def database_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path}/state.db"
    return request.getfixturevalue("postgres_url")
