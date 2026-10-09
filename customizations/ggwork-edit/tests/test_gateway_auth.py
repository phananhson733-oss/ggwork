"""Real host middleware plus real editing credential storage: no fake principal."""

import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def gateway(db_url, monkeypatch):
    import app.gateway.app as app_module
    from app.gateway.auth import AuthConfig, LocalAuthProvider, User, create_access_token, set_auth_config
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.extensions import reset_loaded_extensions, reset_runtime_diagnostics
    from deerflow.extensions.gateway import include_contributed_routers
    from deerflow.extensions.registry import ExtensionRegistry
    from deerflow.persistence.base import Base
    from deerflow.persistence.user.model import UserRow

    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.service import EditingService

    config = AppConfig(sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr(app_module, "get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.authz._get_route_authorization_config", lambda: config.authorization)
    monkeypatch.delenv("DEER_FLOW_AUTH_DISABLED", raising=False)
    set_auth_config(AuthConfig(jwt_secret="editing-test-secret-0123456789abcdef", token_expiry_days=7))
    engine = create_async_engine(db_url, json_serializer=lambda value: json.dumps(value, ensure_ascii=False))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users = SQLiteUserRepository(factory)
    owner = User(email="editing-owner@example.com", system_role="admin")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[UserRow.__table__]))
    await users.create_user(owner)
    provider = LocalAuthProvider(users)
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    reset_loaded_extensions()
    reset_runtime_diagnostics()
    application = app_module.create_app()
    service = EditingService(hook_available=True)
    await service.initialize(factory)
    registry = ExtensionRegistry()
    with registry.attributed_to("ggwork_edit:install"):
        registry.routers((build_router(service),))
        registry.bearer_routers((build_worker_router(service),), service)
    assert include_contributed_routers(application, registry.build()) == []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        client.cookies.set("access_token", create_access_token(str(owner.id)))
        yield client, service, str(owner.id)
    await service.stop()
    await engine.dispose()
    reset_loaded_extensions()
    reset_runtime_diagnostics()


@pytest.mark.asyncio
async def test_pair_real_token_is_narrow_revocable_and_never_session_fallback(gateway):
    from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

    client, service, owner = gateway
    assert (await client.post("/api/editing/devices", json={"name": "Mac"})).status_code == 403
    csrf = "synthetic-double-submit"
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    browser = {CSRF_HEADER_NAME: csrf}
    paired = await client.post("/api/editing/devices", headers=browser, json={"name": "Mac"})
    assert paired.status_code == 200, paired.text
    device = paired.json()["device"]["id"]
    token = paired.json()["token"]
    scoped = await service.authenticate(token)
    assert scoped.user_id == owner and scoped.subject_id == device
    path = f"/api/editing/worker/devices/{device}/heartbeat"
    payload = {"platform": "darwin-arm64", "ready": True, "grants": ["grant-1"], "worker_version": "test"}
    assert (await client.post(path, headers=browser, json=payload)).status_code == 401
    assert (await client.post(path, headers={"Authorization": "Bearer invalid"}, json=payload)).status_code == 401
    worker = {"Authorization": f"Bearer {token}"}
    accepted = await client.post(path, headers=worker, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert (await client.post(path.replace(device, "another-device"), headers=worker, json=payload)).status_code == 403
    assert (await client.post("/api/editing/devices", headers=worker, json={"name": "Unauthorized"})).status_code == 401
    assert (await client.get("/api/threads", headers=worker)).status_code == 401
    assert (await client.post(f"/api/editing/devices/{device}/revoke", headers=browser)).status_code == 200
    assert await service.authenticate(token) is None
    assert (await client.post(path, headers=worker, json=payload)).status_code == 401
    assert (await client.get("/api/editing/devices")).json()["items"][0]["revoked"] is True
