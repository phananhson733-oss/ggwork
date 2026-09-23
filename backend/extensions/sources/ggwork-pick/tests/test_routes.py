import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))


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

    service.sync_settings = SyncSettings(feed_url="https://realshort.test", feed_token=TOKEN)
    service.sync_transport = feed_transport(rows)


@pytest.mark.asyncio
async def test_there_is_no_inbound_cron_endpoint_and_sync_status_is_shared(app_client):
    from test_realshort_sync import feed_row

    client, service = app_client
    _configure_sync(service, [feed_row(1), feed_row(2)])
    internal = {"test-owner": "default", "test-internal": "1"}
    assert (await client.post("/api/pick/cron/sync", headers=internal)).status_code == 404
    assert (await client.post("/api/pick/sync", headers={"test-owner": "bob"})).status_code == 202
    await service.wait_background()
    status = (await client.get("/api/pick/sync", headers={"test-owner": "alice"})).json()
    assert status["configured"] is True
    assert status["runs"][0]["status"] == "success" and status["runs"][0]["trigger"] == "manual"
    assert status["current"]["rows"] == 2 and status["current"]["shared"] is True
    assert "feed-token" not in json.dumps(status)
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


@pytest.mark.asyncio
async def test_a_failed_sync_can_be_retried_after_a_short_backoff(app_client):
    from sqlalchemy import text
    from test_realshort_sync import feed_row

    client, service = app_client
    _configure_sync(service, [feed_row(1)])
    from test_realshort_sync import feed_transport

    service.sync_transport = feed_transport([feed_row(1), feed_row(2)], fail_on=1, page=1)
    assert (await client.post("/api/pick/sync", headers={"test-owner": "alice"})).status_code == 202
    await service.wait_background()
    blocked = await client.post("/api/pick/sync", headers={"test-owner": "alice"})
    assert blocked.status_code == 429 and "1分钟" in blocked.json()["detail"]
    async with service.session_factory() as session, session.begin():
        await session.execute(text("update ggwp_sync_runs set started_at = '2026-01-01T00:00:00+00:00'"))
    assert (await client.post("/api/pick/sync", headers={"test-owner": "alice"})).status_code == 202
    await service.wait_background()


@pytest.mark.asyncio
async def test_answer_checks_are_listed_per_owner_and_thread(app_client):
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    alice = PickRepository(service.session_factory, "alice")
    await alice.record_answer_check(thread_id="t1", run_id="r1", message_id="m1", notes=["正文提到的《X》不在本轮查询结果中"])
    await alice.record_answer_check(thread_id="t1", run_id="r1", message_id="m1", notes=["retried call"])
    await alice.record_answer_check(thread_id="t2", run_id="r2", message_id="m2", notes=["other thread"])
    listed = (await client.get("/api/pick/answer-checks", params={"thread_id": "t1"}, headers={"test-owner": "alice"})).json()["checks"]
    assert [(c["message_id"], c["notes"]) for c in listed] == [("m1", ["正文提到的《X》不在本轮查询结果中"])]
    assert (await client.get("/api/pick/answer-checks", params={"thread_id": "t1"}, headers={"test-owner": "bob"})).json()["checks"] == []
    assert (await client.get("/api/pick/answer-checks", headers={"test-owner": "alice"})).status_code == 422
    assert (await client.get("/api/pick/answer-checks", params={"thread_id": "t1"})).status_code == 401
    # Answers without a message id are kept apart by run and never swallow each other.
    await alice.record_answer_check(thread_id="t3", run_id="r3", message_id=None, notes=["first"])
    await alice.record_answer_check(thread_id="t3", run_id="r4", message_id=None, notes=["second"])
    idless = (await client.get("/api/pick/answer-checks", params={"thread_id": "t3"}, headers={"test-owner": "alice"})).json()["checks"]
    assert [(c["message_id"], c["run_id"], c["notes"]) for c in idless] == [(None, "r3", ["first"]), (None, "r4", ["second"])]
