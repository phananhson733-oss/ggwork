"""Exercise real historical tools and analysis against synthetic owned snapshots."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from .fakes import operating_rows, snapshot_from_rows


@pytest_asyncio.fixture
async def feedback_history(pick_db_url, tmp_path):
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.feedback.contracts import FeedbackReply
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.feedback.sync import RefreshOutcome
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    pick = PickRepository(factory, "alice")
    await Importer(pick, service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"a","title":"Synthetic Wolf","language":"en","theater":"ReelShort"}]', "json"
    )
    result = await SelectionService(pick).query({}, thread_id="thread", run_id="old", call_id="old")
    empty = await SelectionService(pick).query({}, thread_id="thread", run_id="empty", call_id="empty")
    repo = FeedbackRepository(factory, "alice")
    rows = operating_rows()
    versions = []
    for views in (150, 900):
        rows["observations"][1]["播放量"] = views
        snapshot = snapshot_from_rows(rows)
        run = await repo.claim("manual")
        versions.append((await repo.publish(run["id"], snapshot))["id"])
    await repo.freeze_result(
        result["id"],
        FeedbackReply(
            status="ok",
            feedback_version_id=versions[0],
            scan_started_at=snapshot.scan_started_at,
            scan_completed_at=snapshot.scan_completed_at,
            freshness="historical",
        ),
    )
    service.feedback = SimpleNamespace(
        enabled=True,
        owner_id="alice",
        repository=lambda owner: FeedbackRepository(factory, owner),
        refresh=AsyncMock(
            return_value=RefreshOutcome("ok", version_id=versions[1], scan_started_at="2026-10-07T12:00:00Z", verified_at="2026-10-07T12:00:08Z")
        ),
    )

    async def runtime_for(record):
        store = ExtensionData("task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "new", "thread", "lead"))
        runtime = SimpleNamespace(
            context={"user_id": "alice", "pick_reference": {"result_id": record["id"]}, EXTENSION_TASK_STORE_KEY: store}, tool_call_id="history-read"
        )
        return runtime, task_from_runtime(runtime)

    try:
        yield SimpleNamespace(service=service, pick=pick, repo=repo, result=result, empty=empty, versions=versions, runtime_for=runtime_for)
    finally:
        # The feedback service is an isolated synthetic facade with no background jobs.
        service.feedback = None
        await service.stop()
        await engine.dispose()


async def read_history(tool_name, record, runtime, *, item_id=None):
    from ggwork_pick.feedback.tools import get_feedback_tool
    from ggwork_pick.tools import get_drama_detail_tool

    item_id = item_id or record["items"][0]["item_id"]
    if tool_name == "feedback":
        return json.loads(await get_feedback_tool.coroutine(query={"result_id": record["id"], "item_ids": [item_id]}, runtime=runtime))
    return json.loads(await get_drama_detail_tool.coroutine(result_id=record["id"], item_id=item_id, runtime=runtime))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["feedback", "detail"])
@pytest.mark.parametrize("historical_first", [True, False])
@pytest.mark.parametrize("has_feedback", [True, False])
async def test_actual_historical_tools_and_analysis_share_one_pin(feedback_history, tool_name, historical_first, has_feedback):
    from ggwork_pick.feedback.tools import analyze_feedback_tool

    history = feedback_history
    record = history.result if has_feedback else history.empty
    runtime, task = await history.runtime_for(record)

    async def analyze():
        return json.loads(await analyze_feedback_tool.coroutine(query={"group_by": "language"}, runtime=runtime))

    if historical_first:
        reply = await read_history(tool_name, record, runtime)
        assert task.feedback_checked is True
        analysis = await analyze()
        history.service.feedback.refresh.assert_not_called()
        if has_feedback:
            assert analysis["feedback_version_id"] == history.versions[0]
            assert task.feedback_pin.version_id == history.versions[0]
        else:
            assert task.feedback_pin is None
            assert analysis["status"] == "unavailable"
            assert analysis["feedback_version_id"] is None
        # Repeating the same history is compatible, including history with no feedback.
        assert await read_history(tool_name, record, runtime) == reply
    else:
        assert (await analyze())["feedback_version_id"] == history.versions[1]
        reply = await read_history(tool_name, record, runtime)
        feedback = reply if tool_name == "feedback" else reply["feedback"]
        assert feedback["status"] == "unavailable"
        assert "不能同时混用" in feedback["notice"]
        assert feedback["feedback_version_id"] is None
        assert feedback["items"] == []
        assert task.feedback_pin.version_id == history.versions[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["feedback", "detail"])
async def test_historical_reads_accept_the_same_version_and_reject_a_failed_pin(feedback_history, tool_name):
    from ggwork_pick.feedback.runtime import FeedbackPin

    history = feedback_history
    runtime, task = await history.runtime_for(history.result)
    task.feedback_checked = True
    task.feedback_pin = FeedbackPin(history.versions[0], None, None, "fresh_scan")
    reply = await read_history(tool_name, history.result, runtime)
    feedback = reply if tool_name == "feedback" else reply["feedback"]
    assert feedback["feedback_version_id"] == history.versions[0]
    assert feedback["freshness"] == "historical"
    assert task.plugin_read is True
    task.feedback_failure = {"status": "refresh_failed"}
    reply = await read_history(tool_name, history.result, runtime)
    feedback = reply if tool_name == "feedback" else reply["feedback"]
    assert feedback["status"] == "unavailable"
    assert "不能同时混用" in feedback["notice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_feedback", [True, False])
async def test_feature_off_preserves_owned_historical_detail(feedback_history, has_feedback):
    history = feedback_history
    history.service.feedback.enabled = False
    record = history.result if has_feedback else history.empty
    runtime, task = await history.runtime_for(record)
    reply = await read_history("detail", record, runtime)
    assert reply["item"]["title"] == "Synthetic Wolf"
    if has_feedback:
        assert reply["feedback"]["feedback_version_id"] == history.versions[0]
        assert task.plugin_read is True
    else:
        assert "feedback" not in reply
    history.service.feedback.refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["feedback", "detail"])
@pytest.mark.parametrize("boundary", ["unbound", "item", "owner", "thread"])
async def test_rejected_history_does_not_establish_a_feedback_pin(feedback_history, tool_name, boundary):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    history = feedback_history
    runtime, task = await history.runtime_for(history.empty)
    record = history.result
    item_id = None
    if boundary == "item":
        record, item_id = history.empty, "foreign-item"
    elif boundary in {"owner", "thread"}:
        pick = history.pick
        if boundary == "owner":
            pick = PickRepository(history.service.session_factory, "bob")
            await Importer(pick, history.service.data_dir).catalog(b'[{"source":"synthetic","source_id":"b","title":"Private Drama","language":"en"}]', "json")
        record = await SelectionService(pick).query({}, thread_id="foreign" if boundary == "thread" else "thread", run_id="other", call_id="other")
    reply = await read_history(tool_name, record, runtime, item_id=item_id)
    assert reply["status"] == "rejected"
    assert task.feedback_checked is False
    assert task.plugin_read is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["analysis", "candidates"])
@pytest.mark.parametrize("writer_order", ["queued", "subsequent"])
async def test_malformed_feedback_blocks_external_writes(feedback_history, monkeypatch, tool_name, writer_order):
    import asyncio

    from deerflow.tools.mcp_metadata import tag_mcp_tool
    from langchain_core.tools import tool

    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.feedback.sync import RefreshOutcome
    from ggwork_pick.feedback.tools import analyze_feedback_tool
    from ggwork_pick.middleware import PickToolGate
    from ggwork_pick.tools import query_candidates_tool

    @tool("synthetic_send")
    def writer(content: str) -> str:
        """Synthetic external-effect tool; its handler is never allowed to execute."""
        return content

    @tool("web_search")
    def reader(query: str) -> str:
        """Synthetic read-only tool; no network calls."""
        return query

    tag_mcp_tool(writer, server_name="synthetic", transport="http")
    history = feedback_history
    rows = operating_rows()
    marker = "SYNTHETIC_OVERLONG_CURRENCY"
    rows["cps_auto"][0]["币种"] = [marker]
    run = await history.repo.claim("manual")
    version = await history.repo.publish(run["id"], snapshot_from_rows(rows))
    history.service.feedback.refresh.return_value = RefreshOutcome(
        "ok", version_id=version["id"], scan_started_at="2026-10-07T12:00:00Z", verified_at="2026-10-07T12:00:08Z"
    )
    runtime, task = await history.runtime_for(history.empty)
    runtime.tool_call_id = "malformed-feedback"
    snapshot_read = asyncio.Event()
    release = asyncio.Event()
    writer_queued = asyncio.Event()
    original_snapshot = FeedbackRepository.snapshot

    async def paused_snapshot(repo, version_id):
        snapshot = await original_snapshot(repo, version_id)
        snapshot_read.set()
        await release.wait()
        return snapshot

    monkeypatch.setattr(FeedbackRepository, "snapshot", paused_snapshot)
    gate = PickToolGate()
    selected = analyze_feedback_tool if tool_name == "analysis" else query_candidates_tool
    read_request = SimpleNamespace(runtime=runtime, tool_call={"name": selected.name}, tool=selected)
    write_request = SimpleNamespace(runtime=runtime, tool_call={"name": writer.name}, tool=writer)
    write_handler = AsyncMock(return_value="must not execute")

    async def read_feedback(_request):
        if tool_name == "analysis":
            return await selected.coroutine(query={"group_by": "language"}, runtime=runtime)
        return await selected.coroutine(filters={}, runtime=runtime)

    async def write_after_read():
        writer_queued.set()
        return await gate.awrap_tool_call(write_request, write_handler)

    feedback_call = asyncio.create_task(gate.awrap_tool_call(read_request, read_feedback))
    await snapshot_read.wait()
    if writer_order == "queued":
        write_call = asyncio.create_task(write_after_read())
        await writer_queued.wait()
    release.set()
    reply = json.loads(await feedback_call)
    if writer_order == "subsequent":
        write_call = asyncio.create_task(write_after_read())
    outcome = (await asyncio.gather(write_call, return_exceptions=True))[0]
    assert reply["status"] == "rejected"
    assert marker in reply["notice"]
    assert isinstance(outcome, ValueError) and "用户确认" in str(outcome)
    write_handler.assert_not_called()
    assert task.plugin_read is True
    assert (task.tool_calls, task.plugin_calls) == (1, 0)
    safe_read = SimpleNamespace(runtime=runtime, tool_call={"name": reader.name}, tool=reader)
    assert await gate.awrap_tool_call(safe_read, AsyncMock(return_value="safe read")) == "safe read"
    assert task.plugin_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["analysis", "candidates"])
@pytest.mark.parametrize("no_source", ["disabled", "foreign_owner", "no_historical_feedback"])
async def test_tools_without_feedback_do_not_mark_an_external_read(feedback_history, tool_name, no_source):
    from ggwork_pick.feedback.tools import analyze_feedback_tool
    from ggwork_pick.tools import query_candidates_tool

    history = feedback_history
    runtime, task = await history.runtime_for(history.empty)
    runtime.tool_call_id = "no-feedback"
    if no_source == "disabled":
        history.service.feedback.enabled = False
    elif no_source == "foreign_owner":
        history.service.feedback.owner_id = "bob"
    else:
        reply = await read_history("feedback", history.empty, runtime)
        assert reply["status"] == "unavailable"
    if tool_name == "analysis":
        reply = json.loads(await analyze_feedback_tool.coroutine(query={"group_by": "language"}, runtime=runtime))
        assert reply["status"] == "unavailable"
    else:
        reply = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
        assert reply["items"]
        assert "feedback" not in reply
    assert task.plugin_read is False
    history.service.feedback.refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["feedback", "detail"])
async def test_plural_readonly_history_keeps_each_feedback_version_without_repinning_live_analysis(feedback_history, tool_name):
    from test_plural_references import envelope, runtime_for

    from ggwork_pick.context import PickTask

    history = feedback_history
    old = await history.repo.result_evidence(history.result["id"])
    await history.repo.freeze_result(history.empty["id"], old.model_copy(update={"feedback_version_id": history.versions[1]}))
    records = [history.result, history.empty]
    runtime, store = await runtime_for(history.service, envelope(records))
    for index, record in enumerate(records):
        reply = await read_history(tool_name, record, runtime)
        evidence = reply if tool_name == "feedback" else reply["feedback"]
        assert evidence["status"] == "ok"
        assert evidence["feedback_version_id"] == history.versions[index]
        assert evidence["freshness"] == "historical"
    task = store.get(PickTask)
    assert task.feedback_pin is None and task.feedback_checked is False
    assert task.plugin_read is True
    history.service.feedback.refresh.assert_not_called()
