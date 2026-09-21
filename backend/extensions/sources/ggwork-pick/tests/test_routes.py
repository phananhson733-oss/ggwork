import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
        lambda request: ExtensionPrincipal(request.headers["test-owner"]) if "test-owner" in request.headers else None,
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
