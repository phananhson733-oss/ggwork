import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from ggwork_edit.worker.runtime import WorkerSession
from ggwork_edit.worker.storage import WorkerError, WorkerStore


@pytest.mark.asyncio
async def test_claim_request_survives_unknown_network_outcome_and_stop_ack_is_real(tmp_path):
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="test",
        output_root=tmp_path / "output",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    requests = []
    attempt = {"id": "attempt-1", "fence": 1, "output_ids": ["out-1"]}
    task = {"id": "task-1", "status": "stopping", "attempt": attempt}
    first = True

    async def gateway(request):
        nonlocal first
        payload = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/claim"):
            requests.append(payload["request_id"])
            if first:
                first = False
                raise httpx.ReadTimeout("unknown outcome")
            return httpx.Response(200, json={"task": task, "attempt": attempt})
        if request.url.path.endswith("/report"):
            requests.append(payload["kind"])
            return httpx.Response(200, json={"task": {**task, "status": "stopped"}, "stop_requested": True, "fence": 2})
        return httpx.Response(200, json=task)

    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        session = WorkerSession(store, http)
        with pytest.raises(httpx.ReadTimeout):
            await session.claim()
        resumed = WorkerSession(WorkerStore(store.home), http)
        claimed = await resumed.claim()
        assert requests[0] == requests[1]
        await resumed.execute(claimed)
        assert "stopped" in requests
        assert not store.journal()


@pytest.mark.asyncio
async def test_restart_renews_lease_before_replaying_uncertain_output_receipt(tmp_path):
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="test",
        output_root=tmp_path / "output",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    attempt = {"id": "attempt-1", "fence": 1, "output_ids": ["out-1"]}
    pending = {"attempt_id": "attempt-1", "fence": 1, "event_id": "original-event", "kind": "complete"}
    store.save_journal({"task_id": "task-1", "attempt": attempt, "pending_report": pending})
    events = []
    task = {"id": "task-1", "status": "running", "attempt": attempt}

    async def gateway(request):
        body = json.loads(request.content)
        events.append(body)
        return httpx.Response(
            200, json={"task": {**task, "status": "completed" if body["kind"] == "complete" else "running"}, "stop_requested": False, "fence": 1}
        )

    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        await WorkerSession(store, http).execute(task)
    assert [e["kind"] for e in events] == ["heartbeat", "complete"]
    assert events[1] == pending
    assert store.journal() == {}


@pytest.mark.asyncio
async def test_owner_retry_after_lost_terminal_response_gets_fresh_claim(tmp_path):
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="test",
        output_root=tmp_path / "output",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    old = {"id": "old-attempt", "fence": 1, "output_ids": ["out-1"]}
    new = {"id": "new-attempt", "fence": 2, "output_ids": ["out-1"]}
    store.save_journal(
        {
            "claim_request_id": "old-claim",
            "task_id": "task-1",
            "attempt": old,
            "pending_report": {"attempt_id": "old-attempt", "fence": 1, "event_id": "lost-terminal", "kind": "stopped"},
        }
    )
    claims = []

    async def gateway(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "task-1", "status": "queued", "attempt": old})
        assert request.url.path.endswith("/claim"), "obsolete attempt must never report"
        claims.append(json.loads(request.content)["request_id"])
        return httpx.Response(200, json={"task": {"id": "task-1", "status": "running", "attempt": new}, "attempt": new})

    async with httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(gateway)) as http:
        task = await WorkerSession(store, http).claim()
    assert task["attempt"]["id"] == "new-attempt"
    assert len(claims) == 1 and claims[0] != "old-claim"
    assert "pending_report" not in store.journal()


@asynccontextmanager
async def real_gateway(callback):
    """Actual TCP/HTTP boundary; callbacks are the external Gateway test service."""

    async def handle(reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            method, path, _ = head.splitlines()[0].split()
            size = next((int(line.split(":", 1)[1]) for line in head.splitlines() if line.lower().startswith("content-length:")), 0)
            body = json.loads(await reader.readexactly(size)) if size else {}
            result = await callback(method, path, body)
            if result is None:
                return  # Deliberately drop the response after receiving a request.
            status, value = result
            encoded = json.dumps(value).encode()
            writer.write(
                f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                + encoded
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_real_http_planner_failure_is_once_per_attempt_and_manual_retry_is_new(tmp_path):
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="test",
        output_root=tmp_path / "output",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    task = {
        "id": "task-1",
        "status": "running",
        "plan": None,
        "source_manifest": {},
        "requirements": {},
        "attempt": {"id": "attempt-1", "fence": 1, "output_ids": ["out-1"]},
    }
    store.save_journal({"task_id": task["id"], "attempt": task["attempt"]})
    calls = []

    async def gateway(method, path, body):
        status = 200
        value = task
        if path.endswith("/plan"):
            calls.append(body["attempt_id"])
            status = 502
            value = {"detail": {"code": "planner_unavailable", "retry": "explicit"}}
        elif path.endswith("/report"):
            if body["kind"] == "failure":
                task["status"] = "failed"
            value = {"task": task, "stop_requested": False, "fence": task["attempt"]["fence"]}
        elif path.endswith("/retry"):
            task.update(status="queued")
        elif path.endswith("/claim"):
            if task["status"] == "queued":
                task.update(status="running", attempt={"id": "attempt-2", "fence": 2, "output_ids": ["out-1"]})
                value = {"task": task, "attempt": task["attempt"]}
            else:
                value = {"task": None, "attempt": None}
        return status, value

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        session = WorkerSession(store, http)
        with pytest.raises(WorkerError, match="planner_unavailable"):
            await session.request_plan(task["id"], task["attempt"], [])
        # Process restart must not call a definite failed provider again.
        restarted = WorkerSession(store, http)
        with pytest.raises(WorkerError, match="planner_unavailable"):
            await restarted.request_plan(task["id"], task["attempt"], [])
        await restarted.execute(dict(task))
        assert task["status"] == "failed"
        assert await restarted.claim() is None
        assert calls == ["attempt-1"]
        await http.post("/retry", json={})
        claimed = await restarted.claim()
        assert store.journal()["planner_submit_allowed"] is True
        with pytest.raises(WorkerError, match="planner_unavailable"):
            await restarted.request_plan(claimed["id"], claimed["attempt"], [])
        assert calls == ["attempt-1", "attempt-2"]


def pending_plan_store(tmp_path):
    store = WorkerStore(tmp_path / "state")
    store.setup(
        gateway="https://example.test",
        device_id="device-1",
        token="test",
        output_root=tmp_path / "output",
        model=tmp_path / "model",
        model_sha256="a" * 64,
        model_language="en",
    )
    task = {
        "id": "task-1",
        "status": "running",
        "plan": None,
        "source_manifest": {},
        "requirements": {},
        "attempt": {"id": "attempt-1", "fence": 1, "output_ids": ["out-1"]},
    }
    store.save_journal({"task_id": task["id"], "attempt": task["attempt"]})
    return store, task


@pytest.mark.asyncio
async def test_dropped_plan_response_recovers_stored_plan_without_second_model_call(tmp_path, monkeypatch):
    from ggwork_edit.worker import runtime

    monkeypatch.setattr(runtime, "POLL_SECONDS", 0.01)
    store, task = pending_plan_store(tmp_path)
    model_calls = []
    lookups = []

    async def gateway(method, path, body):
        if path.endswith("/plan"):
            model_calls.append(body["attempt_id"])
            return None
        lookups.append(path)
        if len(lookups) == 1:
            return 503, {"detail": "temporarily unavailable"}
        task["plan"] = {"profile": "hook", "aspect_ratio": "9:16", "language": "en", "outputs": []}
        return 200, task

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        recovered = await WorkerSession(store, http).request_plan(task["id"], task["attempt"], [])
        assert recovered["plan"] is not None
        restarted = WorkerSession(store, http)
        assert (await restarted.request_plan(task["id"], task["attempt"], []))["plan"] == recovered["plan"]
    assert model_calls == ["attempt-1"]
    assert len(lookups) == 3


@pytest.mark.asyncio
async def test_uncertain_plan_lookup_budget_survives_restart_then_reports_failure_once(tmp_path, monkeypatch):
    from ggwork_edit.worker import runtime

    monkeypatch.setattr(runtime, "POLL_SECONDS", 0.01)
    store, task = pending_plan_store(tmp_path)
    model_calls, lookups, failures = [], [], []

    async def gateway(method, path, body):
        if path.endswith("/plan"):
            model_calls.append(body["attempt_id"])
            return None
        if path.endswith("/report"):
            if body["kind"] == "failure":
                failures.append(body["error"])
                task["status"] = "failed"
            return 200, {"task": task, "stop_requested": False, "fence": 1}
        lookups.append(path)
        return 503, {"detail": "temporarily unavailable"}

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        with pytest.raises(WorkerError, match="planner_outcome_unknown"):
            await WorkerSession(store, http).request_plan(task["id"], task["attempt"], [])
        restarted = WorkerSession(store, http)
        with pytest.raises(WorkerError, match="planner_outcome_unknown"):
            await restarted.request_plan(task["id"], task["attempt"], [])
        await restarted.execute(task)
    assert model_calls == ["attempt-1"]
    assert len(lookups) == 3
    assert failures == ["planner_outcome_unknown"]
    assert not store.journal()


@pytest.mark.asyncio
@pytest.mark.parametrize("winning_state", ["plan", "failed", "stopping"])
async def test_definite_provider_error_honors_authoritative_plan_terminal_or_stop(tmp_path, winning_state):
    from ggwork_edit.worker.native import WorkerStopped

    store, task = pending_plan_store(tmp_path)
    model_calls = []

    async def gateway(method, path, body):
        if path.endswith("/plan"):
            model_calls.append(body["attempt_id"])
            if winning_state == "plan":
                task["plan"] = {"profile": "hook", "outputs": []}
            else:
                task["status"] = winning_state
            return 502, {"detail": {"code": "planner_unavailable", "retry": "explicit"}}
        assert method == "GET"
        return 200, task

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        session = WorkerSession(store, http)
        if winning_state == "stopping":
            with pytest.raises(WorkerStopped):
                await session.request_plan(task["id"], task["attempt"], [])
        else:
            current = await session.request_plan(task["id"], task["attempt"], [])
            assert current["plan"] is not None if winning_state == "plan" else current["status"] == "failed"
    assert model_calls == ["attempt-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["planner_outcome_unknown", "planner_unavailable"])
async def test_reconnected_worker_honors_plan_before_replaying_deferred_planner_failure(tmp_path, error_code):
    store, task = pending_plan_store(tmp_path)
    state = store.journal()
    state.update(
        planning_request={"lookups": 3, "error": error_code},
        pending_report={"attempt_id": "attempt-1", "fence": 1, "event_id": "pending-failure", "kind": "failure", "error": error_code},
    )
    store.save_journal(state)
    stale = dict(task)
    task.update(plan={"profile": "hook", "outputs": []}, plan_confirmed=True, outputs=[{"id": "out-1", "status": "completed"}])
    kinds = []

    async def gateway(method, path, body):
        assert path.endswith("/report")
        kinds.append(body["kind"])
        if body["kind"] == "complete":
            task["status"] = "completed"
        return 200, {"task": task, "stop_requested": False, "fence": 1}

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        await WorkerSession(store, http).execute(stale)
    assert kinds == ["heartbeat", "complete"]
    assert not store.journal()


@pytest.mark.asyncio
async def test_atomic_winning_plan_response_retains_attempt_for_rendering(tmp_path):
    store, task = pending_plan_store(tmp_path)
    state = store.journal()
    state["planning_request"] = {"lookups": 3, "error": "planner_outcome_unknown"}
    store.save_journal(state)
    failure_reports = []

    async def gateway(method, path, body):
        if path.endswith("/report"):
            if body["kind"] == "failure":
                # The server transaction observes a plan committed after the last
                # no-plan lookup and preserves that winner instead of failing it.
                failure_reports.append(body["error"])
                task.update(plan={"profile": "hook", "outputs": []}, plan_confirmed=True, outputs=[{"id": "out-1", "status": "completed"}])
            elif body["kind"] == "complete":
                task["status"] = "completed"
            return 200, {"task": task, "stop_requested": False, "fence": 1}
        assert method == "GET"
        return 200, task

    async with real_gateway(gateway) as origin, httpx.AsyncClient(base_url=origin, trust_env=False) as http:
        session = WorkerSession(store, http)
        await session.execute(task)
        assert store.journal()["attempt"]["id"] == "attempt-1"
        current = await session.claim()
        assert current["plan"] is not None
        await session.execute(current)
    assert failure_reports == ["planner_outcome_unknown"]
    assert task["status"] == "completed" and not store.journal()
