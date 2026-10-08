"""Explicit plural references resolve owner/thread/item authority before tool use."""

import json
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY


async def results(service, owner="alice", thread="thread"):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    repo = PickRepository(service.session_factory, owner)
    made = []
    for index, date in enumerate(["2026-08-01", "2026-09-01"]):
        await Importer(repo, service.data_dir).catalog(
            json.dumps([{"source": "synthetic", "source_id": "same", "language": "en", "title": "共同剧名", "listed_at": date}]).encode(), "json"
        )
        made.append(await SelectionService(repo).query({}, thread_id=thread, run_id=f"run-{index}", call_id=f"call-{index}"))
    return made


async def runtime_for(service, refs, *, owner="alice", thread="thread", single=None):
    from ggwork_pick.context import PickLifecycle

    store = ExtensionData("t")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("t", "r", thread, "lead"))
    context = {"user_id": owner, "pick_references": refs, EXTENSION_TASK_STORE_KEY: store}
    if single is not None:
        context["pick_reference"] = single
    return SimpleNamespace(context=context, tool_call_id="detail"), store


def envelope(records):
    return {"version": "pick-references-v1", "references": [{"result_id": r["id"], "item_ids": [r["items"][0]["item_id"]]} for r in records]}


@pytest.mark.asyncio
async def test_plural_tools_keep_each_frozen_result_and_require_exact_citations(app_client):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.context import PickTask
    from ggwork_pick.tools import get_drama_detail_tool

    _, service = app_client
    records = await results(service)
    runtime, store = await runtime_for(service, envelope(records))
    for i, record in enumerate(records):
        runtime.tool_call_id = f"detail-{i}"
        answer = json.loads(await get_drama_detail_tool.coroutine(result_id=record["id"], item_id=record["items"][0]["item_id"], runtime=runtime))
        assert answer.get("status") != "rejected", answer
    task = store.get(PickTask)
    assert task.reference_id is None
    assert task.catalog_id == records[1]["catalog_batch_id"]
    assert task.catalog_id != records[0]["catalog_batch_id"]
    claim = "《共同剧名》的上架日期为2026-08-01"

    def checked(text):
        return build_checked_publication(text, evidence=task.answer_evidence, thread_id="thread", run_id="r", message_id="m")

    assert checked(claim).status == "incomplete"
    ref = f"result:{records[0]['id']}:{records[0]['items'][0]['item_id']}"
    assert checked(f"{claim} [{ref}]").status == "confirmed"


@pytest.mark.asyncio
async def test_plural_base_and_save_are_explicit_and_bound_items_are_respected(app_client):
    from ggwork_pick.tools import prepare_selection_tool, query_candidates_tool

    _, service = app_client
    records = await results(service)
    runtime, _ = await runtime_for(service, envelope(records))
    ambiguous = json.loads(await query_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert ambiguous["status"] == "rejected"
    assert "明确" in ambiguous["notice"]
    ambiguous_save = json.loads(await prepare_selection_tool.coroutine(runtime=runtime))
    assert ambiguous_save["status"] == "rejected"
    explicit = json.loads(await prepare_selection_tool.coroutine(result_id=records[0]["id"], runtime=runtime))
    assert explicit.get("requires_confirmation") is True, explicit
    assert explicit["item_ids"] == [records[0]["items"][0]["item_id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["array", "version", "empty", "duplicate_result", "duplicate_item", "unknown_item", "foreign", "thread", "missing", "both"])
async def test_plural_refs_reject_invalid_or_unauthorized_input_without_existence_leak(app_client, bad):
    from ggwork_pick.context import PickTask

    _, service = app_client
    records = await results(service)
    refs = envelope(records)
    kwargs = {}
    if bad == "array":
        refs = refs["references"]
    elif bad == "version":
        refs["version"] = "old"
    elif bad == "empty":
        refs["references"][0]["item_ids"] = []
    elif bad == "duplicate_result":
        refs["references"][1] = refs["references"][0]
    elif bad == "duplicate_item":
        refs["references"][0]["item_ids"] *= 2
    elif bad == "unknown_item":
        refs["references"][0]["item_ids"] = ["missing"]
    elif bad == "foreign":
        kwargs["owner"] = "bob"
    elif bad == "thread":
        kwargs["thread"] = "other"
    elif bad == "missing":
        refs["references"][1]["result_id"] = "missing"
    elif bad == "both":
        kwargs["single"] = refs["references"][0]
    runtime, store = await runtime_for(service, refs, **kwargs)
    task = store.get(PickTask)
    with pytest.raises(ValueError, match="^候选引用无效或已不可用，请重新选择当前对话中的候选和条目$"):
        await task.repository(runtime)
    assert not task.initialized
    assert not task.references
    assert not task.reference_context


@pytest.mark.asyncio
async def test_checked_publication_records_only_validated_explicit_reference_groups(app_client):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.completion_contracts import ResultReference
    from ggwork_pick.context import PickTask

    _, service = app_client
    records = await results(service)
    runtime, store = await runtime_for(service, envelope(records))
    task = store.get(PickTask)
    await task.repository(runtime)
    refs = [ResultReference(result_id=result_id, item_ids=ids) for result_id, ids in task.references.items()]
    checked = build_checked_publication("未知事实", evidence=task.answer_evidence, thread_id="thread", run_id="r", message_id="m", references=refs)
    assert checked.status == "incomplete"
    assert checked.references == refs
