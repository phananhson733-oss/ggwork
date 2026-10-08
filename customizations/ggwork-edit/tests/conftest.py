import json
import sys
from pathlib import Path

import httpx
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / "customizations/ggwork-edit", ROOT / "backend/packages/extension-api"):
    sys.path.insert(0, str(path))


@pytest_asyncio.fixture
async def api(tmp_path):
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal

    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing.db'}", json_serializer=lambda value: json.dumps(value, ensure_ascii=False))
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
