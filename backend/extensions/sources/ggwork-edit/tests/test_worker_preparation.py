"""Real Gateway/native source preparation boundary; no cloud model calls."""

import os
import shutil
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from test_contract import OWNER, worker_identity

from ggwork_edit.worker.runtime import WorkerSession
from ggwork_edit.worker.storage import WorkerStore, digest

ASSETS = Path(os.environ.get("GGWORK_NATIVE_ASSETS", "/nonexistent"))
pytestmark = pytest.mark.skipif(not (ASSETS / "synthetic-drama/episode-01.mp4").exists(), reason="explicit native synthetic media required")


@pytest_asyncio.fixture
async def preparation(api, tmp_path):
    client, service, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Receipt test Mac"})).json()["device"]["id"]
    identity = worker_identity(app, device)
    receiving = tmp_path / "incoming"
    receiving.mkdir()
    store = WorkerStore(tmp_path / "worker")
    store.setup(
        gateway="https://test",
        device_id=device,
        token="synthetic",
        output_root=tmp_path / "outputs",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    store.grant("incoming", receiving, receive=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=identity) as worker:
        await worker.post(
            f"/api/editing/worker/devices/{device}/heartbeat",
            json={"platform": "darwin-arm64", "ready": True, "grants": ["incoming"], "worker_version": "test"},
        )
        yield client, WorkerSession(store, worker), receiving, device


def selected(media_id="new-media", relative_path="episode-01.mp4", *, size=None, episode=1):
    source = ASSETS / "synthetic-drama/episode-01.mp4"
    return {
        "media_id": media_id,
        "name": "Episode 1.mp4",
        "relative_path": relative_path,
        "episode": episode,
        "size_bytes": source.stat().st_size if size is None else size,
        "state": "selected",
    }


async def create(client, device, files, request_id="fresh-upload", **fields):
    response = await client.post(
        "/api/editing/tasks",
        headers=OWNER,
        json={
            "request_id": request_id,
            "title": "Synthetic receipt test",
            "device_id": device,
            "requirements": {
                "profile": "hook",
                "instructions": "Original dialogue",
                "language": "en",
                "aspect_ratio": "9:16",
                "duration_seconds": 5,
                "output_count": 1,
            },
            "source_manifest": {"version": 1, "grant_id": "incoming", "files": files},
            **fields,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_fresh_upload_cannot_adopt_same_name_size_existing_file(preparation):
    client, session, receiving, device = preparation
    shutil.copyfile(ASSETS / "synthetic-drama/episode-01.mp4", receiving / "episode-01.mp4")
    old_hash = digest(receiving / "episode-01.mp4")
    task = await create(client, device, [selected()])
    # No upload endpoint or native Receiver call occurred for this task.
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "waiting"
    assert not current["manifest_frozen"]
    assert current["source_manifest"]["files"][0]["state"] == "selected"
    assert digest(receiving / "episode-01.mp4") == old_hash


async def upload(session, task, source, *, transfer_id="receipt-transfer"):
    import asyncio

    from ggwork_edit.worker.transfer import Receiver

    data = (ASSETS / "synthetic-drama/episode-01.mp4").read_bytes()
    command = {
        "task_id": task["id"],
        "transfer_id": transfer_id,
        "media_id": source["media_id"],
        "source_manifest": task["source_manifest"],
        "offset": 0,
        "length": len(data),
        "final": True,
    }
    await asyncio.to_thread(Receiver(session.store).receive, command, data)


@pytest.mark.asyncio
async def test_completed_receipt_queues_exact_bytes_and_cannot_authorize_another_task(preparation):
    client, session, receiving, device = preparation
    source = selected(relative_path="new-media.mp4")
    task = await create(client, device, [source])
    await session.prepare()
    await upload(session, task, source)
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "queued" and current["manifest_frozen"]
    sha = digest(ASSETS / "synthetic-drama/episode-01.mp4")
    assert current["source_manifest"]["files"][0]["sha256"] == sha
    copied_selection = await create(client, device, [source], request_id="another-task")
    await session.prepare()
    other = (await client.get("/api/editing/tasks/" + copied_selection["id"], headers=OWNER)).json()
    assert other["status"] == "waiting" and other["native_preparation_error"] == "source_receipt_missing"
    assert digest(receiving / "new-media.mp4") == sha


@pytest.mark.asyncio
async def test_selected_set_revision_retains_unchanged_file_receipt(preparation):
    client, session, receiving, device = preparation
    first = selected(media_id="first", relative_path="first.mp4")
    missing = selected(media_id="missing", relative_path="missing.mp4", episode=2)
    task = await create(client, device, [first, missing])
    await upload(session, task, first)
    await session.prepare()
    waiting = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert waiting["status"] == "waiting"
    revision = await client.post(
        "/api/editing/tasks/" + task["id"] + "/prepare",
        headers=OWNER,
        json={"device_id": device, "source_manifest": {"version": 2, "grant_id": "incoming", "files": [first]}},
    )
    assert revision.status_code == 200, revision.text
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "queued" and current["source_manifest"]["version"] == 2
    assert current["source_manifest"]["files"][0]["sha256"] == digest(receiving / "first.mp4")


@pytest.mark.asyncio
async def test_received_bytes_changed_after_receipt_cannot_queue(preparation):
    client, session, receiving, device = preparation
    source = selected(relative_path="changed.mp4")
    task = await create(client, device, [source])
    await upload(session, task, source)
    with (receiving / "changed.mp4").open("r+b") as stream:
        stream.seek(100)
        original = stream.read(1)
        stream.seek(100)
        stream.write(bytes([original[0] ^ 1]))
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "waiting" and not current["manifest_frozen"]


async def directory_parent(client, session, receiving, device, *, request_id="directory-parent"):
    shutil.copyfile(ASSETS / "synthetic-drama/episode-01.mp4", receiving / "episode-01.mp4")
    response = await client.post(
        "/api/editing/tasks",
        headers=OWNER,
        json={
            "request_id": request_id,
            "title": "Directory parent",
            "device_id": device,
            "requirements": {
                "profile": "hook",
                "instructions": "Original dialogue",
                "language": "en",
                "aspect_ratio": "9:16",
                "duration_seconds": 5,
                "output_count": 1,
            },
            "source_directory": {"grant_id": "incoming", "relative_path": "."},
        },
    )
    assert response.status_code == 200
    await session.prepare()
    parent = (await client.get("/api/editing/tasks/" + response.json()["id"], headers=OWNER)).json()
    assert parent["status"] == "queued" and parent["manifest_frozen"]
    return parent


def clone_files(parent):
    return [{**source, "state": "selected", "sha256": None, "duration_seconds": None} for source in parent["source_manifest"]["files"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_stop_ack", [False, True])
async def test_stopped_attempt_releases_same_session_for_next_directory(preparation, lose_stop_ack):
    import json

    client, original, receiving, device = preparation

    class StopAckTransport(httpx.AsyncBaseTransport):
        lost = False

        async def handle_async_request(self, request):
            response = await original.http._transport.handle_async_request(request)
            if request.url.path.endswith("/report") and json.loads(request.content)["kind"] == "stopped" and lose_stop_ack and not self.lost:
                self.lost = True
                raise httpx.ReadTimeout("Lost stopped acknowledgment", request=request)
            return response

    async with httpx.AsyncClient(transport=StopAckTransport(), base_url="https://test", headers=original.http.headers) as worker:
        session = WorkerSession(original.store, worker)
        first = await directory_parent(client, session, receiving, device)
        claimed = await session.claim()
        assert (await client.post(f"/api/editing/tasks/{first['id']}/stop", headers=OWNER, json={})).json()["status"] == "stopping"
        if lose_stop_ack:
            with pytest.raises(httpx.ReadTimeout):
                await session.execute(claimed)
            assert session.stop.is_set()
            await session.execute(await session.claim())
        else:
            await session.execute(claimed)
        assert (await client.get(f"/api/editing/tasks/{first['id']}", headers=OWNER)).json()["status"] == "stopped"
        assert not session.store.journal()
        # Same WorkerSession and real ffprobe: no process restart as a workaround.
        second = await directory_parent(client, session, receiving, device, request_id="after-stop")
        assert second["status"] == "queued" and second["manifest_frozen"]
        assert not session.stop.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("global_stop", ["shutdown", "revocation"])
async def test_stop_ack_cleanup_preserves_shutdown_and_authorization_loss(preparation, global_stop):
    import json

    client, original, receiving, device = preparation

    class InterruptedAckTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            stopping = request.url.path.endswith("/report") and json.loads(request.content)["kind"] == "stopped"
            if stopping and global_stop == "revocation":
                assert (await client.post(f"/api/editing/devices/{device}/revoke", headers=OWNER, json={})).status_code == 200
            response = await original.http._transport.handle_async_request(request)
            if stopping and global_stop == "shutdown":
                session.shutdown.set()  # Local process signal arrives while its ACK is in flight.
            return response

    async with httpx.AsyncClient(transport=InterruptedAckTransport(), base_url="https://test", headers=original.http.headers) as worker:
        session = WorkerSession(original.store, worker)
        first = await directory_parent(client, session, receiving, device)
        claimed = await session.claim()
        await client.post(f"/api/editing/tasks/{first['id']}/stop", headers=OWNER, json={})
        if global_stop == "revocation":
            with pytest.raises(httpx.HTTPStatusError) as denied:
                await session.execute(claimed)
            assert denied.value.response.status_code == 403
            assert session.authorization_lost
        else:
            await session.execute(claimed)
            assert not session.store.journal()
        assert session.shutdown.is_set() and session.stop.is_set()
        await session.execute({**claimed, "status": "stopped"})
        assert session.shutdown.is_set() and session.stop.is_set()


@pytest.mark.asyncio
async def test_removing_invalid_discovered_file_preserves_exact_directory_subset(preparation):
    client, session, receiving, device = preparation
    shutil.copyfile(ASSETS / "synthetic-drama/episode-01.mp4", receiving / "episode-01.mp4")
    (receiving / "episode-02.mp4").write_bytes(b"invalid synthetic video")
    task = await create(
        client, device, [], request_id="directory-remove", source_manifest=None, source_directory={"grant_id": "incoming", "relative_path": "."}
    )
    await session.prepare()
    before = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert before["native_preparation_error"] == "native_process_failed"
    selected_files = [source for source in clone_files(before) if source["episode"] == 1]
    updated = await client.post(
        f"/api/editing/tasks/{task['id']}/prepare",
        headers=OWNER,
        json={
            "device_id": device,
            "source_manifest": {**before["source_manifest"], "version": 2, "files": selected_files},
        },
    )
    assert updated.status_code == 200
    await session.prepare()
    current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert current["status"] == "queued" and current["manifest_frozen"]
    assert current["source_directory"] == before["source_directory"]
    assert [source["episode"] for source in current["source_manifest"]["files"]] == [1]
    assert current["source_manifest"]["files"][0]["sha256"] == digest(receiving / "episode-01.mp4")
    assert (receiving / "episode-02.mp4").read_bytes() == b"invalid synthetic video"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["added", "media_id", "name", "episode", "relative_path", "size_bytes", "grant", "device", "device_only", "device_rediscover"]
)
async def test_directory_provenance_never_authorizes_added_or_retargeted_sources(preparation, changed):
    client, session, receiving, device = preparation
    shutil.copyfile(ASSETS / "synthetic-drama/episode-01.mp4", receiving / "episode-01.mp4")
    (receiving / "episode-02.mp4").write_bytes(b"invalid synthetic video")
    task = await create(
        client, device, [], request_id="directory-retarget", source_manifest=None, source_directory={"grant_id": "incoming", "relative_path": "."}
    )
    await session.prepare()
    before = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    files = [source for source in clone_files(before) if source["episode"] == 1]
    manifest = {**before["source_manifest"], "version": 2, "files": files}
    if changed == "added":
        shutil.copyfile(receiving / "episode-01.mp4", receiving / "added.mp4")
        files.append(selected(media_id="new-source", relative_path="added.mp4", episode=3))
    elif changed in ("media_id", "name", "relative_path"):
        files[0][changed] = "retargeted.mp4"
        if changed == "relative_path":
            shutil.copyfile(receiving / "episode-01.mp4", receiving / "retargeted.mp4")
    elif changed == "episode":
        files[0]["episode"] = 3
    elif changed == "size_bytes":
        files[0]["size_bytes"] += 1
    elif changed == "grant":
        manifest["grant_id"] = "other"
        session.store.grant("other", receiving)
        await session.request("POST", "/heartbeat", json={"platform": "darwin-arm64", "ready": True, "grants": ["incoming", "other"], "worker_version": "test"})
    target_device = device
    if changed in ("device", "device_only", "device_rediscover"):
        target_device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Different Mac"})).json()["device"]["id"]
    payload = {"device_id": target_device}
    if changed == "device_rediscover":
        payload["source_directory"] = before["source_directory"]
    elif changed != "device_only":
        payload["source_manifest"] = manifest
    response = await client.post(f"/api/editing/tasks/{task['id']}/prepare", headers=OWNER, json=payload)
    assert response.status_code == 200
    if changed == "device_rediscover":
        assert response.json()["source_directory"] == before["source_directory"]
        assert response.json()["source_manifest"] is None
    else:
        assert response.json()["source_directory"] is None
    if target_device == device:
        await session.prepare()
        current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
        assert current["status"] == "waiting" and not current["manifest_frozen"]
        assert current["native_preparation_error"] == "source_receipt_missing"


@pytest.mark.asyncio
async def test_explicit_version_reuses_only_exact_frozen_parent_identity(preparation):
    client, session, receiving, device = preparation
    parent = await directory_parent(client, session, receiving, device)
    child = await create(client, device, clone_files(parent), request_id="exact-clone", parent_task_id=parent["id"])
    mismatched = await create(client, device, [{**clone_files(parent)[0], "media_id": "another-media"}], request_id="false-clone", parent_task_id=parent["id"])
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + child["id"], headers=OWNER)).json()
    other = (await client.get("/api/editing/tasks/" + mismatched["id"], headers=OWNER)).json()
    assert current["status"] == "queued"
    assert current["source_manifest"]["files"][0]["sha256"] == parent["source_manifest"]["files"][0]["sha256"]
    assert other["status"] == "waiting" and other["native_preparation_error"] == "source_changed"


@pytest.mark.asyncio
async def test_cross_device_parent_does_not_revoke_worker_and_fresh_receipt_wins(preparation):
    client, session, receiving, device = preparation
    other_device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Another Mac"})).json()["device"]["id"]
    parent = await create(client, other_device, [selected()], request_id="foreign-device-parent")
    source = selected(relative_path="own-upload.mp4")
    child = await create(client, device, [source], request_id="cross-device-version", parent_task_id=parent["id"])
    await session.prepare()
    blocked = (await client.get("/api/editing/tasks/" + child["id"], headers=OWNER)).json()
    assert blocked["status"] == "waiting" and blocked["native_preparation_error"] == "source_changed"
    assert not session.authorization_lost and not session.shutdown.is_set()
    await upload(session, child, source)
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + child["id"], headers=OWNER)).json()
    assert current["status"] == "queued"
    assert not session.authorization_lost


@pytest.mark.asyncio
async def test_partial_transfer_has_no_receipt_until_complete(preparation):
    import asyncio

    from ggwork_edit.worker.transfer import Receiver

    client, session, receiving, device = preparation
    source = selected(relative_path="chunked.mp4")
    task = await create(client, device, [source])
    data = (ASSETS / "synthetic-drama/episode-01.mp4").read_bytes()
    cut = len(data) // 2
    command = {
        "task_id": task["id"],
        "transfer_id": "chunked-transfer",
        "media_id": source["media_id"],
        "source_manifest": task["source_manifest"],
        "offset": 0,
        "length": cut,
        "final": False,
    }
    receiver = Receiver(session.store)
    await asyncio.to_thread(receiver.receive, command, data[:cut])
    await session.prepare()
    waiting = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert waiting["status"] == "waiting" and waiting["native_preparation_error"] == "source_receipt_missing"
    assert not (receiving / "chunked.mp4").exists()
    await asyncio.to_thread(receiver.receive, {**command, "offset": cut, "length": len(data) - cut, "final": True}, data[cut:])
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "queued"


@pytest.mark.asyncio
async def test_same_task_replacement_media_identity_requires_its_own_receipt(preparation):
    client, session, receiving, device = preparation
    first = selected(media_id="first", relative_path="first.mp4")
    task = await create(client, device, [first, selected(media_id="missing", relative_path="missing.mp4", episode=2)])
    await upload(session, task, first)
    changed = {**first, "media_id": "replacement"}
    response = await client.post(
        "/api/editing/tasks/" + task["id"] + "/prepare",
        headers=OWNER,
        json={"device_id": device, "source_manifest": {"version": 2, "grant_id": "incoming", "files": [changed]}},
    )
    assert response.status_code == 200
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).json()
    assert current["status"] == "waiting" and current["native_preparation_error"] == "source_receipt_missing"
    assert digest(receiving / "first.mp4") == digest(ASSETS / "synthetic-drama/episode-01.mp4")


@pytest.mark.asyncio
async def test_parent_reuse_rechecks_frozen_bytes(preparation):
    client, session, receiving, device = preparation
    parent = await directory_parent(client, session, receiving, device)
    child = await create(client, device, clone_files(parent), request_id="changed-parent-bytes", parent_task_id=parent["id"])
    with (receiving / "episode-01.mp4").open("r+b") as stream:
        stream.seek(-1, 2)
        value = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([value[0] ^ 1]))
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + child["id"], headers=OWNER)).json()
    assert current["status"] == "waiting" and current["native_preparation_error"] == "source_changed"
    historical = (await client.get("/api/editing/tasks/" + parent["id"], headers=OWNER)).json()
    assert historical["source_manifest"] == parent["source_manifest"]


@pytest.mark.asyncio
async def test_unverified_parent_is_not_upload_authority(preparation):
    client, session, receiving, device = preparation
    shutil.copyfile(ASSETS / "synthetic-drama/episode-01.mp4", receiving / "episode-01.mp4")
    parent = await create(client, device, [selected()], request_id="unverified-parent")
    child = await create(client, device, [selected()], request_id="unverified-child", parent_task_id=parent["id"])
    await session.prepare()
    current = (await client.get("/api/editing/tasks/" + child["id"], headers=OWNER)).json()
    assert current["status"] == "waiting" and current["native_preparation_error"] == "source_changed"
    assert not current["manifest_frozen"]
