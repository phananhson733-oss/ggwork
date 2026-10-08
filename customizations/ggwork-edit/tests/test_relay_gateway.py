"""Actual Gateway cookie/CSRF and device bearer admission for relay HTTP."""

import asyncio

import httpx
import pytest


@pytest.mark.asyncio
async def test_real_gateway_relay_cookie_csrf_bearer_and_revocation(api, monkeypatch):
    import app.gateway.app as app_module
    from app.gateway.auth import AuthConfig, LocalAuthProvider, User, create_access_token, set_auth_config
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.extensions.gateway import include_contributed_routers
    from deerflow.extensions.registry import ExtensionRegistry
    from deerflow.persistence.base import Base
    from deerflow.persistence.user.model import UserRow

    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.routes import build_router, build_worker_router
    from ggwork_edit.worker.relay_client import RelayClient

    _, service, _ = api
    config = AppConfig(sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr(app_module, "get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.authz._get_route_authorization_config", lambda: config.authorization)
    monkeypatch.delenv("DEER_FLOW_AUTH_DISABLED", raising=False)
    set_auth_config(AuthConfig(jwt_secret="relay-test-secret-0123456789abcdef", token_expiry_days=7))
    factory = service.session_factory
    users = SQLiteUserRepository(factory)
    user = User(email="relay-test@example.com", system_role="admin")
    async with factory.kw["bind"].begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[UserRow.__table__]))
    await users.create_user(user)
    provider = LocalAuthProvider(users)
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    app = app_module.create_app()
    relay = Relay(service, timeout=1)
    registry = ExtensionRegistry()
    with registry.attributed_to("ggwork_edit:install"):
        registry.routers((build_router(service), build_relay_router(relay)))
        registry.bearer_routers((build_worker_router(service), build_relay_worker_router(relay)), service)
    assert include_contributed_routers(app, registry.build()) == []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
        browser.cookies.set("access_token", create_access_token(str(user.id)))
        browser.cookies.set(CSRF_COOKIE_NAME, "relay-csrf")
        csrf = {CSRF_HEADER_NAME: "relay-csrf"}
        paired = (await browser.post("/api/editing/devices", headers=csrf, json={"name": "Mac"})).json()
        device = paired["device"]["id"]
        bearer = {"Authorization": f"Bearer {paired['token']}"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=bearer) as native_http:
            heartbeat = await native_http.post(
                f"/api/editing/worker/devices/{device}/heartbeat",
                json={
                    "platform": "darwin-arm64",
                    "ready": True,
                    "grants": ["grant-1"],
                    "worker_version": "test",
                },
            )
            assert heartbeat.status_code == 200
            task = (
                await browser.post(
                    "/api/editing/tasks",
                    headers=csrf,
                    json={
                        "request_id": "relay",
                        "title": "Relay",
                        "requirements": {"instructions": "Hook", "output_count": 1, "duration_seconds": 30, "aspect_ratio": "9:16"},
                        "device_id": device,
                        "source_manifest": {
                            "version": 1,
                            "grant_id": "grant-1",
                            "files": [
                                {"media_id": "m1", "name": "1.mp4", "episode": 1, "relative_path": "1.mp4", "size_bytes": 4},
                            ],
                        },
                    },
                )
            ).json()
            start = f"/api/editing/tasks/{task['id']}/uploads"
            assert (await browser.post(start, json={"media_id": "m1"})).status_code == 403
            assert (await native_http.post(start, json={"media_id": "m1"})).status_code == 401
            transfer = (await browser.post(start, headers=csrf, json={"media_id": "m1"})).json()["transfer_id"]
            path = f"/api/editing/uploads/{transfer}?offset=0"
            assert (await browser.put(path, content=b"test")).status_code == 403
            received = []
            native = RelayClient(native_http, device, receive=lambda command, data: received.append(data), read=lambda command: b"")
            stop = asyncio.Event()
            runner = asyncio.create_task(native.run(stop, poll_seconds=0.001))
            try:
                result = await browser.put(path, headers=csrf, content=b"test")
                assert result.status_code == 200 and result.json()["state"] == "received"
                assert received == [b"test"]
            finally:
                stop.set()
                await runner
            await browser.post(f"/api/editing/devices/{device}/revoke", headers=csrf)
            assert (await native_http.get(f"/api/editing/worker/devices/{device}/relay/commands")).status_code == 401
