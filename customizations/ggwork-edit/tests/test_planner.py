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
