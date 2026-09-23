import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parent))


@pytest_asyncio.fixture
async def app_client(tmp_path):
    from ggwork_pick.routes import build_router
    from ggwork_pick.service import PickService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
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


@pytest.mark.asyncio
async def test_routes_fail_closed_and_import_metadata_has_no_local_path(app_client):
    client, _ = app_client
    assert (await client.get("/api/pick/imports")).status_code == 401
    data = [{"source": "synthetic", "source_id": "1", "language": "en", "title": "Synthetic"}]
    response = await client.post(
        "/api/pick/imports", headers={"test-owner": "alice"}, data={"kind": "catalog"}, files=[("files", ("sample.json", json.dumps(data), "application/json"))]
    )
    assert response.status_code == 201
    assert "raw_blob_path" not in response.json()
    assert "owner_id" not in response.json()
    assert len((await client.get("/api/pick/imports", headers={"test-owner": "alice"})).json()["batches"]) == 1
    assert (await client.get("/api/pick/imports", headers={"test-owner": "bob"})).json()["batches"] == []


@pytest.mark.asyncio
async def test_save_api_rejects_authority_fields_and_returns_actual_receipt(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"=1+1"}]', "json")
    result = await SelectionService(repo).query({}, thread_id="t1", run_id="r1", call_id="c1")
    payload = {"request_id": "save-1", "result_id": result["id"], "item_ids": [result["items"][0]["item_id"]]}
    assert (await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json={**payload, "owner_id": "bob"})).status_code == 422
    assert (await client.post("/api/pick/selections", headers={"test-owner": "bob"}, json=payload)).status_code == 404
    response = await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json=payload)
    assert response.status_code == 200
    assert (await client.get("/api/pick/commands/save-1", headers={"test-owner": "alice"})).json() == response.json()
    assert (await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json={**payload, "note": "different"})).status_code == 409
    csv = await client.get("/api/pick/selections/export.csv", headers={"test-owner": "alice"})
    assert "'=1+1" in csv.text


@pytest.mark.asyncio
async def test_multi_document_knowledge_is_one_complete_batch(app_client):
    client, service = app_client
    response = await client.post(
        "/api/pick/imports",
        headers={"test-owner": "alice"},
        data={"kind": "knowledge"},
        files=[
            ("files", ("a.md", "# A\nfirst", "text/markdown")),
            ("files", ("b.md", "# B\nsecond", "text/markdown")),
        ],
    )
    assert response.status_code == 201
    assert response.json()["validation_json"]["rows"] == 2
    from ggwork_pick.repository import PickRepository

    repo = PickRepository(service.session_factory, "alice")
    assert len(await repo.knowledge_documents(response.json()["id"])) == 2


@pytest.mark.asyncio
async def test_incomplete_runs_cannot_save_but_committed_receipts_survive_missing_run(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
    result = await SelectionService(repo).query({}, thread_id="t", run_id="r", call_id="c")
    payload = {"request_id": "save", "result_id": result["id"], "item_ids": [result["items"][0]["item_id"]], "note": "下周准备剪辑"}
    reader = service.run_evidence_reader.get_run_status
    for status in ("pending", "running", "error", "timeout", "interrupted", "unknown"):
        reader.return_value = SimpleNamespace(status=status) if status != "unknown" else None
        response = await client.get(f"/api/pick/results/{result['id']}", headers={"test-owner": "alice"})
        assert response.json()["run_status"] == status
        assert (await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json=payload)).status_code == 409
    reader.reset_mock()
    assert (await client.get(f"/api/pick/results/{result['id']}", headers={"test-owner": "bob"})).status_code == 404
    reader.assert_not_called()
    reader.return_value = SimpleNamespace(status="success")
    saved = await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json=payload)
    assert saved.status_code == 200
    reader.return_value = None
    assert (await client.post("/api/pick/selections", headers={"test-owner": "alice"}, json=payload)).json() == saved.json()
    assert (await repo.selections())[0]["note"] == "下周准备剪辑"


def _configure_sync(service, rows):
    from test_realshort_sync import TOKEN, feed_transport

    from ggwork_pick.service import SyncSettings

    service.sync_settings = SyncSettings(feed_url="https://realshort.test", feed_token=TOKEN, trigger_token="trigger-secret")
    service.sync_transport = feed_transport(rows)


@pytest.mark.asyncio
async def test_cron_sync_requires_internal_caller_and_sync_token(app_client):
    from test_realshort_sync import feed_row

    client, service = app_client
    internal = {"test-owner": "default", "test-internal": "1"}
    assert (await client.post("/api/pick/cron/sync", headers=internal)).status_code == 404
    _configure_sync(service, [feed_row(1), feed_row(2)])
    good = {**internal, "x-pick-sync-token": "trigger-secret"}
    assert (await client.post("/api/pick/cron/sync", headers={**internal, "x-pick-sync-token": "wrong"})).status_code == 401
    assert (await client.post("/api/pick/cron/sync", headers=internal)).status_code == 401
    # A logged-in user holding the token is still not the internal cron caller.
    assert (await client.post("/api/pick/cron/sync", headers={"test-owner": "alice", "x-pick-sync-token": "trigger-secret"})).status_code == 401
    started = await client.post("/api/pick/cron/sync", headers=good)
    assert started.status_code == 202
    await service.wait_background()
    status = (await client.get("/api/pick/sync", headers={"test-owner": "bob"})).json()
    assert status["configured"] is True
    assert status["runs"][0]["status"] == "success" and status["runs"][0]["trigger"] == "cron"
    assert status["current"]["rows"] == 2 and status["current"]["shared"] is True
    assert "trigger-secret" not in json.dumps(status) and "feed-token" not in json.dumps(status)
    batches = (await client.get("/api/pick/imports", headers={"test-owner": "bob"})).json()["batches"]
    assert {b["shared"] for b in batches} == {True}
    assert (await client.get("/api/pick/sync")).status_code == 401


@pytest.mark.asyncio
async def test_manual_sync_is_rate_limited_and_results_expose_data_time(app_client):
    from test_realshort_sync import feed_row

    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    assert (await client.post("/api/pick/sync", headers={"test-owner": "alice"})).status_code == 503
    _configure_sync(service, [feed_row(1)])
    assert (await client.post("/api/pick/sync", headers={"test-owner": "alice"})).status_code == 202
    await service.wait_background()
    again = await client.post("/api/pick/sync", headers={"test-owner": "alice"})
    assert again.status_code == 429
    result = await SelectionService(PickRepository(service.session_factory, "alice")).query({}, thread_id="t", run_id="r", call_id="c")
    view = (await client.get(f"/api/pick/results/{result['id']}", headers={"test-owner": "alice"})).json()
    assert view["data_as_of"]["source_as_of"] == "2026-09-23T03:00:00.000Z"
    assert view["data_as_of"]["freshness"]["catalogImportedAt"] == "2026-09-22T03:19:21.327Z"
