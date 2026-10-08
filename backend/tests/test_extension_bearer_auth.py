"""Bearer contribution contract at the production Gateway/router boundary."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from deerflow.extensions.gateway import include_contributed_routers
from deerflow.extensions.registry import ExtensionRegistry


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    import app.gateway.app as app_module
    from app.gateway.auth import AuthConfig, LocalAuthProvider, User, create_access_token, set_auth_config
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.extensions import reset_loaded_extensions, reset_runtime_diagnostics
    from deerflow.persistence.base import Base
    from deerflow.persistence.user.model import UserRow

    config = AppConfig(sandbox=SandboxConfig(use="test"))
    monkeypatch.setattr(app_module, "get_app_config", lambda: config)
    monkeypatch.setattr("app.gateway.authz._get_route_authorization_config", lambda: config.authorization)
    monkeypatch.delenv("DEER_FLOW_AUTH_DISABLED", raising=False)
    set_auth_config(AuthConfig(jwt_secret="extension-test-secret-0123456789abcdef", token_expiry_days=7))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}", poolclass=NullPool)
    repository = SQLiteUserRepository(async_sessionmaker(engine, expire_on_commit=False))
    owner = User(email="owner@example.com", system_role="admin")

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[UserRow.__table__]))
        await repository.create_user(owner)

    asyncio.run(setup())
    provider = LocalAuthProvider(repository)
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    reset_loaded_extensions()
    reset_runtime_diagnostics()
    app = app_module.create_app()
    yield SimpleNamespace(app=app, owner=owner, cookie=create_access_token(str(owner.id)), engine=engine)
    asyncio.run(engine.dispose())
    reset_loaded_extensions()
    reset_runtime_diagnostics()


class CredentialStore:
    """An extension-owned credential authority, independent of host sessions."""

    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.active = True

    async def authenticate(self, token):
        from deerflow_extension_api import ExtensionCredential

        if token == "worker-secret" and self.active:
            return ExtensionCredential(user_id=self.owner_id, subject_id="installation-1")
        return None


def worker_router(path="/api/extension-worker/{item_id}", methods=None):
    from deerflow_extension_api import resolve_principal

    router = APIRouter()

    async def worker(request: Request):
        from app.gateway.deps import is_admin_user
        from deerflow.runtime.user_context import get_effective_user_id

        return {
            "principal": asdict(resolve_principal(request)),
            "permissions": request.state.auth.permissions,
            "auth_source": request.state.auth_source,
            "context_user_id": get_effective_user_id(),
            "host_admin": await is_admin_user(request),
        }

    router.add_api_route(path, worker, methods=methods or ["GET", "POST"])
    return router


def mount_worker(gateway, router=None, authenticator=None):
    registry = ExtensionRegistry()
    with registry.attributed_to("example:install"):
        registry.bearer_routers((router or worker_router(),), authenticator or CredentialStore(str(gateway.owner.id)))
    assert include_contributed_routers(gateway.app, registry.build()) == []


def test_valid_worker_credential_has_owner_and_subject_but_no_host_authority(gateway):
    mount_worker(gateway)
    response = TestClient(gateway.app).post("/api/extension-worker/one", headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "principal": {"user_id": str(gateway.owner.id), "subject_id": "installation-1", "is_admin": False, "is_internal": False, "roles": []},
        "permissions": [],
        "auth_source": "extension",
        "context_user_id": str(gateway.owner.id),
        "host_admin": False,
    }


@pytest.mark.parametrize("subject", [None, "", 42])
def test_malformed_extension_identity_is_rejected_without_cookie_fallback(gateway, subject):
    from deerflow_extension_api import ExtensionCredential

    class MalformedIdentity:
        async def authenticate(self, token):
            return ExtensionCredential(user_id=str(gateway.owner.id), subject_id=subject)

    mount_worker(gateway, authenticator=MalformedIdentity())
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    response = client.get("/api/extension-worker/one", headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}


@pytest.mark.parametrize("authorization", [None, "Basic worker-secret", "Bearer", "Bearer wrong", "Bearer dfp_wrong"])
@pytest.mark.parametrize("fallback", ["session", "internal", "auth_disabled"])
def test_worker_route_never_uses_broader_fallback_credentials(gateway, monkeypatch, authorization, fallback):
    from app.gateway.internal_auth import create_internal_auth_headers

    mount_worker(gateway)
    client = TestClient(gateway.app)
    headers = {}
    if fallback == "session":
        client.cookies.set("access_token", gateway.cookie)
    elif fallback == "internal":
        headers.update(create_internal_auth_headers())
    else:
        monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
    if authorization is not None:
        headers["Authorization"] = authorization
    assert client.get("/api/extension-worker/one", headers=headers).status_code == 401


@pytest.mark.parametrize("path", ["/api/extension-worker/one", "/proxy/api/extension-worker/one"])
def test_root_path_dispatch_matches_the_actual_router(gateway, path):
    mount_worker(gateway)
    client = TestClient(gateway.app, root_path="/proxy")
    response = client.get(path, headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 200
    assert response.json()["principal"]["subject_id"] == "installation-1"


def test_method_and_first_actual_dispatch_bound_the_worker_credential(gateway):
    # The literal host path intersects but does not fully shadow the later
    # parameter route. The host must remain authoritative on the intersection.
    gateway.app.include_router(worker_router("/api/extension-worker/special", ["GET"]))
    gateway.app.include_router(worker_router("/api/extension-worker/{item_id}", ["DELETE"]))
    mount_worker(gateway)
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    bearer = {"Authorization": "Bearer worker-secret"}
    assert client.get("/api/extension-worker/special", headers=bearer).status_code == 401
    assert client.delete("/api/extension-worker/one", headers=bearer).status_code == 401
    assert client.get("/api/extension-worker/one", headers=bearer).status_code == 200
    assert client.post("/api/extension-worker/special", headers=bearer).status_code == 200
    # Existing host session admission still works on the earlier handler.
    response = client.get("/api/extension-worker/special")
    assert response.status_code == 200
    assert response.json()["principal"]["is_admin"] is True
    assert response.json()["principal"]["subject_id"] is None


def test_worker_credential_cannot_authorize_other_routes_or_extensions(gateway):
    gateway.app.include_router(worker_router("/api/browser-pair", ["GET", "POST"]))
    mount_worker(gateway)
    other = CredentialStore(str(gateway.owner.id))
    other.active = False
    mount_worker(gateway, worker_router("/api/other-worker"), other)
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    for path in ["/api/browser-pair", "/api/v1/auth/me", "/api/threads/example", "/api/other-worker", "/api/extension-worker/one/extra"]:
        response = client.get(path, headers={"Authorization": "Bearer worker-secret"})
        assert response.status_code == 401, (path, response.text)


def test_revocation_and_deleted_owner_take_effect_on_the_next_request(gateway):
    from sqlalchemy import delete

    from deerflow.persistence.user.model import UserRow

    store = CredentialStore(str(gateway.owner.id))
    mount_worker(gateway, authenticator=store)
    client = TestClient(gateway.app)
    headers = {"Authorization": "Bearer worker-secret"}
    assert client.get("/api/extension-worker/one", headers=headers).status_code == 200
    store.active = False
    assert client.get("/api/extension-worker/one", headers=headers).status_code == 401
    store.active = True

    async def delete_owner():
        async with gateway.engine.begin() as connection:
            await connection.execute(delete(UserRow).where(UserRow.id == str(gateway.owner.id)))

    asyncio.run(delete_owner())
    assert client.get("/api/extension-worker/one", headers=headers).status_code == 401


def test_unavailable_credential_store_fails_closed_without_exposing_the_token(gateway):
    class Unavailable:
        async def authenticate(self, token):
            raise RuntimeError(f"database failed for {token}")

    mount_worker(gateway, authenticator=Unavailable())
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    response = client.get("/api/extension-worker/one", headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}


def test_browser_pairing_remains_session_authenticated_and_csrf_protected(gateway):
    from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

    gateway.app.include_router(worker_router("/api/browser-pair", ["POST"]))
    mount_worker(gateway)
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    assert client.post("/api/browser-pair").status_code == 403
    csrf = "test-double-submit"
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    response = client.post("/api/browser-pair", headers={CSRF_HEADER_NAME: csrf})
    assert response.status_code == 200
    assert response.json()["principal"]["is_admin"] is True
    # A valid CSRF pair does not turn the session into a worker credential.
    assert client.post("/api/extension-worker/one", headers={CSRF_HEADER_NAME: csrf}).status_code == 401
    assert client.post("/api/browser-pair", headers={"Authorization": "Bearer worker-secret"}).status_code == 401


def test_extension_originated_cancellation_is_a_generic_credential_failure(gateway):
    class CancelledStore:
        async def authenticate(self, token):
            raise asyncio.CancelledError("worker-secret")

    mount_worker(gateway, authenticator=CancelledStore())
    response = TestClient(gateway.app).get("/api/extension-worker/one", headers={"Authorization": "Bearer worker-secret"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}


@pytest.mark.parametrize("rejection", ["reserved", "collision", "include_failure"])
def test_rejected_router_leaves_neither_partial_routes_nor_auth_bindings(gateway, rejection):
    from fastapi.routing import APIRoute

    rejected = worker_router("/api/rejected-partial")
    if rejection == "reserved":
        rejected.include_router(worker_router("/health/extension"))
    elif rejection == "collision":
        gateway.app.include_router(worker_router("/api/collision"))
        rejected.include_router(worker_router("/api/collision"))
    else:

        class CannotCopy(APIRoute):
            copies = 0

            def __init__(self, *args, **kwargs):
                type(self).copies += 1
                if self.copies > 1:
                    raise RuntimeError("copy failed")
                super().__init__(*args, **kwargs)

        rejected.add_api_route("/api/broken-copy", lambda: {}, route_class_override=CannotCopy)
    registry = ExtensionRegistry()
    with registry.attributed_to("bad:install"):
        registry.bearer_routers((rejected,), CredentialStore(str(gateway.owner.id)))
    with registry.attributed_to("good:install"):
        registry.routers((worker_router("/api/rejected-partial"),))
    diagnostics = include_contributed_routers(gateway.app, registry.build())
    assert len(diagnostics) == 1
    assert diagnostics[0].source == "bad:install"
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    # The later ordinary router must be usable, with ordinary session authority.
    response = client.get("/api/rejected-partial")
    assert response.status_code == 200
    assert response.json()["principal"]["is_admin"] is True
    assert client.get("/api/rejected-partial", headers={"Authorization": "Bearer worker-secret"}).status_code == 401


@pytest.mark.parametrize("undo", ["rollback", "discard"])
def test_registration_rollback_removes_credentials_and_routes_together(gateway, undo):
    registry = ExtensionRegistry()
    with registry.attributed_to("extension:install"):
        registry.bearer_routers((worker_router("/api/retained-worker"),), CredentialStore(str(gateway.owner.id)))
    mark = registry.mark()
    source = "extension:install" if undo == "rollback" else "failed:install"
    with registry.attributed_to(source):
        registry.bearer_routers((worker_router("/api/undone-worker"),), CredentialStore(str(gateway.owner.id)))
    if undo == "rollback":
        registry.rollback_to(mark)
    else:
        registry.discard(source)
    with registry.attributed_to("later:install"):
        registry.routers((worker_router("/api/undone-worker"),))
    assert include_contributed_routers(gateway.app, registry.build()) == []
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    bearer = {"Authorization": "Bearer worker-secret"}
    assert client.get("/api/undone-worker", headers=bearer).status_code == 401
    assert client.get("/api/undone-worker").json()["principal"]["is_admin"] is True
    assert client.get("/api/retained-worker", headers=bearer).status_code == 200


def test_valid_host_pat_cannot_substitute_for_worker_credential(gateway):
    from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
    from deerflow.persistence.base import Base
    from deerflow.persistence.personal_access_tokens import PersonalAccessTokenRepository
    from deerflow.persistence.personal_access_tokens.model import PersonalAccessTokenRow

    async def setup_pats():
        async with gateway.engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[PersonalAccessTokenRow.__table__]))

    asyncio.run(setup_pats())
    gateway.app.state.pat_repo = PersonalAccessTokenRepository(async_sessionmaker(gateway.engine, expire_on_commit=False))
    mount_worker(gateway)
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    client.cookies.set(CSRF_COOKIE_NAME, "csrf-test")
    created = client.post("/api/v1/auth/pats", json={"name": "test", "scopes": ["runs:read"]}, headers={CSRF_HEADER_NAME: "csrf-test"})
    assert created.status_code == 201, created.text
    bearer = {"Authorization": f"Bearer {created.json()['token']}"}
    assert client.get("/api/extension-worker/one", headers=bearer).status_code == 401


@pytest.mark.parametrize("authenticator", [None, object()])
def test_invalid_authenticator_cannot_create_a_session_accessible_worker_route(gateway, authenticator):
    registry = ExtensionRegistry()
    with registry.attributed_to("invalid:install"):
        registry.bearer_routers((worker_router(),), authenticator)
    diagnostics = include_contributed_routers(gateway.app, registry.build())
    assert len(diagnostics) == 1
    client = TestClient(gateway.app)
    client.cookies.set("access_token", gateway.cookie)
    assert client.get("/api/extension-worker/one").status_code == 404


@pytest.mark.parametrize("with_internal_header", [False, True])
def test_host_auth_decorators_cannot_restore_owner_permissions(gateway, with_internal_header):
    from app.gateway.authz import require_auth, require_permission

    router = APIRouter()

    @router.get("/api/extension-protected")
    @require_auth
    @require_permission("threads", "delete")
    async def protected(request: Request):
        return {"host_permission_used": True}

    mount_worker(gateway, router)
    from app.gateway.internal_auth import create_internal_auth_headers

    headers = create_internal_auth_headers() if with_internal_header else {}
    headers["Authorization"] = "Bearer worker-secret"
    response = TestClient(gateway.app).get("/api/extension-protected", headers=headers)
    assert response.status_code == 403


def test_valid_internal_header_does_not_elevate_a_worker_credential(gateway):
    from app.gateway.internal_auth import create_internal_auth_headers

    mount_worker(gateway)
    headers = create_internal_auth_headers(owner_user_id="another-owner")
    headers["Authorization"] = "Bearer worker-secret"
    response = TestClient(gateway.app).get("/api/extension-worker/one", headers=headers)
    assert response.status_code == 200
    assert response.json()["principal"] == {
        "user_id": str(gateway.owner.id),
        "subject_id": "installation-1",
        "is_admin": False,
        "is_internal": False,
        "roles": [],
    }
    assert response.json()["permissions"] == []


@pytest.mark.asyncio
async def test_host_cancellation_during_authentication_propagates(gateway):
    import httpx

    entered = asyncio.Event()

    class WaitingStore:
        async def authenticate(self, token):
            entered.set()
            await asyncio.Event().wait()

    mount_worker(gateway, authenticator=WaitingStore())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://testserver") as client:
        pending = asyncio.create_task(client.get("/api/extension-worker/one", headers={"Authorization": "Bearer worker-secret"}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
