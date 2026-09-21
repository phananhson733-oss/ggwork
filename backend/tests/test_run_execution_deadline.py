"""Exercise the worker boundary, including waits outside agent middleware."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext, run_agent


def bridge():
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


@pytest.mark.asyncio
async def test_deadline_covers_preflight_wait_and_emits_terminal_receipt():
    manager = RunManager()
    record = await manager.create("deadline-preflight")
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    manager.wait_for_prior_finalizing = blocked
    factory = Mock()
    events = MemoryRunEventStore()
    stream = bridge()
    await asyncio.wait_for(run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=events, execution_timeout_seconds=0.1), agent_factory=factory, graph_input={}, config={}), 3)
    assert entered.is_set() and cancelled.is_set()
    factory.assert_not_called()
    assert record.status == RunStatus.timeout
    assert record.stop_reason == "execution_timeout"
    stream.publish_end.assert_awaited_once()
    delivery = [event for event in await events.list_events(record.thread_id, record.run_id) if event["event_type"] == "run.delivery"]
    assert len(delivery) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("user_cancel", [False, True])
async def test_deadline_cancels_inflight_execution_and_preserves_user_stop(user_cancel):
    manager = RunManager()
    record = await manager.create("deadline-model")
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingAgent:
        async def astream(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield {}
            finally:
                cancelled.set()

    stream = bridge()
    task = asyncio.create_task(run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, execution_timeout_seconds=0.5), agent_factory=lambda **kwargs: WaitingAgent(), graph_input={}, config={}))
    record.task = task
    await asyncio.wait_for(entered.wait(), 2)
    if user_cancel:
        await manager.cancel(record.run_id)
    await asyncio.wait_for(task, 3)
    assert cancelled.is_set()
    assert record.status == (RunStatus.interrupted if user_cancel else RunStatus.timeout)
    stream.publish_end.assert_awaited_once()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_execution_timeout_rejects_invalid_operator_values(value):
    with pytest.raises(ValueError):
        RunContext(checkpointer=None, execution_timeout_seconds=value)


def test_gateway_binds_deadline_from_operator_environment(monkeypatch):
    from app.gateway import deps

    for name in ("get_checkpointer", "get_store", "get_run_event_store", "get_thread_store"):
        monkeypatch.setattr(deps, name, lambda request: None)
    monkeypatch.setattr(deps, "get_config", lambda: None)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.delenv("PICK_RUN_TIMEOUT_SECONDS", raising=False)
    assert deps.get_run_context(request).execution_timeout_seconds is None
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "120")
    assert deps.get_run_context(request).execution_timeout_seconds == 120
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "nan")
    with pytest.raises(ValueError):
        deps.get_run_context(request)


@pytest.mark.asyncio
async def test_successful_execution_can_finish_durable_writes_after_deadline():
    manager = RunManager()
    record = await manager.create("deadline-finalization")
    original = manager.set_status_if_not_cancelled
    crossed_deadline = asyncio.Event()

    async def slow_terminal_write(*args, **kwargs):
        asyncio.get_running_loop().call_later(0.3, crossed_deadline.set)
        await crossed_deadline.wait()
        return await original(*args, **kwargs)

    manager.set_status_if_not_cancelled = slow_terminal_write

    class DoneAgent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    await asyncio.wait_for(run_agent(bridge(), manager, record, ctx=RunContext(checkpointer=None, execution_timeout_seconds=0.2), agent_factory=lambda **kwargs: DoneAgent(), graph_input={}, config={}), 3)
    assert crossed_deadline.is_set()
    assert record.status == RunStatus.success


@pytest.mark.asyncio
async def test_finished_run_does_not_cancel_callers_later_work():
    manager = RunManager()
    record = await manager.create("deadline-finished")

    class DoneAgent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    await run_agent(bridge(), manager, record, ctx=RunContext(checkpointer=None, execution_timeout_seconds=0.2), agent_factory=lambda **kwargs: DoneAgent(), graph_input={}, config={})
    continued = asyncio.Event()
    asyncio.get_running_loop().call_later(0.3, continued.set)
    await asyncio.wait_for(continued.wait(), 2)
    assert record.status == RunStatus.success
