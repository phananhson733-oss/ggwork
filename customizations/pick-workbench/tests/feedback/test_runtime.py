"""The real tools bind feedback to immutable candidate evidence; all data are synthetic."""

import json
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from .fakes import operating_rows, snapshot_from_rows


@pytest.mark.asyncio
async def test_candidate_feedback_is_frozen_and_shared_by_tools_in_one_run(pick_db_url, tmp_path):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.feedback.contracts import SourcePage
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_candidates_tool

    source_rows = operating_rows()
    constructions = []

    class Source:
        def __init__(self):
            self.tables = {table.table_id: table for table in snapshot_from_rows(source_rows).tables}
            constructions.append(self)

        async def fields(self, table):
            return self.tables[table.table_id].fields

        async def page(self, table, fields, offset):
            return SourcePage(table_id=table.table_id, records=self.tables[table.table_id].records, has_more=False)

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    service.feedback = FeedbackSyncService(factory, enabled=True, owner_id="alice", source_factory=lambda *_args: Source())
    repo = PickRepository(factory, "alice")
    await Importer(repo, service.data_dir).catalog(
        json.dumps(
            [
                {
                    "source": "synthetic",
                    "source_id": "catalog-a",
                    "title": "Synthetic Wolf",
                    "language": "en",
                    "theater": "ReelShort",
                    "posted": {"matched": True, "records": ["SD-A"], "post_count": 3},
                }
            ]
        ).encode(),
        "json",
    )
    store = ExtensionData("task-feedback")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task-feedback", "run-1", "thread-1", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call-1")
    try:
        first = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
        assert first["feedback"]["status"] == "ok"
        version_id = first["feedback"]["feedback_version_id"]
        original_items = (await repo.result(first["id"]))["ordered_items_json"]
        runtime.tool_call_id = "call-2"
        second = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
        assert second["feedback"]["feedback_version_id"] == version_id
        assert len(constructions) == 1
        source_rows["observations"][1]["播放量"] = 900
        latest = await service.feedback.refresh("alice", wait_seconds=2)
        assert latest.version_id != version_id
        frozen = await FeedbackRepository(factory, "alice").result_evidence(first["id"])
        assert frozen.feedback_version_id == version_id
        assert (await repo.result(first["id"]))["ordered_items_json"] == original_items
    finally:
        await service.stop()
        await engine.dispose()


def test_feedback_tools_registered_and_do_not_accept_source_authority():
    from ggwork_pick.feedback.tools import analyze_feedback_tool, get_feedback_tool
    from ggwork_pick.middleware import ALLOWED_TOOLS

    for tool in (analyze_feedback_tool, get_feedback_tool):
        assert tool.name in ALLOWED_TOOLS
        properties = tool.tool_call_schema.model_json_schema()["properties"]
        assert not {"owner_id", "user_id", "base_token", "version_id", "sql", "runtime"}.intersection(properties)


def test_explicit_pick_skill_allows_feedback_tools_when_runtime_offers_them():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[4]
    frontmatter = (root / "skills/public/pick-drama/SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    assert {"pick_get_feedback", "pick_analyze_feedback"}.issubset(yaml.safe_load(frontmatter)["allowed-tools"])


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,owner,offered", [(False, "alice", False), (True, "bob", False), (True, "alice", True)])
async def test_model_only_gets_feedback_tools_and_instructions_when_authorized(enabled, owner, offered):
    from unittest.mock import AsyncMock

    from langchain.agents.middleware.types import ModelRequest

    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickModelGate

    service = SimpleNamespace(feedback=SimpleNamespace(enabled=enabled, owner_id="alice"))
    task = PickTask(service=service, info=TaskInfo("t", "r", "c", "lead"), owner_id=owner)
    task.repository = AsyncMock()
    store = ExtensionData("t")
    store.set(task)
    request = ModelRequest(
        model=SimpleNamespace(),
        messages=[],
        runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}),
        tools=[{"name": "pick_query_candidates"}, {"name": "pick_analyze_feedback"}],
    )
    result = await PickModelGate().awrap_model_call(request, AsyncMock(side_effect=lambda value: value))
    assert any(tool["name"] == "pick_analyze_feedback" for tool in result.tools) is offered
    assert ("运营反馈与外部榜单" in result.system_message.content) is offered


@pytest.mark.asyncio
async def test_country_analysis_returns_unsupported_before_any_source_read():
    from unittest.mock import AsyncMock

    from ggwork_pick.context import PickTask
    from ggwork_pick.feedback.tools import analyze_feedback_tool

    task = PickTask(service=SimpleNamespace(feedback=None), info=TaskInfo("t", "r", "c", "lead"), owner_id="alice")
    task.repository = AsyncMock()
    store = ExtensionData("t")
    store.set(task)
    runtime = SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store})
    answer = json.loads(await analyze_feedback_tool.coroutine(query={"group_by": "country"}, runtime=runtime))
    assert answer["status"] == "unsupported_dimension"
    assert answer["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_first", [True, False])
async def test_parent_feedback_and_cohort_analysis_cannot_alternate_versions(historical_first):
    from unittest.mock import AsyncMock

    from ggwork_pick.feedback.contracts import FeedbackReply
    from ggwork_pick.feedback.runtime import prepare_feedback
    from ggwork_pick.feedback.sync import RefreshOutcome

    historical = FeedbackReply(
        status="ok", feedback_version_id="old", scan_started_at="2026-10-01T00:00:00Z", scan_completed_at="2026-10-01T00:00:08Z", freshness="historical"
    )
    repo = SimpleNamespace(result_evidence=AsyncMock(return_value=historical))
    source = SimpleNamespace(
        enabled=True,
        owner_id="alice",
        repository=lambda _owner: repo,
        refresh=AsyncMock(return_value=RefreshOutcome("ok", version_id="new", verified_at="2026-10-07T00:00:08Z", scan_started_at="2026-10-07T00:00:00Z")),
    )
    task = SimpleNamespace(
        service=SimpleNamespace(feedback=source), owner_id="alice", feedback_checked=False, feedback_pin=None, feedback_failure=None, remaining=lambda: 120
    )
    if historical_first:
        first, _ = await prepare_feedback(task, parent={"id": "parent"})
        second, _ = await prepare_feedback(task)
        assert first.version_id == second.version_id == "old"
        source.refresh.assert_not_called()
    else:
        first, _ = await prepare_feedback(task)
        second, error = await prepare_feedback(task, parent={"id": "parent"})
        assert first.version_id == "new"
        assert second is None
        assert error["status"] == "unavailable"
        assert task.feedback_pin.version_id == "new"
