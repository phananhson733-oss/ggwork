"""Catalog trimming, preset remote endpoints, deployment-native status and live connection checks."""

import base64
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP

from app.gateway import capabilities
from app.gateway.deps import get_config
from app.gateway.routers import capabilities as router
from app.gateway.routers import mcp
from deerflow.capabilities.catalog import load_catalog
from deerflow.config.extensions_config import ExtensionsConfig

HIDDEN = {"lark", "dingtalk", "wecom", "tencent-docs", "notion", "browser"}
GOOD_TOKEN = "good-token-123456"


@pytest.fixture
def connect_client(tmp_path, monkeypatch):
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps({"mcpServers": {}, "skills": {}}))
    monkeypatch.setattr(ExtensionsConfig, "resolve_config_path", lambda *args: path)
    monkeypatch.setattr(mcp, "reload_extensions_config", lambda: None)
    monkeypatch.setattr(mcp, "reset_mcp_tools_cache", lambda: None)
    app = FastAPI()
    tools = [SimpleNamespace(name="pick_query_candidates"), SimpleNamespace(name="web_search")]
    app.dependency_overrides[get_config] = lambda: SimpleNamespace(tools=tools)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.user = SimpleNamespace(system_role=request.headers.get("test-role", "admin"))
        return await call_next(request)

    app.include_router(router.router)
    app.include_router(mcp.router)
    with TestClient(app) as client:
        yield client, path


@pytest.fixture(scope="module")
def token_mcp_server():
    """A real Streamable HTTP MCP server that requires ``Authorization: Bearer GOOD_TOKEN``."""
    server = FastMCP("check-fixture", stateless_http=True, json_response=True)

    @server.tool()
    def lookup(query: str) -> str:
        """Echo the query."""
        return query

    inner = server.streamable_http_app()

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != f"Bearer {GOOD_TOKEN}".encode():
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await inner(scope, receive, send)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(guarded, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert instance.started
    yield f"http://127.0.0.1:{port}/mcp"
    instance.should_exit = True
    thread.join(timeout=10)


def _save(path, servers):
    path.write_text(json.dumps({"mcpServers": servers, "skills": {}}))


def test_hidden_entries_leave_discovery_and_cannot_be_installed(connect_client):
    client, path = connect_client
    listed = {item["id"] for item in client.get("/api/capabilities/catalog").json()}
    assert not listed & HIDDEN
    assert {"feishu-bot", "feishu-docs", "google-docs", "github", "atlassian", "web-search"} <= listed
    assert {item.id for item in load_catalog(include_hidden=True)} >= HIDDEN
    before = path.read_bytes()
    response = client.post("/api/capabilities/installations", json={"plugin_id": "dingtalk", "name": "d", "configuration": {"access_token": "robot-token", "sign_secret": "SEC-secret"}})
    assert response.status_code == 404
    assert path.read_bytes() == before


def test_github_install_uses_the_official_endpoint_and_only_takes_a_token(connect_client):
    client, path = connect_client
    payload = {"plugin_id": "github", "name": "github", "configuration": {"token": " github_pat_SECRETVALUE123 "}}
    assert client.post("/api/capabilities/installations", json=payload, headers={"test-role": "user"}).status_code == 403
    response = client.post("/api/capabilities/installations", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["plugin_id"] == "github"
    assert "SECRETVALUE" not in response.text
    saved = json.loads(path.read_text())["mcpServers"]["github"]
    assert saved["type"] == "http" and saved["url"] == "https://api.githubcopilot.com/mcp/readonly"
    assert saved["headers"] == {"Authorization": "Bearer github_pat_SECRETVALUE123"}
    assert saved["capability"]["plugin_id"] == "github"
    assert "SECRETVALUE" not in client.get("/api/capabilities/installations/remote").text


@pytest.mark.parametrize(
    "configuration",
    [
        {"token": "github_pat_SECRETVALUE123", "url": "https://attacker.example/mcp"},
        {"authorization": "Bearer github_pat_SECRETVALUE123"},
        {"token": "short"},
        {"token": "has space SECRETVALUE"},
        {"token": "line\nbreak-SECRETVALUE"},
    ],
)
def test_remote_install_rejects_anything_but_valid_credentials(connect_client, configuration):
    client, path = connect_client
    before = path.read_bytes()
    response = client.post("/api/capabilities/installations", json={"plugin_id": "github", "name": "github", "configuration": configuration})
    assert response.status_code == 422
    assert "SECRETVALUE" not in response.text
    assert path.read_bytes() == before


def test_atlassian_uses_basic_auth_with_email_and_scoped_token(connect_client):
    client, path = connect_client
    payload = {"plugin_id": "atlassian", "name": "jira", "configuration": {"email": "dev@example.com", "api_token": "ATATT3x-token_value="}}
    assert client.post("/api/capabilities/installations", json=payload).status_code == 200
    saved = json.loads(path.read_text())["mcpServers"]["jira"]
    assert saved["url"] == "https://mcp.atlassian.com/v1/mcp"
    expected = base64.b64encode(b"dev@example.com:ATATT3x-token_value=").decode()
    assert saved["headers"] == {"Authorization": f"Basic {expected}"}
    bad = {**payload, "name": "jira2", "configuration": {"email": "not-an-email", "api_token": "ATATT3x-token_value="}}
    assert client.post("/api/capabilities/installations", json=bad).status_code == 422


def test_native_entries_report_configured_deployment_tools_only(connect_client):
    client, _ = connect_client
    items = client.get("/api/capabilities/installations/native", headers={"test-role": "user"}).json()["items"]
    assert [(item["plugin_id"], item["adapter"], item["enabled"]) for item in items] == [("web-search", "native", True)]
    response = client.post("/api/capabilities/installations", json={"plugin_id": "web-fetch", "name": "x", "configuration": {}})
    assert response.status_code == 422


def test_check_requires_admin_and_a_known_server(connect_client):
    client, _ = connect_client
    assert client.post("/api/capabilities/connections/check", json={"name": "missing"}, headers={"test-role": "user"}).status_code == 403
    assert client.post("/api/capabilities/connections/check", json={"name": "missing"}).status_code == 404
    assert client.post("/api/capabilities/connections/check", json={"name": ""}).status_code == 422


def test_check_discovers_tools_with_the_saved_token(connect_client, token_mcp_server, monkeypatch):
    client, path = connect_client
    resets = []
    monkeypatch.setattr("deerflow.mcp.cache.reset_mcp_tools_cache", lambda: resets.append(1))
    monkeypatch.setattr("deerflow.mcp.cache.cached_mcp_server_names", lambda: set())
    _save(path, {"team": {"type": "http", "url": token_mcp_server, "headers": {"Authorization": f"Bearer {GOOD_TOKEN}"}, "enabled": True}})
    response = client.post("/api/capabilities/connections/check", json={"name": "team"})
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "team", "ok": True, "code": "ok", "tool_count": 1, "tools": ["lookup"], "detail": None}
    # The agent cache had cached this server as tool-less; a working check refreshes it.
    assert resets == [1]


def test_check_reports_rejected_credentials_without_echoing_them(connect_client, token_mcp_server, monkeypatch):
    client, path = connect_client
    resets = []
    monkeypatch.setattr("deerflow.mcp.cache.reset_mcp_tools_cache", lambda: resets.append(1))
    _save(path, {"team": {"type": "http", "url": token_mcp_server, "headers": {"Authorization": "Bearer wrong-SECRETVALUE"}, "enabled": True}})
    response = client.post("/api/capabilities/connections/check", json={"name": "team"})
    assert response.status_code == 200
    body = response.json()
    assert (body["ok"], body["code"], body["detail"]) == (False, "auth_failed", "HTTP 401")
    assert "SECRETVALUE" not in response.text and "127.0.0.1" not in response.text
    assert resets == []


def test_check_reports_an_unreachable_server(connect_client):
    client, path = connect_client
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _save(path, {"gone": {"type": "http", "url": f"http://127.0.0.1:{port}/mcp", "enabled": False}})
    body = client.post("/api/capabilities/connections/check", json={"name": "gone"}).json()
    assert (body["ok"], body["code"]) == (False, "unreachable")


def test_check_runs_bundled_verification_for_business_providers(connect_client, monkeypatch):
    from deerflow.capabilities import business

    client, path = connect_client
    calls = []

    async def verify(provider, credentials, http=None):
        calls.append((provider, credentials))
        raise ValueError("hubspot: HTTP 401; check credentials, permissions and provider limits")

    monkeypatch.setattr(business, "verify_credentials", verify)
    _save(path, {"crm": {**business.connection_config("hubspot", {"access_token": "private-token"}), "enabled": True}})
    body = client.post("/api/capabilities/connections/check", json={"name": "crm"}).json()
    assert calls == [("hubspot", {"access_token": "private-token"})]
    assert body["ok"] is False and body["code"] == "provider_error"
    assert body["tool_count"] == 2 and "get_companies" in body["tools"]
    assert "private-token" not in json.dumps(body)


def test_failure_classification_walks_exception_groups():
    import httpx

    from deerflow.capabilities.check import classify_failure

    request = httpx.Request("POST", "https://secret.example/mcp?key=SECRETVALUE")
    status = httpx.HTTPStatusError("boom SECRETVALUE", request=request, response=httpx.Response(403, request=request))
    assert classify_failure(ExceptionGroup("outer", [RuntimeError("x"), status])).code == "auth_failed"
    assert classify_failure(ExceptionGroup("outer", [httpx.ConnectError("SECRETVALUE", request=request)])).code == "unreachable"
    assert classify_failure(TimeoutError()).code == "timeout"
    unknown = classify_failure(RuntimeError("SECRETVALUE"))
    assert (unknown.code, unknown.detail) == ("error", "RuntimeError")


def test_capabilities_module_registers_every_catalog_adapter():
    adapters = {item.adapter for item in load_catalog()} - {"guide"}
    for name in adapters:
        assert capabilities.registry.get(name) is not None


def test_check_fails_when_a_preset_server_only_lists_its_public_tools(connect_client, token_mcp_server, monkeypatch):
    from deerflow.capabilities import remote

    client, path = connect_client
    preset = remote.RemotePreset(token_mcp_server, ("token",), lambda c: {}, expected_tools=("jira",), missing_tools_hint="No Jira tools")
    monkeypatch.setitem(remote.PRESETS, "fixture", preset)
    _save(path, {"jira": {"type": "http", "url": token_mcp_server, "headers": {"Authorization": f"Bearer {GOOD_TOKEN}"}, "enabled": True}})
    body = client.post("/api/capabilities/connections/check", json={"name": "jira"}).json()
    assert (body["ok"], body["code"], body["detail"], body["tools"]) == (False, "auth_failed", "No Jira tools", ["lookup"])
