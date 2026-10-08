"""A feedback-required candidate becomes public only with its immutable evidence."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from sqlalchemy import func, select

from .fakes import operating_rows, snapshot_from_rows

RESULT_ID = "12345678123456781234567812345678"


@pytest_asyncio.fixture
async def atomic_candidate(app_client, monkeypatch):
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.feedback.sync import RefreshOutcome
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    pick = PickRepository(service.session_factory, "alice")
    await Importer(pick, service.data_dir).catalog(
        b'[{"source":"synthetic","source_id":"a","title":"Synthetic Wolf","language":"en","theater":"ReelShort"}]', "json"
    )
    repo = FeedbackRepository(service.session_factory, "alice")

    async def start(rows):
        snapshot = snapshot_from_rows(rows)
        run = await repo.claim("manual")
        version = await repo.publish(run["id"], snapshot)
        service.feedback = SimpleNamespace(
            enabled=True,
            owner_id="alice",
            repository=lambda owner: FeedbackRepository(service.session_factory, owner),
            refresh=AsyncMock(
                return_value=RefreshOutcome("ok", version_id=version["id"], scan_started_at="2026-10-07T12:00:00Z", verified_at="2026-10-07T12:00:08Z")
            ),
        )
        store = ExtensionData("task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
        runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call")
        return runtime, task_from_runtime(runtime)

    monkeypatch.setattr("ggwork_pick.selection.uuid4", lambda: UUID(RESULT_ID))
    yield SimpleNamespace(client=client, service=service, pick=pick, repo=repo, start=start)
    service.feedback = None


async def assert_unpublished(case, task):
    from ggwork_pick.models import candidate_sets, feedback_result_evidence

    assert task.produced_result_ids == set()
    assert await case.pick.results("thread") == []
    assert await case.pick.result_for_call("run", "call") is None
    async with case.service.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(candidate_sets)) == 0
        assert await session.scalar(select(func.count()).select_from(feedback_result_evidence)) == 0
    headers = {"test-owner": "alice"}
    listing = await case.client.get("/api/pick/results", params={"thread_id": "thread"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["results"] == []
    assert (await case.client.get(f"/api/pick/results/{RESULT_ID}", headers=headers)).status_code == 404
    # app_client reports a successful host run: it must still be impossible to save.
    response = await case.client.post(
        "/api/pick/selections", headers=headers, json={"request_id": "save-failed", "result_id": RESULT_ID, "item_ids": ["item-1"]}
    )
    assert response.status_code == 404
    assert await case.pick.selections() == []


@pytest.mark.asyncio
async def test_actual_feedback_normalization_failure_never_publishes_candidate(atomic_candidate):
    from ggwork_pick.tools import query_candidates_tool

    rows = operating_rows()
    rows["cps_auto"][0]["币种"] = ["CURRENCY-LONGER-THAN-TWELVE"]
    runtime, task = await atomic_candidate.start(rows)
    reply = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert reply["status"] == "rejected"
    assert "currency" in reply["notice"]
    assert task.plugin_read is True
    await assert_unpublished(atomic_candidate, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_feedback_freeze_failure_rolls_back_candidate_and_sidecar(atomic_candidate, monkeypatch, cancel):
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.tools import query_candidates_tool

    runtime, task = await atomic_candidate.start(operating_rows())
    original = FeedbackRepository.freeze_result
    reached = asyncio.Event()

    async def interrupted(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        reached.set()
        if cancel:
            await asyncio.Event().wait()
        raise ValueError("synthetic freeze failure after insert")

    monkeypatch.setattr(FeedbackRepository, "freeze_result", interrupted)
    query = asyncio.create_task(query_candidates_tool.coroutine(filters={}, runtime=runtime))
    if cancel:
        await asyncio.wait_for(reached.wait(), 10)
        # A different connection must see neither row while the freeze transaction waits.
        assert await atomic_candidate.pick.results("thread") == []
        query.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query
    else:
        assert json.loads(await query)["status"] == "rejected"
    await assert_unpublished(atomic_candidate, task)


@pytest.mark.asyncio
async def test_atomic_candidate_success_reuses_evidence_and_is_saveable(atomic_candidate, monkeypatch):
    from ggwork_pick.tools import query_candidates_tool

    case = atomic_candidate
    runtime, task = await case.start(operating_rows())
    first = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert first["feedback"]["status"] == "ok"
    assert task.produced_result_ids == {first["id"]}
    frozen = await case.repo.result_evidence(first["id"])
    assert frozen.model_dump(mode="json") == first["feedback"]

    def forbidden(*args, **kwargs):
        raise AssertionError("a repeated call must reuse frozen evidence")

    monkeypatch.setattr("ggwork_pick.feedback.runtime.drama_feedback", forbidden)
    assert json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime)) == first
    assert len(await case.pick.results("thread")) == 1
    conflict = json.loads(await query_candidates_tool.coroutine(filters={"limit": 2}, runtime=runtime))
    assert conflict["status"] == "rejected"
    response = await case.client.post(
        "/api/pick/selections",
        headers={"test-owner": "alice"},
        json={"request_id": "save-ok", "result_id": first["id"], "item_ids": [first["items"][0]["item_id"]]},
    )
    assert response.status_code == 200
    assert len(await case.pick.selections()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["status", "foreign_version", "foreign_item"])
async def test_atomic_freeze_preserves_validation_and_rolls_back(atomic_candidate, monkeypatch, invalid):
    from ggwork_pick.feedback import runtime as feedback_runtime
    from ggwork_pick.feedback.contracts import FeedbackItem, FeedbackReply
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.tools import query_candidates_tool

    case = atomic_candidate
    runtime, task = await case.start(operating_rows())
    foreign = FeedbackRepository(case.service.session_factory, "bob")
    run = await foreign.claim("manual")
    version = await foreign.publish(run["id"], snapshot_from_rows(operating_rows()))
    original = feedback_runtime.candidate_feedback

    async def invalid_reply(*args):
        reply = await original(*args)
        if invalid == "status":
            return FeedbackReply(status="unavailable")
        if invalid == "foreign_version":
            return reply.model_copy(update={"feedback_version_id": version["id"]})
        return reply.model_copy(update={"items": [FeedbackItem(key="foreign", evidence_kind="unknown")]})

    monkeypatch.setattr(feedback_runtime, "candidate_feedback", invalid_reply)
    assert json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))["status"] == "rejected"
    await assert_unpublished(case, task)


@pytest.mark.asyncio
async def test_concurrent_duplicate_publication_keeps_one_candidate_and_evidence(atomic_candidate, monkeypatch):
    from uuid import uuid4

    from ggwork_pick.feedback import runtime as feedback_runtime
    from ggwork_pick.models import feedback_result_evidence
    from ggwork_pick.tools import query_candidates_tool

    case = atomic_candidate
    runtime, _ = await case.start(operating_rows())
    monkeypatch.setattr("ggwork_pick.selection.uuid4", uuid4)
    original = feedback_runtime.candidate_feedback
    ready = asyncio.Event()
    prepared = []

    async def together(*args):
        reply = await original(*args)
        prepared.append(reply)
        if len(prepared) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 10)
        return reply

    monkeypatch.setattr(feedback_runtime, "candidate_feedback", together)
    answers = await asyncio.gather(*(query_candidates_tool.coroutine(filters={}, runtime=runtime) for _ in range(2)))
    assert json.loads(answers[0]) == json.loads(answers[1])
    assert len(await case.pick.results("thread")) == 1
    async with case.service.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(feedback_result_evidence)) == 1


@pytest.mark.asyncio
async def test_old_candidate_without_evidence_is_not_retrofitted_or_deleted(atomic_candidate):
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.tools import query_candidates_tool

    case = atomic_candidate
    runtime, task = await case.start(operating_rows())
    historical = await SelectionService(case.pick).query({}, thread_id="thread", run_id="run", call_id="call")
    original = await case.pick.result(historical["id"])
    reply = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert reply["status"] == "rejected"
    assert task.produced_result_ids == set()
    assert await case.pick.result(historical["id"]) == original
    assert await case.repo.result_evidence(historical["id"]) is None
