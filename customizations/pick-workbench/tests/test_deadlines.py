"""Ordinary work leaves the final part of the trusted run budget untouched."""

import asyncio
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY


@pytest.mark.asyncio
async def test_trusted_parent_reserve_never_resets_or_extends(monkeypatch):
    from deerflow_extension_api.pick_publication import PickPublication

    from ggwork_pick.context import PickLifecycle, PickTask

    clock = [100.0]
    monkeypatch.setattr("ggwork_pick.context.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "120")
    store = ExtensionData("t")
    store.set(PickPublication(thread_id="c", run_id="r", deadline=150.0))
    await PickLifecycle(None).on_task_start(ExtensionData("app"), store, TaskInfo("t", "r", "c", "lead"))
    task = store.get(PickTask)
    assert task.deadline == 150.0
    assert task.ordinary_remaining() == 30.0
    clock[0] = 131.0
    with pytest.raises(TimeoutError):
        task.ordinary_remaining()
    assert task.remaining() == 19.0
    clock[0] = 151.0
    with pytest.raises(TimeoutError):
        task.remaining()


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0.1, 10, 20])
async def test_short_run_never_starts_ordinary_tool(seconds):
    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickToolGate

    task = PickTask(None, TaskInfo("t", "r", "c", "lead"), budget=seconds)
    store = ExtensionData("t")
    store.set(task)
    request = SimpleNamespace(runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}), tool_call={"name": "pick_query_candidates"})
    entered = []

    async def work(_):
        entered.append(True)

    with pytest.raises(TimeoutError):
        await PickToolGate().awrap_tool_call(request, work)
    assert entered == []
    assert task.remaining() <= seconds


@pytest.mark.asyncio
async def test_inflight_ordinary_tool_is_cancelled_at_reserve():
    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickToolGate

    task = PickTask(None, TaskInfo("t", "r", "c", "lead"), budget=20.04)
    store = ExtensionData("t")
    store.set(task)
    request = SimpleNamespace(runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}), tool_call={"name": "pick_query_candidates"})
    stopped = asyncio.Event()

    async def work(_):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(PickToolGate().awrap_tool_call(request, work), 1)
    assert stopped.is_set()
    assert 19.7 < task.remaining() <= 20
