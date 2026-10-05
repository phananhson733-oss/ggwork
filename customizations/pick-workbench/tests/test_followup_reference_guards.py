"""Negative follow-up coverage uses a synthetic owned catalog, never a live model."""

from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from test_result_notes import _import, drama


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,thread,error,reason", [("alice", "other-thread", ValueError, "当前对话"), ("bob", "thread1", LookupError, "不存在")])
async def test_a_borrowed_reference_cannot_query_or_prepare_a_selection(app_client, owner, thread, error, reason):
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.tools import prepare_selection_tool, query_candidates_tool

    _, service = app_client
    await _import(service, [drama(1)])
    repo = PickRepository(service.session_factory, "alice")
    parent = await SelectionService(repo).query({}, thread_id="thread1", run_id="parent-run", call_id="parent-call")
    item_id = parent["items"][0]["item_id"]
    before = await repo.result(parent["id"])

    for tool, args in (
        (query_candidates_tool, {"filters": {}}),
        (prepare_selection_tool, {"result_id": parent["id"], "item_ids": [item_id], "note": "synthetic"}),
    ):
        store = ExtensionData("guard-task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("guard-task", "guard-run", thread, "lead"))
        runtime = SimpleNamespace(
            context={"user_id": owner, "pick_reference": {"result_id": parent["id"], "item_ids": [item_id]}, EXTENSION_TASK_STORE_KEY: store},
            tool_call_id="guard-call",
        )
        with pytest.raises(error, match=reason):
            await tool.coroutine(**args, runtime=runtime)
        task = task_from_runtime(runtime)
        assert not task.initialized and not task.known_titles and task.reference_id is None

    assert await repo.result(parent["id"]) == before
    assert await repo.results("thread1") == [before]
    assert await repo.selections() == []
    assert await PickRepository(service.session_factory, "bob").selections() == []
    assert await PickRepository(service.session_factory, "bob").results("thread1") == []
    assert await repo.results("other-thread") == []
