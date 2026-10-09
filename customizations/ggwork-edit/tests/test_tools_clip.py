import json
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY


@pytest.fixture
def skill_config(tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.skills.storage import get_or_new_user_skill_storage, reset_skill_storage

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    config = AppConfig.model_validate(
        {"config_version": 46, "models": [], "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "skills": {"path": str(tmp_path / "skills")}}
    )
    set_app_config(config)
    storage = get_or_new_user_skill_storage("alice", app_config=config)
    for name in ("clip-hook", "clip-highlight"):
        storage.write_custom_skill(name, "SKILL.md", f"---\nname: {name}\ndescription: Synthetic editing skill\n---\nUse editing tools.")
    yield config
    reset_skill_storage()
    reset_app_config()


@pytest.mark.asyncio
async def test_tools_and_page_share_owner_task_without_catalog(api, skill_config):
    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import get_tool, submit_tool

    client, service, _ = api
    store = ExtensionData("edit-run")
    await EditingLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("edit-run", "run-1", "thread-1", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    payload = {
        "request_id": "natural-1",
        "title": "Synthetic",
        "requirements": {"profile": "hook", "instructions": "Make one cut", "output_count": 1, "duration_seconds": 30, "aspect_ratio": "9:16"},
    }
    task = json.loads(await submit_tool.coroutine(request=payload, runtime=runtime))["task"]
    assert task["status"] == "waiting"
    assert task["source_thread_id"] == "thread-1"
    assert json.loads(await submit_tool.coroutine(request=payload, runtime=runtime))["task"]["id"] == task["id"]
    page = await client.get(f"/api/editing/tasks/{task['id']}", headers={"test-owner": "alice"})
    assert page.json()["id"] == task["id"]
    assert json.loads(await get_tool.coroutine(task_id=task["id"], runtime=runtime))["task"]["id"] == task["id"]
    assert (await client.get(f"/api/editing/tasks/{task['id']}", headers={"test-owner": "bob"})).status_code == 404
    schema = submit_tool.tool_call_schema.model_json_schema()
    assert not {"owner_id", "runtime", "source_thread_id"}.intersection(schema["$defs"]["Submit"]["properties"])
    other = SimpleNamespace(context={"user_id": "bob", EXTENSION_TASK_STORE_KEY: store})
    with pytest.raises(PermissionError):
        await get_tool.coroutine(task_id=task["id"], runtime=other)


@pytest.mark.asyncio
async def test_named_skills_require_owner_enabled_and_role_admission(skill_config):
    from deerflow.skills.storage import get_or_new_user_skill_storage

    from ggwork_edit.capability import resolve_command

    alice = {"user_id": "alice", "user_role": "user"}
    assert (await resolve_command("/clip-hook make a cut", alice)).skill.name == "clip-hook"
    assert (await resolve_command("$ggwork-edit/clip-hook make a cut", alice)).skill.name == "clip-hook"
    assert await resolve_command("/tmp/clip-hook", alice) is None
    assert await resolve_command("$unregistered/clip-hook make a cut", alice) is None
    assert await resolve_command("/clip-hook make a cut", {"user_id": "bob"}) is None
    storage = get_or_new_user_skill_storage("alice", app_config=skill_config)
    storage.set_skill_enabled_state("clip-hook", False)
    assert await resolve_command("/clip-hook make a cut", alice) is None
    storage.set_skill_enabled_state("clip-hook", True)
    from deerflow.config.authorization_config import AuthorizationConfig

    skill_config.authorization = AuthorizationConfig.model_validate(
        {
            "enabled": True,
            "fail_closed": True,
            "provider": {"use": "deerflow.authz.rbac:RbacAuthorizationProvider", "config": {"roles": {"user": {"skills": {"allow": ["clip-highlight"]}}}}},
        }
    )
    assert await resolve_command("/clip-hook make a cut", alice) is None
    assert (await resolve_command("/clip-highlight make a cut", alice)).skill.name == "clip-highlight"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/clip-hook make a cut", "$ggwork-edit/clip-hook make a cut"])
async def test_pick_model_gate_admits_registered_tools_without_catalog(api, skill_config, tmp_path, command):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.middleware import PickModelGate, PickToolGate
    from ggwork_pick.service import PickService
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage

    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import get_tool, submit_tool

    store = ExtensionData("edit-gate")
    info = TaskInfo("edit-gate", "r", "t", "lead")
    pick = PickService(tmp_path / "no-mirror")
    await pick.initialize(api[1].session_factory)
    await PickLifecycle(pick).on_task_start(ExtensionData("app"), store, info)
    await EditingLifecycle(api[1]).on_task_start(ExtensionData("app"), store, info)
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    clone = submit_tool.model_copy(update={"description": "Host configured description"})
    request = ModelRequest(
        model=SimpleNamespace(),
        runtime=runtime,
        messages=[HumanMessage(content=command)],
        tools=[clone, get_tool, {"name": "clip_submit"}, {"name": "bash"}],
    )

    async def capture(adjusted):
        return adjusted

    adjusted = await PickModelGate().awrap_model_call(request, capture)
    assert adjusted.tools == [clone, get_tool]
    assert "clip-hook" in adjusted.system_message.content
    forged = SimpleNamespace(runtime=runtime, tool_call={"name": "clip_submit"}, tool={"name": "clip_submit"})
    with pytest.raises(ValueError, match="不允许"):
        await PickToolGate().awrap_tool_call(forged, capture)
    from deerflow.skills.storage import get_or_new_user_skill_storage

    storage = get_or_new_user_skill_storage("alice", app_config=skill_config)
    for name in ("clip-hook", "clip-highlight"):
        storage.set_skill_enabled_state(name, False)
    adjusted = await PickModelGate().awrap_model_call(request, capture)
    assert adjusted.tools == [get_tool]


@pytest.mark.asyncio
async def test_change_version_keeps_original_and_requires_fresh_verification(api, skill_config):
    from test_contract import REQUEST, ready_task

    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import change_version_tool

    client, parent, _, _, _ = await ready_task(api)
    store = ExtensionData("version")
    await EditingLifecycle(api[1]).on_task_start(ExtensionData("app"), store, TaskInfo("version", "r", "t", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    version = json.loads(
        await change_version_tool.coroutine(parent["id"], "changed-1", {**REQUEST["requirements"], "instructions": "A different opening"}, runtime)
    )["task"]
    assert version["parent_task_id"] == parent["id"]
    assert version["id"] != parent["id"]
    assert version["status"] == "waiting"
    current = (await client.get(f"/api/editing/tasks/{version['id']}", headers={"test-owner": "alice"})).json()
    assert current["source_manifest"]["files"][0]["sha256"] is None
    assert (await client.get(f"/api/editing/tasks/{parent['id']}", headers={"test-owner": "alice"})).json()["requirements"]["instructions"] == "A dialogue hook"


@pytest.mark.asyncio
async def test_waiting_tool_intent_discovers_paired_device_and_prepares_same_task(api, skill_config):
    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import get_tool, prepare_tool, submit_tool

    client, service, _ = api
    store = ExtensionData("prepare")
    await EditingLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("prepare", "r", "t", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    submitted = json.loads(
        await submit_tool.coroutine(
            {
                "request_id": "waiting-1",
                "title": "Drama",
                "requirements": {"instructions": "Cut", "output_count": 1, "duration_seconds": 30, "aspect_ratio": "9:16"},
            },
            runtime,
        )
    )["task"]
    paired = (await client.post("/api/editing/devices", headers={"test-owner": "alice"}, json={"name": "My Mac"})).json()["device"]
    overview = json.loads(await get_tool.coroutine(runtime))
    assert overview["devices"][0]["id"] == paired["id"]
    continued = json.loads(await prepare_tool.coroutine(submitted["id"], {"device_id": paired["id"]}, runtime))["task"]
    assert continued["id"] == submitted["id"]
    assert continued["device_id"] == paired["id"]


@pytest.mark.asyncio
async def test_large_task_receipts_and_history_keep_identity_through_host_budget(api, skill_config, tmp_path):
    from deerflow.agents.middlewares.tool_output_budget_middleware import _patch_tool_message
    from deerflow.config.tool_output_config import ToolOutputConfig
    from langchain_core.messages import ToolMessage

    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import get_tool, submit_tool

    client, service, _ = api
    store = ExtensionData("budget")
    await EditingLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("budget", "r", "t", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    files = [{"media_id": f"m-{i}", "name": f"Episode {i}", "episode": i, "relative_path": f"episode-{i}.mp4", "size_bytes": 100} for i in range(1, 61)]
    ids = []
    for index in range(6):
        raw = await submit_tool.coroutine(
            {
                "request_id": f"large-{index}",
                "title": "Long drama",
                "requirements": {"instructions": "x" * 10000, "output_count": 10, "duration_seconds": 30, "aspect_ratio": "9:16"},
                "source_manifest": {"version": 1, "grant_id": "source", "files": files},
            },
            runtime,
        )
        patched = _patch_tool_message(ToolMessage(content=raw, name="clip_submit", tool_call_id="budget"), ToolOutputConfig(), str(tmp_path))
        receipt = json.loads(patched.content)["task"]
        ids.append(receipt["id"])
        assert receipt["source_count"] == 60
        assert receipt["available_actions"] == ["stop"]
        assert len(receipt["outputs"]) == 10
        full = (await client.get(f"/api/editing/tasks/{receipt['id']}", headers={"test-owner": "alice"})).json()
        assert len(full["source_manifest"]["files"]) == 60
        assert full["requirements"]["instructions"] == "x" * 10000
    found = []
    offset = 0
    while offset is not None:
        raw = await get_tool.coroutine(runtime, offset=offset, limit=2)
        patched = _patch_tool_message(ToolMessage(content=raw, name="clip_get", tool_call_id="history"), ToolOutputConfig(), str(tmp_path))
        page = json.loads(patched.content)
        assert len(page["items"]) == 2
        found.extend(item["id"] for item in page["items"])
        offset = page["next_offset"]
    assert set(found) == set(ids)


@pytest.mark.asyncio
async def test_receipt_bounds_worker_diagnostics_and_plan_pages_preserve_all_cuts(api, skill_config, tmp_path):
    from deerflow.agents.middlewares.tool_output_budget_middleware import _patch_tool_message
    from deerflow.config.tool_output_config import ToolOutputConfig
    from langchain_core.messages import ToolMessage
    from test_contract import OWNER, REQUEST, ready_task

    from ggwork_edit.context import EditingLifecycle
    from ggwork_edit.tools import get_tool

    client, task, device, worker, attempt = await ready_task(api, request={**REQUEST, "title": "\x01" * 255})
    store = ExtensionData("maximum")
    await EditingLifecycle(api[1]).on_task_start(ExtensionData("app"), store, TaskInfo("maximum", "r", "t" * 128, "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store})
    first = json.loads(await get_tool.coroutine(runtime, task_id=task["id"], section="plan", limit=2))
    second = json.loads(await get_tool.coroutine(runtime, task_id=task["id"], section="plan", limit=2, offset=first["plan_cuts"]["next_offset"]))
    assert [cut["output_id"] for cut in first["plan_cuts"]["items"] + second["plan_cuts"]["items"]] == ["out-1", "out-2", "out-3"]
    assert second["plan_cuts"]["next_offset"] is None
    failed = await client.post(
        f"/api/editing/worker/devices/{device}/tasks/{task['id']}/report",
        headers=worker,
        json={
            "attempt_id": attempt["id"],
            "fence": attempt["fence"],
            "event_id": "e" * 128,
            "kind": "failure",
            "error": "\x01" * 2000,
        },
    )
    assert failed.status_code == 200
    raw = await get_tool.coroutine(runtime, task_id=task["id"])
    patched = _patch_tool_message(ToolMessage(content=raw, name="clip_get", tool_call_id="maximum"), ToolOutputConfig(), str(tmp_path))
    receipt = json.loads(patched.content)["task"]
    assert receipt["id"] == task["id"]
    assert receipt["available_actions"] == ["retry"]
    assert [output["id"] for output in receipt["outputs"]] == ["out-1", "out-2", "out-3"]
    full = (await client.get(f"/api/editing/tasks/{task['id']}", headers=OWNER)).json()
    assert full["outputs"][0]["error"] == "\x01" * 2000
