"""Actual database/HTTP interleavings for preview and execution receipt recovery."""

import asyncio

import pytest
from test_planning import editable
from test_planning_export import ready_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["preview", "exports"])
async def test_edit_while_current_source_is_read_rejects_confirmation(app_client, operation):
    from sqlalchemy import event
    from sqlalchemy.util import await_only

    from ggwork_pick.repository import CATALOG_CACHE

    client, service = app_client
    h = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    body = {"request_id": "racing-read", "expected_version": 1}
    if operation == "exports":
        preview = (await client.post(path + "/preview", headers=h, json={**body, "request_id": "before-race"})).json()
        body["preview_id"] = preview["preview_id"]
    CATALOG_CACHE.clear()
    entered, release = asyncio.Event(), asyncio.Event()
    engine = service.session_factory.kw["bind"].sync_engine
    armed = True

    def pause(conn, cursor, statement, parameters, context, executemany):
        nonlocal armed
        if armed and statement.startswith("SELECT") and "FROM ggwp_drama_versions" in statement:
            armed = False
            entered.set()
            await_only(release.wait())

    event.listen(engine, "after_cursor_execute", pause)
    pending = asyncio.create_task(client.post(path + "/" + operation, headers=h, json=body))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        patch = {**editable(plan), "request_id": "concurrent-edit", "expected_version": 1, "title": "Edited during read"}
        changed = await asyncio.wait_for(client.patch(path, headers=h, json=patch), 3)
        assert changed.status_code == 200, changed.text
        release.set()
        refused = await pending
        assert refused.status_code == 409 and refused.json()["detail"]["current_version"] == 2
        assert (await client.get(path, headers=h)).json() == changed.json()
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        event.remove(engine, "after_cursor_execute", pause)


@pytest.mark.asyncio
async def test_lost_export_and_download_reply_recover_exact_bytes_after_service_restart(app_client):
    import httpx
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.planning_export import PlanningExportService
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    client, service = app_client
    h = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    preview = (await client.post(path + "/preview", headers=h, json={"request_id": "preview", "expected_version": 1})).json()
    body = {"request_id": "lost-export", "expected_version": 1, "preview_id": preview["preview_id"]}

    class LoseReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await client._transport.handle_async_request(request)
            await response.aread()
            assert response.status_code == 200
            raise httpx.ReadError("synthetic response loss after actual server completion", request=request)

    async with httpx.AsyncClient(transport=LoseReply(), base_url="http://test") as lossy:
        with pytest.raises(httpx.ReadError):
            await lossy.post(path + "/exports", headers=h, json=body)
        receipt = (await client.post(path + "/exports", headers=h, json=body)).json()
        with pytest.raises(httpx.ReadError):
            await lossy.get("/api/pick/exports/" + receipt["id"], headers=h)
    original = (await client.get("/api/pick/exports/" + receipt["id"], headers=h)).content
    engine = host_engine(service.session_factory.kw["bind"].url)
    restarted = PickService(service.data_dir)
    try:
        await restarted.initialize(async_sessionmaker(engine))
        repo = PickRepository(restarted.session_factory, "alice")
        restored_receipt, restored_bytes = await PlanningExportService(repo, restarted.common_query(repo)).download(receipt["id"])
        assert restored_receipt == receipt and restored_bytes == original
    finally:
        await restarted.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_commit_fence_releases_waiting_real_source_publisher(app_client):
    import json

    from sqlalchemy import event
    from sqlalchemy.util import await_only

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    h = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    preview = (await client.post(path + "/preview", headers=h, json={"request_id": "before-fence", "expected_version": 1})).json()
    body = {"request_id": "cancel-at-fence", "expected_version": 1, "preview_id": preview["preview_id"]}
    engine = service.session_factory.kw["bind"].sync_engine
    held, release, publisher_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reads = 0

    def after(conn, cursor, statement, parameters, context, executemany):
        nonlocal reads
        if " AS catalog_owner" in statement and " AS catalog_id" in statement:
            reads += 1
            if reads == 2:
                held.set()
                await_only(release.wait())

    def before(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ggwp_import_batches") or (held.is_set() and statement == "BEGIN IMMEDIATE"):
            publisher_started.set()

    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "before_cursor_execute", before)
    pending = asyncio.create_task(client.post(path + "/exports", headers=h, json=body))
    publisher = None
    try:
        await asyncio.wait_for(held.wait(), 3)
        row = {
            "source": "synthetic",
            "source_id": "export-1",
            "title": "New current source",
            "language": "en",
            "theater": "Example",
            "availability": "active",
            "channel_rules": {"youtube": "allowed"},
        }
        publisher = asyncio.create_task(Importer(PickRepository.shared(service.session_factory), service.data_dir).catalog(json.dumps([row]).encode(), "json"))
        await asyncio.wait_for(publisher_started.wait(), 3)
        assert not publisher.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release.set()
        await asyncio.wait_for(publisher, 3)
    finally:
        release.set()
        for task in (pending, publisher):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (pending, publisher) if task is not None), return_exceptions=True)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "before_cursor_execute", before)
    # Cancellation stored no usable receipt; the old preview cannot authorize the new pin.
    refused = await client.post(path + "/exports", headers=h, json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "version_conflict"
    fresh = (await client.post(path + "/preview", headers=h, json={"request_id": "after-fence", "expected_version": 1})).json()
    resumed = await client.post(path + "/exports", headers=h, json={**body, "preview_id": fresh["preview_id"]})
    assert resumed.status_code == 200, resumed.text
    assert (await client.get(path, headers=h)).json() == plan


@pytest.mark.asyncio
async def test_asgi_disconnect_cancels_actual_source_read_and_allows_clean_retry(app_client):
    import json

    from sqlalchemy import event
    from sqlalchemy.util import await_only

    from ggwork_pick.repository import CATALOG_CACHE

    client, service = app_client
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}/preview"
    body = {"request_id": "disconnect-read", "expected_version": 1}
    incoming = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": json.dumps(body).encode(), "more_body": False})
    entered, release = asyncio.Event(), asyncio.Event()
    CATALOG_CACHE.clear()
    engine = service.session_factory.kw["bind"].sync_engine
    armed = True

    def pause(conn, cursor, statement, parameters, context, executemany):
        nonlocal armed
        if armed and statement.startswith("SELECT") and "FROM ggwp_drama_versions" in statement:
            armed = False
            entered.set()
            await_only(release.wait())

    async def send(message):
        pass

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "headers": [(b"content-type", b"application/json"), (b"test-owner", b"alice")],
    }
    event.listen(engine, "after_cursor_execute", pause)
    pending = asyncio.create_task(client._transport.app(scope, incoming.get, send))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await incoming.put({"type": "http.disconnect"})
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 3)
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        event.remove(engine, "after_cursor_execute", pause)
    retried = await client.post(path, headers={"test-owner": "alice"}, json=body)
    assert retried.status_code == 200 and retried.json()["exportable"] is True


@pytest.mark.asyncio
async def test_source_publication_during_validation_requires_new_confirmation(app_client):
    import json

    from sqlalchemy import event
    from sqlalchemy.util import await_only

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository

    client, service = app_client
    h = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}/preview"
    entered, release = asyncio.Event(), asyncio.Event()
    engine = service.session_factory.kw["bind"].sync_engine
    CATALOG_CACHE.clear()
    armed = True

    def pause(conn, cursor, statement, parameters, context, executemany):
        nonlocal armed
        if armed and statement.startswith("SELECT") and "FROM ggwp_drama_versions" in statement:
            armed = False
            entered.set()
            await_only(release.wait())

    event.listen(engine, "after_cursor_execute", pause)
    body = {"request_id": "publish-during-read", "expected_version": 1}
    pending = asyncio.create_task(client.post(path, headers=h, json=body))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        row = {
            "source": "synthetic",
            "source_id": "export-1",
            "title": "New permission",
            "language": "en",
            "theater": "Example",
            "availability": "active",
            "channel_rules": {"youtube": "denied"},
        }
        await Importer(PickRepository.shared(service.session_factory), service.data_dir).catalog(json.dumps([row]).encode(), "json")
        release.set()
        refused = await pending
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "version_conflict"
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        event.remove(engine, "after_cursor_execute", pause)
    fresh = (await client.post(path, headers=h, json=body)).json()
    assert fresh["exportable"] is False and any("不允许" in b for b in fresh["checks"][0]["blockers"])
