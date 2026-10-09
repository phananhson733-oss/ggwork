import json

import pytest
from langchain_core.messages import AIMessage
from test_contract import OWNER, ready_task


class Model:
    def __init__(self, plan):
        self.plan = plan
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return AIMessage(content=json.dumps(self.plan))


def valid_plan():
    return {
        "profile": "hook",
        "aspect_ratio": "9:16",
        "language": "auto",
        "outputs": [{"output_id": f"out-{i}", "segments": [{"media_id": "m-1", "start": 0, "end": 30}]} for i in range(1, 4)],
    }


@pytest.mark.asyncio
async def test_device_text_planning_returns_same_task_and_no_paths(api):
    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    model = Model(valid_plan())
    api[2].include_router(build_planner_router(api[1], TextPlanner(model)))
    body = {
        "attempt_id": attempt["id"],
        "fence": attempt["fence"],
        "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Synthetic dialogue"}]}],
    }
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan"
    response = await client.post(path, headers=worker, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == task["id"]
    assert response.json()["plan"] == valid_plan()
    assert response.json()["plan_confirmed"] is True
    assert "1.mp4" not in str(model.messages)
    assert "grant-1" not in str(model.messages)
    assert (await client.post(path, headers=worker, json=body)).json()["plan"] == valid_plan()
    assert len(model.messages) == 1
    assert (await client.post(path, headers=OWNER, json=body)).status_code == 403
    assert (await client.post(path, headers={**worker, "test-owner": "bob"}, json=body)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["unknown_source", "negative", "nan", "beyond_source", "wrong_profile", "missing_output", "duration", "extra_path"])
async def test_invalid_model_plan_is_rejected_without_persistence(api, mutation):
    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    plan = valid_plan()
    segment = plan["outputs"][0]["segments"][0]
    if mutation == "unknown_source":
        segment["media_id"] = "missing"
    if mutation == "negative":
        segment["start"] = -1
    if mutation == "nan":
        segment["start"] = float("nan")
    if mutation == "beyond_source":
        segment.update(start=80, end=110)
    if mutation == "wrong_profile":
        plan["profile"] = "highlight"
    if mutation == "missing_output":
        plan["outputs"].pop()
    if mutation == "duration":
        segment["end"] = 5
    if mutation == "extra_path":
        segment["path"] = "private.mp4"
    api[2].include_router(build_planner_router(api[1], TextPlanner(Model(plan))))
    response = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
        },
    )
    assert response.status_code == 422
    assert (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()["plan"] is None


@pytest.mark.asyncio
async def test_stopped_attempt_cannot_spend_model_or_store_plan(api):
    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    model = Model(valid_plan())
    api[2].include_router(build_planner_router(api[1], TextPlanner(model)))
    await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={})
    response = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
        },
    )
    assert response.status_code == 409
    assert model.messages == []


@pytest.mark.asyncio
async def test_plan_first_waits_for_same_task_confirmation(api):
    from test_contract import HEARTBEAT, REQUEST, worker_identity

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, service, app = api
    device = (await client.post("/api/editing/devices", headers=OWNER, json={"name": "Mac"})).json()["device"]["id"]
    worker = worker_identity(app, device)
    await client.post(f"/api/editing/worker/devices/{device}/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {"version": 1, "grant_id": "grant-1", "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "1.mp4", "size_bytes": 100}]}
    task = (
        await client.post(
            "/api/editing/tasks",
            headers=OWNER,
            json={**REQUEST, "requirements": {**REQUEST["requirements"], "review_plan": True}, "device_id": device, "source_manifest": manifest},
        )
    ).json()
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    await client.post(f"/api/editing/worker/devices/{device}/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})
    attempt = (await client.post(f"/api/editing/worker/devices/{device}/claim", headers=worker, json={"request_id": "claim-1"})).json()["attempt"]
    app.include_router(build_planner_router(service, TextPlanner(Model(valid_plan()))))
    response = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
        },
    )
    assert response.json()["status"] == "awaiting_plan"
    assert response.json()["plan_confirmed"] is False
    confirmed = await client.post(f"/api/editing/tasks/{task['id']}/confirm-plan", headers=OWNER, json={})
    assert confirmed.json()["id"] == task["id"]
    assert confirmed.json()["plan_confirmed"] is True
    assert confirmed.json()["attempt"]["id"] == attempt["id"]


# Reuse the real host auth/CSRF fixture; no test principal for this boundary.
from test_gateway_auth import gateway  # noqa: E402,F401
from test_tools_clip import skill_config  # noqa: E402,F401


@pytest.mark.asyncio
async def test_real_device_bearer_planning_rechecks_owner_skill_and_model(gateway, skill_config):  # noqa: F811
    from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
    from deerflow.extensions.gateway import include_contributed_routers
    from deerflow.extensions.registry import ExtensionRegistry
    from deerflow.skills.storage import get_or_new_user_skill_storage
    from test_contract import HEARTBEAT, REQUEST

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, service, user_id = gateway
    app = client._transport.app
    model = Model(valid_plan())
    registry = ExtensionRegistry()
    with registry.attributed_to("ggwork_edit:planner"):
        registry.bearer_routers((build_planner_router(service, TextPlanner(model, model_name="synthetic")),), service)
    assert include_contributed_routers(app, registry.build()) == []
    storage = get_or_new_user_skill_storage(user_id, app_config=skill_config)
    storage.write_custom_skill("clip-hook", "SKILL.md", "---\nname: clip-hook\ndescription: Synthetic hook\n---\nUse clip tools.")
    csrf = "synthetic-plan-csrf"
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    browser = {CSRF_HEADER_NAME: csrf}
    paired = (await client.post("/api/editing/devices", headers=browser, json={"name": "Mac"})).json()
    device = paired["device"]["id"]
    worker = {"Authorization": "Bearer " + paired["token"]}
    base = f"/api/editing/worker/devices/{device}"
    await client.post(base + "/heartbeat", headers=worker, json=HEARTBEAT)
    manifest = {"version": 1, "grant_id": "grant-1", "files": [{"media_id": "m-1", "name": "Ep1", "episode": 1, "relative_path": "1.mp4", "size_bytes": 100}]}
    task = (await client.post("/api/editing/tasks", headers=browser, json={**REQUEST, "device_id": device, "source_manifest": manifest})).json()
    manifest["files"][0].update(state="verified", sha256="a" * 64, duration_seconds=90)
    await client.post(base + f"/tasks/{task['id']}/manifest", headers=worker, json={"source_manifest": manifest})
    attempt = (await client.post(base + "/claim", headers=worker, json={"request_id": "plan-claim"})).json()["attempt"]
    path = base + f"/tasks/{task['id']}/plan"
    body = {
        "attempt_id": attempt["id"],
        "fence": attempt["fence"],
        "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
    }
    assert (await client.post(path, headers=browser, json=body)).status_code == 401
    storage.set_skill_enabled_state("clip-hook", False)
    assert (await client.post(path, headers=worker, json=body)).status_code == 403
    assert model.messages == []
    storage.set_skill_enabled_state("clip-hook", True)
    response = await client.post(path, headers=worker, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["plan"] == valid_plan()


@pytest.mark.asyncio
async def test_concurrent_same_attempt_planning_spends_once_and_returns_same_plan(api):
    import asyncio

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    class ConcurrentModel:
        calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            plan = valid_plan()
            plan["outputs"][0]["segments"][0]["end"] = 31 - self.calls
            await asyncio.sleep(0.1)
            return AIMessage(content=json.dumps(plan))

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    model = ConcurrentModel()
    api[2].include_router(build_planner_router(api[1], TextPlanner(model)))
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan"
    body = {
        "attempt_id": attempt["id"],
        "fence": attempt["fence"],
        "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
    }
    responses = await asyncio.gather(client.post(path, headers=worker, json=body), client.post(path, headers=worker, json=body))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["plan"] == responses[1].json()["plan"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_live_profile_revocation_prevents_cloud_planning(api):
    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    model = Model(valid_plan())
    api[2].include_router(build_planner_router(api[1], TextPlanner(model)))

    async def revoked(owner):
        return set()

    api[1].execution_profiles = revoked
    response = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
        },
    )
    assert response.status_code == 409
    assert model.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_stop_during_cloud_call_remains_responsive_and_fences_plan(api, provider_fails):
    import asyncio

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingModel:
        async def ainvoke(self, messages):
            entered.set()
            await release.wait()
            if provider_fails:
                raise RuntimeError("private provider failure after stop")
            return AIMessage(content=json.dumps(valid_plan()))

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    api[2].include_router(build_planner_router(api[1], TextPlanner(WaitingModel())))
    planning = asyncio.create_task(
        client.post(
            f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
            headers=worker,
            json={
                "attempt_id": attempt["id"],
                "fence": attempt["fence"],
                "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
            },
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        stopped = await asyncio.wait_for(client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={}), timeout=2)
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopping"
    finally:
        release.set()
    response = await asyncio.wait_for(planning, timeout=2)
    assert response.status_code == 409
    assert (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()["plan"] is None


@pytest.mark.asyncio
async def test_provider_failure_is_safe_terminal_and_requires_explicit_new_attempt(api):
    import asyncio

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    class RecoveringModel:
        calls = 0
        fail = True

        async def ainvoke(self, messages):
            self.calls += 1
            await asyncio.sleep(0.05)
            if self.fail:
                raise RuntimeError("private-endpoint private-credential private-transcript")
            return AIMessage(content=json.dumps(valid_plan()))

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    model = RecoveringModel()
    api[2].include_router(build_planner_router(api[1], TextPlanner(model)))
    path = f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan"
    body = {
        "attempt_id": attempt["id"],
        "fence": attempt["fence"],
        "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
    }
    responses = await asyncio.gather(client.post(path, headers=worker, json=body), client.post(path, headers=worker, json=body))
    for response in responses:
        assert response.status_code == 502
        assert response.json() == {"detail": {"code": "planner_unavailable", "retry": "explicit"}}
        assert "private" not in response.text
    assert model.calls == 1
    current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert current["status"] == "failed"
    assert current["plan"] is None
    assert {output["error"] for output in current["outputs"]} == {"planner_unavailable"}
    model.fail = False
    assert (await client.post(path, headers=worker, json=body)).status_code == 502
    assert model.calls == 1
    retried = await client.post(f"/api/editing/tasks/{task['id']}/retry", headers=OWNER, json={"request_id": "explicit-planning-retry", "stage": "planning"})
    assert retried.status_code == 200
    claim = await client.post(f"/api/editing/worker/devices/{device}/claim", headers=worker, json={"request_id": "claim-after-provider-recovery"})
    next_attempt = claim.json()["attempt"]
    recovered = await client.post(path, headers=worker, json={**body, "attempt_id": next_attempt["id"], "fence": next_attempt["fence"]})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["plan"] == valid_plan()
    assert model.calls == 2


@pytest.mark.asyncio
async def test_cancelled_model_call_propagates_without_manufacturing_failure(api):
    import asyncio

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    class CancelledModel:
        async def ainvoke(self, messages):
            raise asyncio.CancelledError()

    client, task, device, worker, attempt = await ready_task(api, store_plan=False)
    api[2].include_router(build_planner_router(api[1], TextPlanner(CancelledModel())))
    with pytest.raises(asyncio.CancelledError):
        await client.post(
            f"/api/editing/worker/devices/{device}/tasks/{task['id']}/plan",
            headers=worker,
            json={
                "attempt_id": attempt["id"],
                "fence": attempt["fence"],
                "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
            },
        )
    current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert current["status"] == "running"
    assert current["plan"] is None
    assert all(output["error"] is None for output in current["outputs"])


@pytest.mark.asyncio
@pytest.mark.parametrize("review_plan", [False, True])
@pytest.mark.parametrize("failure_code", ["planner_outcome_unknown", "planner_unavailable"])
async def test_plan_committed_after_recovery_read_wins_ambiguous_failure_report(api, review_plan, failure_code):
    from test_contract import REQUEST

    from ggwork_edit.planner import TextPlanner
    from ggwork_edit.planner_routes import build_planner_router

    request = {**REQUEST, "requirements": {**REQUEST["requirements"], "review_plan": review_plan}}
    client, task, device, worker, attempt = await ready_task(api, store_plan=False, request=request)
    api[2].include_router(build_planner_router(api[1], TextPlanner(Model(valid_plan()))))
    base = f"/api/editing/worker/devices/{device}/tasks/{task['id']}"
    # The worker's last recovery GET observes no plan. The provider commits before its failure POST.
    assert (await client.get(base, headers=worker)).json()["plan"] is None
    planned = await client.post(
        base + "/plan",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "transcripts": [{"media_id": "m-1", "segments": [{"start": 0, "end": 90, "text": "Dialogue"}]}],
        },
    )
    assert planned.status_code == 200
    failure = {"attempt_id": attempt["id"], "fence": attempt["fence"], "event_id": "late-planning-failure", "kind": "failure", "error": failure_code}
    reported = await client.post(base + "/report", headers=worker, json=failure)
    assert reported.status_code == 200, reported.text
    current = reported.json()["task"]
    assert current["status"] == ("awaiting_plan" if review_plan else "running")
    assert current["plan"] == valid_plan()
    assert current["plan_confirmed"] is (not review_plan)
    assert all(output["status"] == "pending" and output["error"] is None for output in current["outputs"])
    assert (await client.post(base + "/report", headers=worker, json=failure)).json() == reported.json()
    changed = await client.post(base + "/report", headers=worker, json={**failure, "error": "changed-failure"})
    assert changed.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["stop", "stale"])
async def test_winning_plan_does_not_bypass_failure_report_fences(api, barrier):
    client, task, device, worker, attempt = await ready_task(api)
    if barrier == "stop":
        stopped = await client.post(f"/api/editing/tasks/{task['id']}/stop", headers=OWNER, json={})
        assert stopped.json()["status"] == "stopping"
    response = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"] + (barrier == "stale"),
            "event_id": "fenced-planning-failure",
            "kind": "failure",
            "error": "planner_outcome_unknown",
        },
    )
    assert response.status_code == 409
    current = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert current["status"] == ("stopping" if barrier == "stop" else "running")
    assert current["plan"] == valid_plan()
