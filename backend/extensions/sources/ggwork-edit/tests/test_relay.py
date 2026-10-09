import asyncio

import pytest
from test_contract import HEARTBEAT, OWNER, REQUEST, worker_identity


@pytest.mark.asyncio
async def test_upload_progress_waits_for_device_receipt_and_stays_unverified(api):
    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router

    client, service, app = api
    relay = Relay(service, timeout=0.5)
    app.include_router(build_relay_router(relay))
    app.include_router(build_relay_worker_router(relay))
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {"version": 1, "grant_id": "grant-1", "files": [{"media_id": "m1", "name": "1.mp4", "relative_path": "1.mp4", "episode": 1, "size_bytes": 4}]}
    task = (await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    response = await client.post(f"/api/editing/tasks/{task['id']}/uploads", headers=OWNER, json={"media_id": "m1"})
    assert response.status_code == 200
    transfer = response.json()["transfer_id"]
    sending = asyncio.create_task(client.put(f"/api/editing/uploads/{transfer}?offset=0", headers=OWNER, content=b"abcd"))
    commands = f"/api/editing/worker/devices/{device}/relay/commands"
    for _ in range(50):
        items = (await client.get(commands, headers=worker)).json()["items"]
        if items:
            break
        await asyncio.sleep(0.005)
    assert not sending.done()
    command = items[0]
    assert command["final"] is True
    body = await client.get(f"{commands}/{command['id']}/body", headers=worker)
    assert body.content == b"abcd"
    assert (await client.post(f"{commands}/{command['id']}/response", headers=worker, content=b"")).status_code == 200
    result = await sending
    assert result.json()["offset"] == 4
    assert result.json()["state"] == "received"
    detail = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert detail["source_manifest"]["files"][0]["state"] == "selected"
    assert detail["status"] == "waiting"


@pytest.mark.asyncio
async def test_native_client_streams_single_ranges_and_reports_missing_file(api):
    import httpx
    from test_contract import RESULT, ready_task

    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.worker.relay_client import RelayClient, RelayFailure

    client, task, device, worker, attempt = await ready_task(api)
    _, service, app = api
    relay = Relay(service, timeout=0.5)
    app.include_router(build_relay_router(relay))
    app.include_router(build_relay_worker_router(relay))
    report = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "event_id": "output",
            "kind": "output",
            "output_id": "out-1",
            "result": {**RESULT, "size_bytes": 10},
        },
    )
    assert report.status_code == 200
    path = f"/api/editing/tasks/{task['id']}/outputs/out-1/content"
    missing = False

    def read(command):
        if missing:
            raise RelayFailure("file_missing")
        assert command["artifact_id"] == "artifact-1"
        assert command["sha256"] == "b" * 64
        return b"0123456789"[command["offset"] : command["offset"] + command["length"]]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=worker) as native_http:
        native = RelayClient(native_http, device, receive=lambda command, data: None, read=read)
        stop = asyncio.Event()
        runner = asyncio.create_task(native.run(stop, poll_seconds=0.001))
        try:
            response = await client.get(path, headers={**OWNER, "Range": "bytes=2-5"})
            assert response.status_code == 206
            assert response.content == b"2345"
            assert response.headers["content-range"] == "bytes 2-5/10"
            assert response.headers["content-length"] == "4"
            suffix = await client.get(path, headers={**OWNER, "Range": "bytes=-3"})
            assert suffix.content == b"789"
            full = await client.get(path + "?download=true", headers=OWNER)
            assert full.status_code == 200 and full.content == b"0123456789"
            assert "attachment" in full.headers["content-disposition"]
            assert (await client.get(path, headers={**OWNER, "Range": "bytes=10-"})).status_code == 416
            assert (await client.get(path, headers={**OWNER, "Range": "bytes=0-1,3-4"})).status_code == 416
            assert (await client.get(path, headers={"test-owner": "bob"})).status_code == 404
            assert (await client.get(path, headers=worker)).status_code == 403
            available = await client.get(path.replace("/content", "/access"), headers=OWNER)
            assert available.json()["access_status"] == "available"
            missing = True
            unavailable = await client.get(path.replace("/content", "/access"), headers=OWNER)
            assert unavailable.json()["access_status"] == "file_missing"
            failed = await client.get(path, headers=OWNER)
            assert failed.status_code == 409 and failed.json()["detail"] == "file_missing"
        finally:
            stop.set()
            await runner


async def receiving(api, *, timeout=0.1, per_device=4):
    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router

    client, service, app = api
    relay = Relay(service, timeout=timeout, per_device=per_device)
    app.include_router(build_relay_router(relay))
    app.include_router(build_relay_worker_router(relay))
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {
        "version": 1,
        "grant_id": "grant-1",
        "files": [{"media_id": f"m{i}", "name": f"{i}.mp4", "relative_path": f"{i}.mp4", "episode": i, "size_bytes": 2 * 1024 * 1024} for i in (1, 2)],
    }
    task = (await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    start = f"/api/editing/tasks/{task['id']}/uploads"
    transfer = (await client.post(start, headers=OWNER, json={"media_id": "m1"})).json()["transfer_id"]
    return client, device, worker, task, start, f"/api/editing/uploads/{transfer}"


@pytest.mark.asyncio
async def test_upload_limits_timeout_and_owner_device_scope(api):
    client, device, worker, task, start, path = await receiving(api, per_device=1)
    assert (await client.post(start, headers=OWNER, json={"media_id": "m2"})).status_code == 429
    assert (await client.put(path + "?offset=0", headers={"test-owner": "bob"}, content=b"a")).status_code == 404
    assert (await client.put(path + "?offset=0", headers=worker, content=b"a")).status_code == 403
    assert (await client.put(path + "?offset=1", headers=OWNER, content=b"a")).status_code == 409
    assert (await client.get(f"/api/editing/worker/devices/{device}/relay/commands", headers=OWNER)).status_code == 403
    assert (await client.get(f"/api/editing/worker/devices/{device}/relay/commands", headers={**worker, "test-device": "other"})).status_code == 403
    oversized = await client.put(path + "?offset=0", headers=OWNER, content=b"a" * (1024 * 1024 + 1))
    assert oversized.status_code == 413
    assert (await client.put(path + "?offset=0", headers=OWNER, content=b"a")).status_code == 404
    transfer = (await client.post(start, headers=OWNER, json={"media_id": "m1"})).json()["transfer_id"]
    timeout = await client.put(f"/api/editing/uploads/{transfer}?offset=0", headers=OWNER, content=b"a")
    assert timeout.status_code == 504
    assert timeout.json()["detail"] == "transfer_timeout_restart_required"
    assert (await client.get(f"/api/editing/worker/devices/{device}/relay/commands", headers=worker)).json()["items"] == []


@pytest.mark.asyncio
async def test_cancel_and_stop_reject_late_chunk_receipts(api):
    client, device, worker, task, start, path = await receiving(api, timeout=0.5)
    commands = f"/api/editing/worker/devices/{device}/relay/commands"
    sending = asyncio.create_task(client.put(path + "?offset=0", headers=OWNER, content=b"a"))
    for _ in range(50):
        items = (await client.get(commands, headers=worker)).json()["items"]
        if items:
            break
        await asyncio.sleep(0.005)
    assert (await client.delete(path, headers=OWNER)).status_code == 200
    assert (await sending).status_code == 409
    assert (await client.post(f"{commands}/{items[0]['id']}/response", headers=worker)).status_code == 404
    transfer = (await client.post(start, headers=OWNER, json={"media_id": "m1"})).json()["transfer_id"]
    sending = asyncio.create_task(client.put(f"/api/editing/uploads/{transfer}?offset=0", headers=OWNER, content=b"a"))
    for _ in range(50):
        items = (await client.get(commands, headers=worker)).json()["items"]
        if items:
            break
        await asyncio.sleep(0.005)
    await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={})
    await client.post(f"{commands}/{items[0]['id']}/response", headers=worker)
    assert (await sending).status_code == 409


@pytest.mark.asyncio
async def test_relay_refuses_multiple_gateway_workers_and_restart_resume(api, monkeypatch):
    import httpx
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY
    from fastapi import FastAPI

    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router

    client, device, worker, task, start, path = await receiving(api)
    monkeypatch.setenv("GATEWAY_WORKERS", "2")
    rejected = await client.put(path + "?offset=0", headers=OWNER, content=b"a")
    assert rejected.status_code == 503
    monkeypatch.setenv("GATEWAY_WORKERS", "1")
    _, service, app = api
    restarted = FastAPI()
    setattr(restarted.state, EXTENSION_PRINCIPAL_RESOLVER_KEY, getattr(app.state, EXTENSION_PRINCIPAL_RESOLVER_KEY))
    restarted.include_router(build_relay_router(Relay(service)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as after_restart:
        gone = await after_restart.put(path + "?offset=0", headers=OWNER, content=b"a")
        assert gone.status_code == 404 and gone.json()["detail"] == "transfer_restart_required"


@pytest.mark.asyncio
async def test_one_verified_upload_does_not_invalidate_other_selected_files(api):
    client, device, worker, task, start, first_path = await receiving(api, timeout=0.5)
    second = (await client.post(start, headers=OWNER, json={"media_id": "m2"})).json()["transfer_id"]
    manifest = task["source_manifest"]
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    verification = await client.post(f"/api/editing/worker/devices/{device}/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})
    assert verification.status_code == 200
    sending = asyncio.create_task(client.put(f"/api/editing/uploads/{second}?offset=0", headers=OWNER, content=b"a"))
    commands = f"/api/editing/worker/devices/{device}/relay/commands"
    for _ in range(50):
        items = (await client.get(commands, headers=worker)).json()["items"]
        if items or sending.done():
            break
        await asyncio.sleep(0.005)
    assert items, (await sending).text if sending.done() else "no command"
    await client.post(f"{commands}/{items[0]['id']}/response", headers=worker)
    assert (await sending).status_code == 200


@pytest.mark.asyncio
async def test_download_uses_bounded_successive_native_reads_and_checks_revocation(api):
    import httpx
    from test_contract import RESULT, ready_task

    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.worker.relay_client import RelayClient

    client, task, device, worker, attempt = await ready_task(api)
    _, service, app = api
    relay = Relay(service, timeout=1)
    app.include_router(build_relay_router(relay))
    app.include_router(build_relay_worker_router(relay))
    payload = b"x" * 1048576 + b"endclip"
    await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "event_id": "output",
            "kind": "output",
            "output_id": "out-1",
            "result": {**RESULT, "size_bytes": len(payload)},
        },
    )
    reads = []

    def read(command):
        reads.append((command["offset"], command["length"]))
        return payload[command["offset"] : command["offset"] + command["length"]]

    path = f"/api/editing/tasks/{task['id']}/outputs/out-1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=worker) as native_http:
        native = RelayClient(native_http, device, receive=lambda command, data: None, read=read)
        stop = asyncio.Event()
        runner = asyncio.create_task(native.run(stop, poll_seconds=0.001))
        try:
            response = await client.get(path + "/content", headers=OWNER)
            assert response.content == payload
            assert reads == [(0, 1048576), (1048576, 7)]
            await client.post(f"/api/editing/devices/{device}/revoke", headers=OWNER)
            access = await client.get(path + "/access", headers=OWNER)
            assert access.json()["access_status"] == "device_revoked"
            assert (await client.get(path + "/content", headers=OWNER)).status_code == 403
            assert (await native_http.get(f"/api/editing/worker/devices/{device}/relay/commands")).status_code == 403
        finally:
            stop.set()
            await runner


@pytest.mark.asyncio
async def test_cancelling_while_browser_sends_body_never_dispatches_to_device(api):
    client, device, worker, task, start, path = await receiving(api, timeout=0.1)
    body_started, continue_body = asyncio.Event(), asyncio.Event()

    async def body():
        yield b"a"
        body_started.set()
        await continue_body.wait()
        yield b"b"

    sending = asyncio.create_task(client.put(path + "?offset=0", headers=OWNER, content=body()))
    await body_started.wait()
    assert (await client.delete(path, headers=OWNER)).status_code == 200
    continue_body.set()
    response = await sending
    assert response.status_code == 404
    commands = await client.get(f"/api/editing/worker/devices/{device}/relay/commands", headers=worker)
    assert commands.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("send_failure", ["disconnect", "stall"])
async def test_failed_browser_send_releases_stream_slot(api, send_failure):
    import httpx
    from starlette.requests import ClientDisconnect
    from test_contract import RESULT, ready_task

    from ggwork_edit.relay import Relay
    from ggwork_edit.relay_routes import build_relay_router, build_relay_worker_router
    from ggwork_edit.worker.relay_client import RelayClient

    client, task, device, worker, attempt = await ready_task(api)
    _, service, app = api
    relay = Relay(service, timeout=0.5, per_device=1, idle_timeout=0.02)
    app.include_router(build_relay_router(relay))
    app.include_router(build_relay_worker_router(relay))
    await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "event_id": "output",
            "kind": "output",
            "output_id": "out-1",
            "result": {**RESULT, "size_bytes": 1},
        },
    )
    path = f"/api/editing/tasks/{task['id']}/outputs/out-1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=worker) as native_http:
        native = RelayClient(native_http, device, receive=lambda command, data: None, read=lambda command: b"x")
        stop = asyncio.Event()
        runner = asyncio.create_task(native.run(stop, poll_seconds=0.001))

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def disconnected_send(message):
            if send_failure == "disconnect":
                raise OSError("synthetic browser disconnect")
            if message["type"] == "http.response.body":
                await asyncio.Event().wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path + "/content",
            "raw_path": (path + "/content").encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"test-owner", b"alice")],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
        }
        try:
            request_task = asyncio.create_task(app(scope, receive, disconnected_send))
            done, _ = await asyncio.wait({request_task}, timeout=1)
            try:
                assert request_task in done, "stalled browser must release relay capacity within its idle deadline"
                with pytest.raises(ClientDisconnect):
                    await request_task
            finally:
                if not request_task.done():
                    request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
            access = await client.get(path + "/access", headers=OWNER)
            assert access.json()["access_status"] == "available"
        finally:
            stop.set()
            await runner


@pytest.mark.asyncio
async def test_cancel_releases_buffered_body_before_reusing_memory_capacity(api):
    client, device, worker, task, start, path = await receiving(api, timeout=0.5, per_device=1)
    body_started, release_body, body_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def body():
        try:
            yield b"a" * 1048576
            body_started.set()
            await release_body.wait()
        finally:
            body_closed.set()

    sending = asyncio.create_task(client.put(path + "?offset=0", headers=OWNER, content=body()))
    await body_started.wait()
    try:
        assert (await client.delete(path, headers=OWNER)).status_code == 200
        replacement = await client.post(start, headers=OWNER, json={"media_id": "m2"})
        assert replacement.status_code == 429 or body_closed.is_set()
    finally:
        release_body.set()
        await sending


@pytest.mark.asyncio
async def test_cancel_releases_native_response_buffer_before_reusing_capacity(api):
    client, device, worker, task, start, path = await receiving(api, timeout=1, per_device=1)
    sending = asyncio.create_task(client.put(path + "?offset=0", headers=OWNER, content=b"a"))
    commands = f"/api/editing/worker/devices/{device}/relay/commands"
    for _ in range(50):
        items = (await client.get(commands, headers=worker)).json()["items"]
        if items:
            break
        await asyncio.sleep(0.005)
    body_started, release_body, body_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def body():
        try:
            yield b"a" * 1048576
            body_started.set()
            await release_body.wait()
        finally:
            body_closed.set()

    responding = asyncio.create_task(client.post(f"{commands}/{items[0]['id']}/response", headers=worker, content=body()))
    await body_started.wait()
    try:
        assert (await client.delete(path, headers=OWNER)).status_code == 200
        replacement = await client.post(start, headers=OWNER, json={"media_id": "m2"})
        assert replacement.status_code == 429 or body_closed.is_set()
    finally:
        release_body.set()
        assert (await responding).status_code == 404
        assert (await sending).status_code == 409
