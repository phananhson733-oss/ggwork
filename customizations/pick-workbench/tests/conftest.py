import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pg
import pytest
import pytest_asyncio
from engines import HOST_JSON_SERIALIZER, host_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Exercise the pinned host API source; production installs the workspace package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend/packages/extension-api"))

# Session-level settings outlive the transaction on a pooled connection (plan 6.7); only SET LOCAL is allowed.
_SESSION_SETTING = re.compile(r"(?is)^\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")


def _refuse_session_settings(conn, cursor, statement, parameters, context, executemany):
    if _SESSION_SETTING.search(statement):
        raise AssertionError(f"session-level setting on a pooled connection, use SET LOCAL: {statement[:200]}")


def _refuse_foreign_json_serializer(conn, cursor, statement, parameters, context, executemany):
    if conn.dialect._json_serializer is not HOST_JSON_SERIALIZER:
        raise AssertionError("engine does not serialize JSON like the host's; create it with engines.host_engine")


@pytest.fixture(scope="session", autouse=True)
def _transaction_scoped_settings_only():
    """Every statement any test sends through SQLAlchemy, on either dialect."""
    event.listen(Engine, "before_cursor_execute", _refuse_session_settings)
    yield
    event.remove(Engine, "before_cursor_execute", _refuse_session_settings)


@pytest.fixture(scope="session", autouse=True)
def _host_json_serializer_only():
    """Production writes through the host's engine; a test engine must not store what production cannot."""
    event.listen(Engine, "before_cursor_execute", _refuse_foreign_json_serializer)
    yield
    event.remove(Engine, "before_cursor_execute", _refuse_foreign_json_serializer)


@pytest.fixture(autouse=True)
def _empty_batch_cache():
    """The catalog cache lives for the process; each test starts and ends without another test's batches."""
    from ggwork_pick.repository import CATALOG_CACHE

    CATALOG_CACHE.clear()
    yield
    CATALOG_CACHE.clear()


@pytest.fixture(scope="session")
def pg_cluster():
    url = os.environ.get(pg.URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{pg.URL_ENV} is not set")
    return pg.PgCluster(url)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_template(pg_cluster, tmp_path_factory):
    """Migrated to head once per session; every PostgreSQL test starts from a copy."""
    name = pg.unique_name("pick_tpl")
    pg_cluster.create_database(name)
    try:
        await pg.migrate(pg_cluster.async_url(name), tmp_path_factory.mktemp("pick-tpl"))
        yield name
    finally:
        pg_cluster.drop_database(name)


@pytest.fixture
def pg_reader_role(pg_cluster, monkeypatch):
    name = pg.unique_name(pg.DEFAULT_READER_ROLE)
    pg_cluster.create_role(name)
    monkeypatch.setenv(pg.READER_ROLE_ENV, name)
    yield name
    pg_cluster.drop_role(name)


@pytest.fixture
def pg_db_url(pg_cluster, pg_template, pg_reader_role):
    # Set up after the role, so torn down before it: the database goes first, with any grants it gave the role.
    name = pg.unique_name("t")
    pg_cluster.create_database(name, template=pg_template)
    yield pg_cluster.async_url(name)
    pg_cluster.drop_database(name)


@pytest.fixture(params=["sqlite", "postgres"])
def pick_db_url(request, tmp_path):
    """The same test on both dialects; the PostgreSQL half skips when PICK_TEST_PG_URL is unset."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    return request.getfixturevalue("pg_db_url")


@pytest_asyncio.fixture
async def app_client(pick_db_url, tmp_path):
    """The extension's router on either dialect; the test-owner header names the signed-in user."""
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
    from fastapi import FastAPI

    from ggwork_pick.routes import build_router
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    service.run_evidence_reader = SimpleNamespace(get_run_status=AsyncMock(return_value=SimpleNamespace(status="success")))
    app = FastAPI()
    # Test-only identity resolver, never installed by the business extension.
    setattr(
        app.state,
        EXTENSION_PRINCIPAL_RESOLVER_KEY,
        lambda request: (
            ExtensionPrincipal(request.headers["test-owner"], is_internal="test-internal" in request.headers) if "test-owner" in request.headers else None
        ),
    )
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, service
    await engine.dispose()
