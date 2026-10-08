import pytest

OWNER = {"test-owner": "alice"}
REQUEST = {
    "request_id": "intent-1",
    "title": "Synthetic drama",
    "requirements": {"instructions": "A dialogue hook", "output_count": 3, "duration_seconds": 30, "aspect_ratio": "9:16"},
}


@pytest.mark.asyncio
async def test_owner_intent_is_idempotent_private_and_durable(api):
    client, service, app = api
    assert (await client.post("/api/editing/tasks", json=REQUEST)).status_code == 401
    first = await client.post("/api/editing/tasks", json=REQUEST, headers=OWNER)
    assert first.status_code == 200
    task = first.json()
    assert task["status"] == "waiting"
    assert task["source_thread_id"] is None
    duplicate = await client.post("/api/editing/tasks", json=REQUEST, headers=OWNER)
    assert duplicate.json()["id"] == task["id"]
    changed = await client.post("/api/editing/tasks", json={**REQUEST, "title": "Changed"}, headers=OWNER)
    assert changed.status_code == 409
    other = await client.get("/api/editing/tasks/" + task["id"], headers={"test-owner": "bob"})
    assert other.status_code == 404
    forged = await client.post("/api/editing/tasks", json={**REQUEST, "owner_id": "bob"}, headers=OWNER)
    assert forged.status_code == 422
    from ggwork_edit.service import EditingService

    restarted = EditingService(hook_available=True)
    await restarted.initialize(service.session_factory)
    assert (await restarted.repository("alice").get_task(task["id"]))["title"] == "Synthetic drama"


@pytest.mark.asyncio
async def test_device_verifies_entire_selection_before_admission(api):
    client, service, app = api
    paired = await client.post("/api/editing/devices", headers=OWNER, json={"name": "Test Mac"})
    assert paired.status_code == 200
    device = paired.json()["device"]["id"]
    assert paired.json()["token"].startswith("ggwe_")
    assert "token" not in (await client.get("/api/editing/devices", headers=OWNER)).text
    manifest = {
        "version": 1,
        "grant_id": "grant-1",
        "files": [
            {"media_id": f"m-{i}", "name": f"Episode {i}", "episode": i, "relative_path": f"{i}.mp4", "size_bytes": 100, "state": "selected"}
            for i in range(1, 25)
        ],
    }
    task = (await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    verified = {**manifest, "files": [{**f, "sha256": "a" * 64, "duration_seconds": 90, "state": "verified"} for f in manifest["files"]]}
    incomplete = {**verified, "files": verified["files"][:-1] + [manifest["files"][-1]]}
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/manifest"
    partial = await client.post(path, headers=worker, json={"source_manifest": incomplete})
    assert partial.status_code == 200
    assert partial.json()["status"] == "waiting"
    assert (await client.post(f"/api/editing/worker/devices/{device}/claim", headers=worker, json={"request_id": "claim-0"})).json()["task"] is None
    accepted = await client.post(path, headers=worker, json={"source_manifest": verified})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "queued"
    assert accepted.json()["manifest_frozen"] is True
    changed = await client.post(f"/api/editing/tasks/{task['id']}/prepare", headers=OWNER, json={"device_id": device, "source_manifest": manifest})
    assert changed.status_code == 409
    wrong = await client.post(path.replace(device, "other-device"), headers=worker, json={"source_manifest": verified})
    assert wrong.status_code == 403


HEARTBEAT = {"platform": "darwin-arm64", "ready": True, "grants": ["grant-1"], "worker_version": "test-1"}


def worker_identity(app, device):
    from dataclasses import dataclass

    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal

    @dataclass(frozen=True)
    class TestPrincipal(ExtensionPrincipal):
        subject_id: str | None = None

    setattr(
        app.state,
        EXTENSION_PRINCIPAL_RESOLVER_KEY,
        lambda req: TestPrincipal(req.headers["test-owner"], subject_id=req.headers.get("test-device")) if "test-owner" in req.headers else None,
    )
    return {**OWNER, "test-device": device}


async def ready_task(api):
    client, service, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {"version": 1, "grant_id": "grant-1", "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "1.mp4", "size_bytes": 100}]}
    task = (await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    await client.post(f"/api/editing/worker/devices/{device}/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})
    claim = (await client.post(f"/api/editing/worker/devices/{device}/claim", headers=worker, json={"request_id": "claim-1"})).json()
    return client, task, device, worker, claim["attempt"]


RESULT = {
    "artifact_id": "artifact-1",
    "sha256": "b" * 64,
    "size_bytes": 1000,
    "duration_seconds": 30,
    "width": 1080,
    "height": 1920,
    "video_codec": "h264",
    "audio_codec": "aac",
    "verified": True,
}


@pytest.mark.asyncio
async def test_partial_delivery_targeted_retry_and_stop_fence(api):
    client, task, device, worker, attempt = await ready_task(api)
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report"

    def event(event_id, kind, **kw):
        return {"event_id": event_id, "attempt_id": attempt["id"], "fence": attempt["fence"], "kind": kind, **kw}

    output = await client.post(path, headers=worker, json=event("output-1", "output", output_id="out-1", result=RESULT))
    assert output.status_code == 200
    await client.post(path, headers=worker, json=event("output-2", "output", output_id="out-2", result={**RESULT, "artifact_id": "artifact-2"}))
    await client.post(path, headers=worker, json=event("fail-3", "failure", output_id="out-3", error="decode_failed"))
    done = await client.post(path, headers=worker, json=event("finish", "complete"))
    assert done.json()["task"]["status"] == "partial"
    assert done.json()["task"]["completed_count"] == 2
    retry = await client.post(f"/api/editing/tasks/{task['id']}/retry", headers=OWNER, json={"request_id": "retry-1", "output_ids": ["out-3"]})
    assert retry.status_code == 200
    assert retry.json()["outputs"][0]["result"] == RESULT
    attempt = (await client.post(f"/api/editing/worker/devices/{device}/claim", headers=worker, json={"request_id": "claim-2"})).json()["attempt"]
    assert attempt["output_ids"] == ["out-3"]
    stopping = await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={})
    assert stopping.json()["status"] == "stopping"
    late = await client.post(path, headers=worker, json=event("late", "output", output_id="out-3", result={**RESULT, "artifact_id": "artifact-3"}))
    assert late.status_code == 409
    blocked = await client.post(f"/api/editing/tasks/{task['id']}/retry", headers=OWNER, json={"request_id": "retry-2", "output_ids": ["out-3"]})
    assert blocked.status_code == 409
    heartbeat = await client.post(path, headers=worker, json=event("pulse", "heartbeat"))
    assert heartbeat.json()["stop_requested"] is True
    ack = await client.post(path, headers=worker, json=event("ack", "stopped"))
    assert ack.json()["task"]["status"] == "stopped"
    assert ack.json()["task"]["completed_count"] == 2


@pytest.mark.asyncio
async def test_directory_discovery_and_plan_confirmation_share_task(api):
    client, service, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    body = {
        **REQUEST,
        "device_id": device,
        "requirements": {**REQUEST["requirements"], "profile": "highlight", "review_plan": True},
        "source_directory": {"grant_id": "grant-1", "relative_path": "drama"},
    }
    response = await client.post("/api/editing/tasks", headers=OWNER, json=body)
    assert response.status_code == 200
    task = response.json()
    base = f"/api/editing/worker/devices/{device}"
    waiting = await client.get(base + "/preparations", headers=worker)
    assert waiting.json()["items"][0]["id"] == task["id"]
    manifest = {
        "version": 1,
        "grant_id": "grant-1",
        "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "drama/1.mp4", "size_bytes": 100}],
    }
    discovered = await client.post(base + f"/tasks/{task['id']}/discovery", headers=worker, json={"source_manifest": manifest})
    assert discovered.status_code == 200
    assert discovered.json()["status"] == "waiting"
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    await client.post(base + f"/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})
    attempt = (await client.post(base + "/claim", headers=worker, json={"request_id": "claim-plan"})).json()["attempt"]
    # Planner owns validation of its strict domain plan; the repository owns durable attempt fencing.
    repo = service.repository("alice")
    stored = await repo.store_plan(device, task["id"], attempt["id"], attempt["fence"], {"outputs": [{"id": "out-1"}]})
    assert stored["status"] == "awaiting_plan"
    assert stored["plan_confirmed"] is False
    confirmed = await client.post(f"/api/editing/tasks/{task['id']}/confirm-plan", headers=OWNER, json={})
    assert confirmed.json()["plan_confirmed"] is True
    assert confirmed.json()["attempt"]["id"] == attempt["id"]
    current = await client.get(base + f"/tasks/{task['id']}", headers=worker)
    assert current.json()["plan"] == {"outputs": [{"id": "out-1"}]}


@pytest.mark.asyncio
async def test_browser_cannot_forge_source_conversation(api):
    client, _, _ = api
    response = await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "source_thread_id": "someone-elses-thread"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_intents_and_conflicting_retries_serialize(api):
    import asyncio

    client, service, _ = api
    responses = await asyncio.gather(*[client.post("/api/editing/tasks", headers=OWNER, json=REQUEST) for _ in range(8)])
    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["id"] for r in responses}) == 1
    assert len((await client.get("/api/editing/tasks", headers=OWNER)).json()["items"]) == 1


@pytest.mark.asyncio
async def test_disabling_capability_blocks_claim_but_keeps_history(api):
    client, task, device, worker, attempt = await ready_task(api)
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report"
    await client.post("/api/editing/settings", headers=OWNER, json={"skill_enabled": False})
    refusal = await client.post(
        path,
        headers=worker,
        json={"event_id": "output", "attempt_id": attempt["id"], "fence": attempt["fence"], "kind": "output", "output_id": "out-1", "result": RESULT},
    )
    assert refusal.status_code == 409
    assert (await client.get("/api/editing/tasks/" + task["id"], headers=OWNER)).status_code == 200
    assert (await client.get("/api/editing/capabilities", headers=OWNER)).json()["profiles"][0]["available"] is False


@pytest.mark.asyncio
async def test_output_validation_event_receipts_and_completed_first(api):
    client, task, device, worker, attempt = await ready_task(api)
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report"
    base = {"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": "first", "kind": "output", "output_id": "out-1", "result": RESULT}
    invalid = await client.post(path, headers=worker, json={**base, "result": {**RESULT, "duration_seconds": 5}})
    assert invalid.status_code == 409
    first = await client.post(path, headers=worker, json=base)
    assert first.status_code == 200
    assert (await client.post(path, headers=worker, json=base)).status_code == 200
    assert (await client.post(path, headers=worker, json={**base, "result": {**RESULT, "size_bytes": 99}})).status_code == 409
    assert (await client.post(path, headers=worker, json={**base, "event_id": "overwrite"})).status_code == 409
    early = await client.post(path, headers=worker, json={"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": "early", "kind": "complete"})
    assert early.status_code == 409
    for i in (2, 3):
        assert (
            await client.post(
                path, headers=worker, json={**base, "event_id": f"output-{i}", "output_id": f"out-{i}", "result": {**RESULT, "artifact_id": f"artifact-{i}"}}
            )
        ).status_code == 200
    done = await client.post(path, headers=worker, json={"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": "done", "kind": "complete"})
    assert done.json()["task"]["status"] == "completed"
    stopped = await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={})
    assert stopped.json()["status"] == "completed"
    assert stopped.json()["completed_count"] == 3


@pytest.mark.asyncio
async def test_plan_confirmation_cannot_authorize_new_planning_attempt(api):
    from ggwork_edit.contracts import CreateTask, RetryTask, WorkerReport

    client, task, device, worker, attempt = await ready_task(api)
    repo = api[1].repository("alice")
    # A new revision requests review before render, preserving its source task.
    detail = await repo.get_task(task["id"])
    request = CreateTask.model_validate(
        {
            **REQUEST,
            "request_id": "review-revision",
            "parent_task_id": task["id"],
            "device_id": device,
            "requirements": {**REQUEST["requirements"], "review_plan": True},
        }
    )
    revised = await repo.create_task(request)
    # Stop existing running attempt honestly before the next device claim.
    await repo.stop_task(task["id"])
    await repo.report(device, task["id"], WorkerReport(attempt_id=attempt["id"], fence=attempt["fence"], event_id="ack", kind="stopped"))
    from ggwork_edit.contracts import Manifest, PrepareTask

    selected = detail["source_manifest"]
    selected["files"][0].update(state="selected", sha256=None, duration_seconds=None)
    await repo.prepare(revised["id"], PrepareTask(device_id=device, source_manifest=selected))
    selected["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    await repo.verify_manifest(device, revised["id"], Manifest.model_validate(selected))
    current = (await repo.claim(device, "review-claim"))["attempt"]
    await repo.store_plan(device, revised["id"], current["id"], current["fence"], {"outputs": []})
    await repo.confirm_plan(revised["id"])
    path = f"/api/editing/worker/devices/{device}/tasks/{revised['id']}/report"
    base = {"attempt_id": current["id"], "fence": current["fence"]}
    replay = await client.post(path, headers=worker, json={**base, "event_id": "regress", "kind": "stage", "stage": "awaiting_plan"})
    assert replay.status_code == 409
    assert (await repo.get_task(revised["id"]))["status"] == "running"
    await repo.report(device, revised["id"], WorkerReport(**base, event_id="failed", kind="failure", error="render unavailable"))
    await repo.retry(revised["id"], RetryTask(request_id="new-plan", stage="planning"))
    current = (await repo.claim(device, "second-review-claim"))["attempt"]
    base = {"attempt_id": current["id"], "fence": current["fence"]}
    premature = await client.post(path, headers=worker, json={**base, "event_id": "render", "kind": "stage", "stage": "rendering"})
    assert premature.status_code == 409
    assert (await repo.get_task(revised["id"]))["plan_confirmed"] is False


@pytest.mark.asyncio
async def test_history_pagination_and_revision_preserve_original(api):
    client, service, _ = api
    first = (await client.post("/api/editing/tasks", headers=OWNER, json=REQUEST)).json()
    second = (
        await client.post(
            "/api/editing/tasks",
            headers=OWNER,
            json={
                **REQUEST,
                "request_id": "revision",
                "parent_task_id": first["id"],
                "requirements": {**REQUEST["requirements"], "instructions": "A different story"},
            },
        )
    ).json()
    page = (await client.get("/api/editing/tasks?limit=1", headers=OWNER)).json()
    assert page["total"] == 2 and page["next_offset"] == 1
    assert page["items"][0]["id"] == second["id"]
    older = (await client.get("/api/editing/tasks?limit=1&offset=1", headers=OWNER)).json()
    assert older["next_offset"] is None
    assert older["items"][0]["requirements"]["instructions"] == "A dialogue hook"
    assert second["parent_task_id"] == first["id"]
    assert (await client.post("/api/editing/tasks", headers={"test-owner": "bob"}, json={**REQUEST, "parent_task_id": first["id"]})).status_code == 404


@pytest.mark.asyncio
async def test_waiting_directory_intent_binds_newly_paired_device_without_resubmission(api):
    client, service, app = api
    task = (
        await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "source_directory": {"grant_id": "grant-1", "relative_path": "drama"}})
    ).json()
    assert task["device_id"] is None
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "New Mac"})).json()["device"]["id"]
    prepared = await client.post(f"/api/editing/tasks/{task['id']}/prepare", headers=OWNER, json={"device_id": device})
    assert prepared.status_code == 200
    assert prepared.json()["id"] == task["id"]
    assert prepared.json()["source_directory"] == {"grant_id": "grant-1", "relative_path": "drama"}
    worker = worker_identity(app, device)
    waiting = await client.get(f"/api/editing/worker/devices/{device}/preparations", headers=worker)
    assert waiting.json()["items"][0]["id"] == task["id"]


@pytest.mark.asyncio
async def test_stopped_unprepared_intent_does_not_advertise_impossible_retry(api):
    client, _, _ = api
    task = (await client.post("/api/editing/tasks", headers=OWNER, json=REQUEST)).json()
    stopped = (await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER)).json()
    assert stopped["status"] == "stopped"
    assert "retry" not in stopped["available_actions"]


@pytest.mark.asyncio
async def test_native_preparation_error_is_private_scoped_and_clearable(api):
    client, _, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    manifest = {"version": 1, "grant_id": "grant-1", "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "1.mp4", "size_bytes": 100}]}
    task = (await client.post("/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    worker = worker_identity(app, device)
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/preparation-error"
    body = {"error": "source_changed"}
    assert (await client.post(path, headers=OWNER, json=body)).status_code == 403
    assert (await client.post(path, headers={**worker, "test-owner": "bob"}, json=body)).status_code == 404
    assert (await client.post(path.replace(device, "other-device"), headers=worker, json=body)).status_code == 403
    assert (await client.post(path, headers=worker, json={"error": "/Users/private/media.mp4 failed"})).status_code == 422
    failed = await client.post(path, headers=worker, json=body)
    assert failed.status_code == 200
    assert failed.json()["native_preparation_error"] == "source_changed"
    assert "source_changed" in failed.json()["preparation_reasons"]
    assert failed.json()["id"] == task["id"] and failed.json()["status"] == "waiting"
    cleared = await client.post(f"/api/editing/tasks/{task['id']}/prepare", headers=OWNER, json={"device_id": device})
    assert cleared.json()["native_preparation_error"] is None
    await client.post(path, headers=worker, json=body)
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    verified = await client.post(path.replace("/preparation-error", "/manifest"), headers=worker, json={"source_manifest": manifest})
    assert verified.json()["native_preparation_error"] is None
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    assert (await client.post(path, headers=worker, json=body)).status_code == 409
    assert (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()["status"] == "queued"


@pytest.mark.asyncio
async def test_successful_discovery_clears_preparation_error_without_new_intent(api):
    client, _, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    task = (
        await client.post(
            "/api/editing/tasks", headers=OWNER, json={**REQUEST, "device_id": device, "source_directory": {"grant_id": "grant-1", "relative_path": "drama"}}
        )
    ).json()
    worker = worker_identity(app, device)
    base = f"/api/editing/worker/devices/{device}/tasks/{task['id']}"
    failed = await client.post(base + "/preparation-error", headers=worker, json={"error": "source_directory_empty"})
    assert failed.json()["native_preparation_error"] == "source_directory_empty"
    manifest = {
        "version": 1,
        "grant_id": "grant-1",
        "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "drama/1.mp4", "size_bytes": 100}],
    }
    for _ in range(2):
        discovered = await client.post(base + "/discovery", headers=worker, json={"source_manifest": manifest})
        assert discovered.json()["native_preparation_error"] is None
        assert discovered.json()["id"] == task["id"]
        assert "source_directory_empty" not in discovered.json()["preparation_reasons"]
        await client.post(base + "/preparation-error", headers=worker, json={"error": "source_directory_empty"})
