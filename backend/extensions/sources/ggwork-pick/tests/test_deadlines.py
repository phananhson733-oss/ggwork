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


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_ms", [100, 10000])
async def test_agent_query_budget_includes_setup_and_rejects_late_success(monkeypatch, budget_ms):
    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickToolGate

    task = PickTask(None, TaskInfo("t", "r", "c", "lead"), budget=120)
    store = ExtensionData("t")
    store.set(task)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}),
        tool_call={"name": "pick_query_data", "args": {"query": {"domain": "catalog", "budget_ms": budget_ms}}},
    )
    loop = asyncio.get_running_loop()
    real_time = loop.time
    offset = [0]
    monkeypatch.setattr(loop, "time", lambda: real_time() + offset[0])
    started = loop.time()

    async def setup_and_encode(_):
        assert task.query_deadline <= started + budget_ms / 1000 + 0.01
        offset[0] = budget_ms / 1000 + 1
        return "late success"

    try:
        with pytest.raises(TimeoutError):
            await PickToolGate().awrap_tool_call(request, setup_and_encode)
    finally:
        offset[0] = 0
    # A subsequent call receives a fresh per-call budget, never a stale ContextVar.
    assert task.query_deadline - asyncio.get_running_loop().time() == pytest.approx(task.ordinary_remaining(), abs=0.01)


@pytest.mark.asyncio
async def test_short_query_budget_expires_waiting_for_tool_execution_lock():
    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickToolGate

    task = PickTask(None, TaskInfo("t", "r", "c", "lead"))
    store = ExtensionData("t")
    store.set(task)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}),
        tool_call={"name": "pick_query_data", "args": {"query": {"domain": "catalog", "budget_ms": 30}}},
    )
    entered = []

    async def work(_):
        entered.append(True)

    await task.execution_lock.acquire()
    waiting = asyncio.create_task(PickToolGate().awrap_tool_call(request, work))
    try:
        await asyncio.sleep(0.08)
        assert waiting.done()
        with pytest.raises(TimeoutError):
            await waiting
        assert entered == []
    finally:
        task.execution_lock.release()
        if not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lock", "cancel", "late_encode"])
async def test_gate_query_failure_invalidates_current_claims_but_keeps_historical_receipts(monkeypatch, failure):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.context import PickTask
    from ggwork_pick.middleware import PickToolGate

    task = PickTask(None, TaskInfo("t", "r", "c", "lead"))
    payload = {"counts": {"matched": 4}, "request": {"domain": "catalog"}, "pin": {"catalog_batch_id": "b", "mirror_version": 1}, "rows": []}
    if failure != "late_encode":
        task.answer_evidence.capture("pick_query_data", "old", payload)
    store = ExtensionData("t")
    store.set(task)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}),
        tool_call={"id": "new", "name": "pick_query_data", "args": {"query": {"domain": "catalog", "budget_ms": 30}}},
    )
    loop = asyncio.get_running_loop()
    real_time = loop.time
    offset = [0]
    monkeypatch.setattr(loop, "time", lambda: real_time() + offset[0])
    entered = asyncio.Event()

    async def work(_):
        entered.set()
        if failure == "cancel":
            await asyncio.Event().wait()
        task.answer_evidence.capture("pick_query_data", "new", {**payload, "counts": {"matched": 5}})
        offset[0] = 1
        return "late encoded success"

    if failure == "lock":
        await task.execution_lock.acquire()
    pending = asyncio.create_task(PickToolGate().awrap_tool_call(request, work))
    try:
        if failure == "cancel":
            await entered.wait()
            pending.cancel()
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else TimeoutError):
            await pending
    finally:
        offset[0] = 0
        if failure == "lock":
            task.execution_lock.release()

    def check(text):
        return build_checked_publication(text, evidence=task.answer_evidence, thread_id="c", run_id="r", message_id="m")

    assert check(f"本次查询符合条件总数为{5 if failure == 'late_encode' else 4}部。").status == "incomplete"
    assert check("本次镜像版本为1。").status == "incomplete"
    if failure != "late_encode":
        assert check("本次查询符合条件总数为4部 [tool:old]。").status == "confirmed"
