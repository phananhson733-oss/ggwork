"""Recovery and conflict contracts through real plan routes; no internal collaborator mocks."""

import asyncio

import pytest
from test_planning import draft_input, editable


@pytest.mark.asyncio
async def test_source_loss_does_not_destroy_frozen_rows_or_receipts(app_client):
    from sqlalchemy import delete

    from ggwork_pick.models import candidate_sets

    client, service = app_client
    body, card = await draft_input(service, count=1)
    headers = {"test-owner": "alice"}
    created = (await client.post("/api/pick/plans", headers=headers, json=body)).json()
    async with service.session_factory() as session, session.begin():
        await session.execute(delete(candidate_sets).where(candidate_sets.c.id == card["id"], candidate_sets.c.owner_id == "alice"))
    patch = {**editable(created), "request_id": "source-gone-edit", "expected_version": 1, "timezone_change": None}
    patch["rows"][0]["note"] = "Frozen evidence still editable"
    result = await client.patch("/api/pick/plans/" + created["id"], headers=headers, json=patch)
    assert result.status_code == 200, result.text
    assert result.json()["rows"][0]["source_pin"] == created["rows"][0]["source_pin"]
    assert result.json()["version"] == 2
    original = await client.post("/api/pick/plans", headers=headers, json=body)
    assert original.status_code == 200 and original.json() == created


@pytest.mark.asyncio
async def test_concurrent_patches_have_single_atomic_winner(app_client):
    client, service = app_client
    body, _ = await draft_input(service, count=2)
    headers = {"test-owner": "alice"}
    created = (await client.post("/api/pick/plans", headers=headers, json=body)).json()
    left = {**editable(created), "title": "Left", "request_id": "race-left", "expected_version": 1, "timezone_change": None}
    right = {**editable(created), "title": "Right", "request_id": "race-right", "expected_version": 1, "timezone_change": None}
    left["rows"] = [left["rows"][0]]
    right["rows"] = [right["rows"][1]]
    replies = await asyncio.gather(*(client.patch("/api/pick/plans/" + created["id"], headers=headers, json=payload) for payload in (left, right)))
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(r.json() for r in replies if r.status_code == 200)
    loser = next(r.json() for r in replies if r.status_code == 409)
    assert winner["version"] == 2 and loser["detail"]["current_version"] == 2
    current = await client.get("/api/pick/plans/" + created["id"], headers=headers)
    assert current.json() == winner
    assert len(current.json()["rows"]) == 1


@pytest.mark.asyncio
async def test_tombstone_cannot_be_rebound_but_same_source_can_return(app_client):
    client, service = app_client
    body, _ = await draft_input(service, count=2)
    h = {"test-owner": "alice"}
    original = (await client.post("/api/pick/plans", headers=h, json=body)).json()
    remove = {**editable(original), "rows": [body["rows"][1]], "request_id": "omit-first", "expected_version": 1, "timezone_change": None}
    removed = await client.patch("/api/pick/plans/" + original["id"], headers=h, json=remove)
    assert removed.status_code == 200
    rebound = {**remove, "rows": [{**body["rows"][1], "row_id": body["rows"][0]["row_id"]}], "request_id": "rebind-tombstone", "expected_version": 2}
    refused = await client.patch("/api/pick/plans/" + original["id"], headers=h, json=rebound)
    assert refused.status_code == 422
    assert (await client.get("/api/pick/plans/" + original["id"], headers=h)).json() == removed.json()
    restore = {**remove, "rows": body["rows"], "request_id": "restore-origin", "expected_version": 2}
    restored = await client.patch("/api/pick/plans/" + original["id"], headers=h, json=restore)
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["rows"] == original["rows"]


@pytest.mark.asyncio
async def test_lost_http_reply_after_real_commit_retries_one_plan(app_client):
    import httpx

    client, service = app_client
    body, _ = await draft_input(service, count=1)

    class LoseReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await client._transport.handle_async_request(request)
            await response.aread()
            raise httpx.ReadError("synthetic reply lost after actual server commit", request=request)

    async with httpx.AsyncClient(transport=LoseReply(), base_url="http://test") as lossy:
        with pytest.raises(httpx.ReadError):
            await lossy.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    retried = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    listed = (await client.get("/api/pick/plans", headers={"test-owner": "alice"})).json()
    assert retried.status_code == 200 and listed["total"] == 1
    assert listed["items"] == [retried.json()]


@pytest.mark.asyncio
async def test_empty_and_hundred_rows_pagination_and_storable_boundaries(app_client):
    client, service = app_client
    body, _ = await draft_input(service, count=1)
    h = {"test-owner": "alice"}
    empty = await client.post("/api/pick/plans", headers=h, json={**body, "request_id": "empty", "rows": []})
    assert empty.status_code == 200 and empty.json()["rows"] == []
    hundred = {**body, "rows": [{**body["rows"][0], "row_id": f"row-{i}"} for i in range(100)]}
    full = await client.post("/api/pick/plans", headers=h, json=hundred)
    assert full.status_code == 200 and len(full.json()["rows"]) == 100
    page = (await client.get("/api/pick/plans?limit=1", headers=h)).json()
    assert page["total"] == 2 and page["next_offset"] == 1 and page["items"][0]["id"] == full.json()["id"]
    tail = (await client.get("/api/pick/plans?limit=1&offset=1", headers=h)).json()
    assert tail["next_offset"] is None and tail["items"][0]["id"] == empty.json()["id"]
    for changed in (
        {"rows": hundred["rows"] + [hundred["rows"][0]]},
        {"title": "x" * 201},
        {"title": "bad\x00title"},
        {"owner_id": "bob"},
        {"rows": [{**body["rows"][0], "title": "invented enrichment"}]},
    ):
        rejected = await client.post("/api/pick/plans", headers=h, json={**body, "request_id": "invalid", **changed})
        assert rejected.status_code == 422
    assert (await client.get("/api/pick/plans", headers=h)).json()["total"] == 2


@pytest.mark.asyncio
async def test_saved_plan_and_original_command_survive_new_service_and_engine(pick_db_url, tmp_path):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.completion_contracts import PlanCreate, PlanUpdate
    from ggwork_pick.planning import PlanningService
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    first = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(first))
    body, _ = await draft_input(service, count=1)
    plans = PlanningService(PickRepository(service.session_factory, "alice"))
    original = await plans.create(PlanCreate.model_validate(body))
    changed = await plans.update(
        original["id"], PlanUpdate.model_validate({**editable(original), "request_id": "persist-update", "expected_version": 1, "title": "Persisted edit"})
    )
    await service.stop()
    await first.dispose()
    second = host_engine(pick_db_url)
    restarted = PickService(tmp_path / "files")
    try:
        await restarted.initialize(async_sessionmaker(second))
        restored = PlanningService(PickRepository(restarted.session_factory, "alice"))
        assert await restored.get(original["id"]) == changed
        assert await restored.create(PlanCreate.model_validate(body)) == original
        assert (await PlanningService(PickRepository(restarted.session_factory, "bob")).list())["items"] == []
    finally:
        await restarted.stop()
        await second.dispose()
