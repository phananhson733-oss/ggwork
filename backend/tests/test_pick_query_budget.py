"""ASGI request boundary: body/auth time, encode, disconnect and unchanged paths."""

import asyncio

import pytest


def scope(path="/api/pick/query", budget=b"40"):
    return {"type": "http", "asgi": {"version": "3.0"}, "path": path, "root_path": "", "method": "POST", "headers": [(b"x-pick-query-budget-ms", budget)], "state": {}}


@pytest.mark.asyncio
async def test_request_budget_cancels_wait_before_body_and_auth():
    from app.gateway.pick_query_budget import PickQueryBudget

    stopped = asyncio.Event()
    sent = []

    async def request_receive():
        await asyncio.Event().wait()

    async def downstream(scope, receive, send):
        try:
            await receive()
        finally:
            stopped.set()

    async def send(message):
        sent.append(message)

    await asyncio.wait_for(PickQueryBudget(downstream)(scope(), request_receive, send), 1)
    assert stopped.is_set()
    assert sent[0]["status"] == 504
    assert b"query_timeout" in sent[1]["body"]


@pytest.mark.asyncio
async def test_disconnect_cancels_active_request_and_cleans_up():
    from app.gateway.pick_query_budget import PickQueryBudget

    messages = asyncio.Queue()
    await messages.put({"type": "http.request", "body": b"{}"})
    entered, stopped = asyncio.Event(), asyncio.Event()
    sent = []

    async def downstream(scope, receive, send):
        await receive()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def send(message):
        sent.append(message)

    work = asyncio.create_task(PickQueryBudget(downstream)(scope(budget=b"10000"), messages.get, send))
    await entered.wait()
    await messages.put({"type": "http.disconnect"})
    await asyncio.wait_for(work, 1)
    assert stopped.is_set()
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [b"999999999", b"nan", b"0", b"-1", b"Infinity"])
async def test_untrusted_header_cannot_extend_ten_second_cap(budget):
    from app.gateway.pick_query_budget import PickQueryBudget

    captured = []

    async def downstream(scope, receive, send):
        captured.append(scope["state"]["pick_query_deadline"] - scope["state"]["pick_query_started"])

    async def receive():
        await asyncio.Event().wait()

    await PickQueryBudget(downstream)(scope(budget=budget), receive, None)
    assert 0 < captured[0] <= 10


@pytest.mark.asyncio
async def test_other_routes_keep_their_own_receive_and_scope():
    from app.gateway.pick_query_budget import PickQueryBudget

    request_scope = scope("/api/threads/a/runs/stream")

    async def receive():
        raise AssertionError("ordinary path middleware must not consume body")

    async def downstream(scope, received, send):
        assert scope is request_scope and received is receive
        assert not scope["state"]

    await PickQueryBudget(downstream)(request_scope, receive, None)
