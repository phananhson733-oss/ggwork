import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / "customizations/ggwork-edit", ROOT / "backend/packages/extension-api"):
    sys.path.insert(0, str(path))


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'editing.db'}"
        return
    url = os.environ.get("EDIT_TEST_PG_URL")
    if not url:
        pytest.skip("EDIT_TEST_PG_URL must name a disposable PostgreSQL cluster")
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    name = "ggwe_test_" + uuid4().hex[:16]
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_url(url).set(drivername="postgresql+asyncpg", database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest_asyncio.fixture
async def api(db_url):
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal

    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    engine = create_async_engine(db_url, json_serializer=lambda value: json.dumps(value, ensure_ascii=False))
    service = EditingService(hook_available=True)
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    app = FastAPI()
    setattr(
        app.state,
        EXTENSION_PRINCIPAL_RESOLVER_KEY,
        lambda request: ExtensionPrincipal(request.headers["test-owner"]) if "test-owner" in request.headers else None,
    )
    app.include_router(build_router(service))
    app.include_router(build_worker_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, service, app
    await service.stop()
    await engine.dispose()
