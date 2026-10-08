"""The event-loop epoch is independent from the host/thread monotonic epoch."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

from ggwork_pick.context import PickTask
from ggwork_pick.middleware import PickToolGate


class OffsetLoop(asyncio.SelectorEventLoop):
    def __init__(self, offset):
        super().__init__()
        self.offset = offset

    def time(self):
        return super().time() + self.offset


def request_for(task, name="pick_query_data", budget_ms=1000):
    store = ExtensionData("task")
    store.set(task)
    return SimpleNamespace(
        runtime=SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}),
        tool_call={"id": "clock-call", "name": name, "args": {"query": {"domain": "catalog", "budget_ms": budget_ms}}},
    )


@pytest.mark.parametrize("offset", [-5554.5, 0, 5554.5])
def test_fresh_query_preserves_one_loop_deadline_with_unrelated_epoch(offset):
    async def run():
        task = PickTask(None, TaskInfo("task", "run", "thread", "lead"), budget=60)
        loop = asyncio.get_running_loop()
        started = loop.time()
        seen = []

        async def handler(_):
            seen.append(task.query_deadline)
            await asyncio.sleep(0.01)
            seen.append(task.query_deadline)
            return "query completed"

        assert await PickToolGate().awrap_tool_call(request_for(task), handler) == "query completed"
        assert seen[0] == seen[1]
        assert 0 < seen[0] - started <= 1.01
        assert 39 < task.ordinary_deadline - time.monotonic() <= 40
        assert 39 < task.query_deadline - loop.time() <= 40  # invocation context was reset

    with asyncio.Runner(loop_factory=lambda: OffsetLoop(offset)) as runner:
        runner.run(run())


@pytest.mark.parametrize("offset", [-5554.5, 5554.5])
def test_ordinary_tool_stops_at_reserve_in_both_epoch_directions(offset):
    async def run():
        task = PickTask(None, TaskInfo("task", "run", "thread", "lead"), budget=20.05)
        entered, stopped = asyncio.Event(), asyncio.Event()
        started = time.monotonic()

        async def handler(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(PickToolGate().awrap_tool_call(request_for(task, "pick_prepare_selection"), handler), 0.5)
        assert entered.is_set() and stopped.is_set()
        assert time.monotonic() - started < 0.3
        assert 19.7 < task.remaining() <= 20

    with asyncio.Runner(loop_factory=lambda: OffsetLoop(offset)) as runner:
        runner.run(run())


@pytest.mark.parametrize("parent_seconds", [15, 60, 200])
def test_conversion_keeps_trusted_parent_total_and_never_renews_budget(monkeypatch, parent_seconds):
    from deerflow_extension_api.pick_publication import PickPublication

    from ggwork_pick.context import PickLifecycle

    logical = [100.0]
    monkeypatch.setattr("ggwork_pick.context.time", SimpleNamespace(monotonic=lambda: logical[0]))
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "120")

    class FakeClockLoop(asyncio.SelectorEventLoop):
        def time(self):
            return logical[0] + 10000

    async def run():
        store = ExtensionData("task")
        store.set(PickPublication(thread_id="thread", run_id="run", deadline=100.0 + parent_seconds))
        await PickLifecycle(None).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
        task = store.get(PickTask)
        assert task.remaining() == min(parent_seconds, 120)
        if parent_seconds <= 20:
            with pytest.raises(TimeoutError):
                task.query_deadline
            return
        expected = 10140.0 if parent_seconds == 60 else 10200.0
        assert task.ordinary_loop_deadline == expected
        assert task.query_deadline == expected
        logical[0] += 5
        assert task.ordinary_loop_deadline == expected
        assert task.query_deadline == expected

    with asyncio.Runner(loop_factory=FakeClockLoop) as runner:
        runner.run(run())


@pytest.mark.asyncio
async def test_reserve_crossed_during_clock_conversion_records_failed_query(monkeypatch):
    from ggwork_pick.answer_check import build_checked_publication

    base = time.monotonic()
    reads = [0]

    def monotonic():
        reads[0] += 1
        return base if reads[0] == 1 else base + 0.02

    task = PickTask(None, TaskInfo("task", "run", "thread", "lead"), deadline=base + 20.01)
    task.answer_evidence.capture("pick_count_candidates", "old", {"total": 1})
    monkeypatch.setattr("ggwork_pick.context.time", SimpleNamespace(monotonic=monotonic))
    entered = []

    async def handler(_):
        entered.append(True)

    with pytest.raises(TimeoutError):
        await PickToolGate().awrap_tool_call(request_for(task, "pick_count_candidates"), handler)
    assert not entered
    checked = build_checked_publication("本次查询共1部", evidence=task.answer_evidence, thread_id="thread", run_id="run", message_id="m")
    assert checked.status == "incomplete"
