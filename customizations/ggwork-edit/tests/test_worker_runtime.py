import json

import httpx
import pytest

from ggwork_edit.worker.runtime import WorkerSession
from ggwork_edit.worker.storage import WorkerStore


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
