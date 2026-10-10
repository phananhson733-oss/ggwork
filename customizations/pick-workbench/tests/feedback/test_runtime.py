"""The real tools bind feedback to immutable candidate evidence; all data are synthetic."""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
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
            self.tables = {table.table_id: table for table in snapshot_from_rows(source_rows, transform_version="feedback-v2").tables}
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
        # Nothing was published yet, so this one query had to scan for itself.
        assert first["feedback"]["freshness"] == "fresh_scan"
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
    published = _status(verified_at="2026-10-07T00:00:08.000000+00:00", current="new")
    repo = SimpleNamespace(result_evidence=AsyncMock(return_value=historical), status=AsyncMock(return_value=published))
    source = SimpleNamespace(
        enabled=True,
        owner_id="alice",
        repository=lambda _owner: repo,
        refresh=AsyncMock(return_value=RefreshOutcome("refresh_pending")),
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


@pytest_asyncio.fixture
async def published_stack(pick_db_url, tmp_path):
    """The real service, tools and SQL over a synthetic source that counts every scan it serves."""
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.feedback.contracts import SourcePageV2
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    rows = operating_rows()
    scans = []

    class Source:
        def __init__(self):
            self.tables = {table.table_id: table for table in snapshot_from_rows(rows, transform_version="feedback-v3").tables}
            scans.append(self)

        async def fields(self, table):
            return self.tables[table.table_id].fields

        async def page(self, table, fields, offset):
            return SourcePageV2(table_id=table.table_id, records=self.tables[table.table_id].records, has_more=False)

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    service.feedback = FeedbackSyncService(factory, enabled=True, owner_id="alice", source_factory=lambda *_args: Source())
    await Importer(PickRepository(factory, "alice"), service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"catalog-a","title":"Synthetic Wolf","language":"en","theater":"ReelShort"}]', "json"
    )

    async def run(run_id):
        store = ExtensionData(run_id)
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run_id, run_id, "thread-1", "lead"))
        return SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id=f"{run_id}-call-1")

    async def publish():
        outcome = await service.feedback.refresh("alice", wait_seconds=10, trigger="scheduled")
        assert outcome.status == "ok"
        return outcome.version_id

    try:
        yield SimpleNamespace(service=service, rows=rows, scans=scans, run=run, publish=publish, feedback=service.feedback.repository("alice"))
    finally:
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_query_and_analysis_use_the_published_version_without_a_scan_of_their_own(published_stack):
    from datetime import datetime

    from ggwork_pick.feedback.tools import analyze_feedback_tool
    from ggwork_pick.tools import query_candidates_tool

    stack = published_stack
    published = await stack.publish()
    before = await stack.feedback.status()
    runtime = await stack.run("run-1")

    answer = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert answer["feedback"]["status"] == "ok"
    assert answer["feedback"]["feedback_version_id"] == published
    # Not read for this request: the reply says so and carries the published read's own times.
    assert answer["feedback"]["freshness"] == "stale"
    assert answer["feedback"]["notice"] == ""
    assert datetime.fromisoformat(answer["feedback"]["scan_completed_at"]) == datetime.fromisoformat(before["current"]["scan_completed_at"])
    assert datetime.fromisoformat(answer["feedback"]["last_verified_at"]) == datetime.fromisoformat(before["last_verified_at"])

    runtime.tool_call_id = "run-1-call-2"
    analysis = json.loads(await analyze_feedback_tool.coroutine(query={"group_by": "genre"}, runtime=runtime))
    assert analysis["status"] == "ok"
    assert analysis["feedback_version_id"] == published
    assert analysis["freshness"] == "stale"

    after = await stack.feedback.status()
    assert len(stack.scans) == 1
    assert after["last_run"]["id"] == before["last_run"]["id"]
    assert after["last_run"]["trigger"] == "scheduled"


@pytest.mark.asyncio
async def test_one_run_keeps_its_version_when_a_refresh_publishes_mid_run(published_stack):
    from ggwork_pick.tools import query_candidates_tool

    stack = published_stack
    old = await stack.publish()
    runtime = await stack.run("run-1")
    first = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert first["feedback"]["feedback_version_id"] == old

    stack.rows["observations"][1]["播放量"] = 900
    new = await stack.publish()
    assert new != old

    runtime.tool_call_id = "run-1-call-2"
    second = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert second["feedback"]["feedback_version_id"] == old
    later = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=await stack.run("run-2")))
    assert later["feedback"]["feedback_version_id"] == new
    assert len(stack.scans) == 2


@pytest.mark.asyncio
async def test_a_version_past_its_refresh_window_says_so_on_the_candidate(published_stack, monkeypatch):
    from ggwork_pick.feedback import runtime as feedback_runtime
    from ggwork_pick.feedback.tools import analyze_feedback_tool
    from ggwork_pick.tools import query_candidates_tool

    stack = published_stack
    published = await stack.publish()
    monkeypatch.setattr(feedback_runtime, "STALE_NOTICE_SECONDS", 0)
    answer = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=await stack.run("run-1")))
    assert answer["feedback"]["status"] == "ok"
    assert answer["feedback"]["feedback_version_id"] == published
    assert "不是最新数据" in answer["feedback"]["notice"]
    # The notice is part of what the candidate froze, so the notes panel shows it too.
    frozen = await stack.feedback.result_evidence(answer["id"])
    assert frozen.notice == answer["feedback"]["notice"]
    analysis = json.loads(await analyze_feedback_tool.coroutine(query={"group_by": "genre"}, runtime=await stack.run("run-2")))
    assert (analysis["status"], analysis["notice"]) == ("ok", answer["feedback"]["notice"])


def _status(*, verified_at, last_run=None, current="published"):
    return {
        "current": {"id": current, "published_at": "2026-10-10T05:43:06.000000+00:00"} if current else None,
        "last_verified_at": verified_at,
        "running": None,
        "lease_expired": False,
        "last_run": last_run,
    }


@pytest.mark.parametrize(
    "verified_at,last_run,expected",
    [
        ("2026-10-10T07:30:00.000000+00:00", None, ""),
        # One missed slot is ordinary (the Base is edited while it is read); the read time already tells the age.
        ("2026-10-10T06:30:00.000000+00:00", {"status": "failed", "error_code": "source_changed"}, ""),
        ("2026-10-10T05:30:00.000000+00:00", {"status": "success", "error_code": None}, "运营反馈已超过2小时没有成功刷新；"),
        ("2026-10-10T05:30:00.000000+00:00", {"status": "failed", "error_code": "schema_changed"}, "（最近一次失败：飞书反馈字段发生变化）；"),
        (
            "2026-10-09T05:30:00.000000+00:00",
            {"status": "failed", "error_code": "auth_required"},
            "运营反馈已超过26小时没有成功刷新（最近一次失败：需要重新完成飞书用户授权）；",
        ),
        ("2026-10-10T05:30:00.000000+00:00", {"status": "failed", "error_code": "capacity"}, "（最近一次失败：刷新未完成）；"),
        ("2026-10-10T05:30:00.000000+00:00", {"status": "running", "error_code": None}, "运营反馈已超过2小时没有成功刷新；"),
    ],
)
def test_published_pin_reports_a_missed_refresh_window_and_its_reason(verified_at, last_run, expected):
    from datetime import UTC, datetime

    from ggwork_pick.feedback.runtime import published_pin

    pin = published_pin(_status(verified_at=verified_at, last_run=last_run), now=datetime(2026, 10, 10, 8, 0, tzinfo=UTC))
    assert (pin.version_id, pin.verified_at, pin.scan_started_at, pin.freshness) == ("published", verified_at, None, "stale")
    assert expected in pin.notice
    assert bool(pin.notice) is bool(expected)
    if expected:
        assert pin.notice.endswith("本次使用最后一次成功读取的反馈，不是最新数据。")


def test_published_pin_needs_a_published_version():
    from ggwork_pick.feedback.runtime import published_pin

    assert published_pin(_status(verified_at=None, current=None)) is None


def _prepare_case(status, outcome):
    from unittest.mock import AsyncMock

    repo = SimpleNamespace(status=AsyncMock(return_value=status))
    source = SimpleNamespace(enabled=True, owner_id="alice", repository=lambda _owner: repo, refresh=AsyncMock(return_value=outcome))
    task = SimpleNamespace(
        service=SimpleNamespace(feedback=source), owner_id="alice", feedback_checked=False, feedback_pin=None, feedback_failure=None, remaining=lambda: 120
    )
    return task, source


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, "fr_" + "0" * 32])
async def test_a_failing_or_pending_refresh_no_longer_withholds_candidates_once_a_version_exists(receipt):
    from ggwork_pick.feedback.runtime import prepare_feedback
    from ggwork_pick.feedback.sync import RefreshOutcome

    # The receipt is one an earlier turn was handed for a refresh that failed or has long since expired: a retry in
    # the same conversation passes it again, and it must not stand between the user and the published version.
    failed = {"status": "failed", "error_code": "schema_changed"}
    expired = RefreshOutcome("refresh_failed", run_id=receipt, error_code="receipt_expired")
    task, source = _prepare_case(_status(verified_at="2026-10-10T05:43:06.000000+00:00", last_run=failed), expired)
    pin, failure = await prepare_feedback(task, resume_run_id=receipt)
    assert failure is None
    assert (pin.version_id, pin.freshness) == ("published", "stale")
    assert task.feedback_checked and task.feedback_pin is pin and task.feedback_failure is None
    source.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_an_empty_scope_still_waits_on_a_refresh_and_resumes_it_by_receipt():
    from ggwork_pick.feedback.runtime import prepare_feedback
    from ggwork_pick.feedback.sync import RefreshOutcome

    run_id = "fr_" + "1" * 32
    task, source = _prepare_case(_status(verified_at=None, current=None), RefreshOutcome("refresh_pending", run_id=run_id))
    pin, failure = await prepare_feedback(task)
    assert pin is None
    assert (failure["status"], failure["feedback_refresh_id"]) == ("refresh_pending", run_id)
    source.refresh.assert_awaited_once()

    # Still nothing published in the next turn: the receipt continues that refresh instead of starting another.
    done = RefreshOutcome("ok", run_id=run_id, version_id="fresh", verified_at="2026-10-10T08:00:08Z", scan_started_at="2026-10-10T08:00:00Z")
    task, source = _prepare_case(_status(verified_at=None, current=None), done)
    pin, failure = await prepare_feedback(task, resume_run_id=run_id)
    assert failure is None
    assert (pin.version_id, pin.freshness) == ("fresh", "fresh_scan")
    assert source.refresh.await_args.kwargs["resume_run_id"] == run_id
