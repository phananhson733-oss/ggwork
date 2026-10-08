"""Plan drafts through the authenticated public API on SQLite and disposable PostgreSQL."""

import json

import pytest


async def draft_input(service, *, owner="alice", request_id="create-plan", count=3):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    repo = PickRepository(service.session_factory, owner)
    await Importer(repo, service.data_dir).catalog(
        json.dumps(
            [{"source": "synthetic", "source_id": str(i), "title": f"Synthetic {i}", "language": "en", "theater": "Example"} for i in range(count)]
        ).encode(),
        "json",
    )
    card = await SelectionService(repo).query({"limit": count}, thread_id="plan-source", run_id=request_id, call_id="source")
    body = {
        "request_id": request_id,
        "title": "Next week",
        "timezone": "America/Chicago",
        "rows": [
            {
                "row_id": f"row-{i}",
                "identity": item["identity"],
                "source_result_id": card["id"],
                "source_item_id": item["item_id"],
                "selection_id": None,
                "account": None,
                "channel": None,
                "local_time": None,
                "fold": None,
                "copy_text": "",
                "note": "",
            }
            for i, item in enumerate(card["items"])
        ],
    }
    return body, card


@pytest.mark.asyncio
async def test_create_draft_from_real_owned_candidate_then_read_and_list(app_client):
    from ggwork_pick.completion_contracts import Plan

    client, service = app_client
    body, card = await draft_input(service)
    reply = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    assert reply.status_code == 200, reply.text
    plan = Plan.model_validate(reply.json())
    assert plan.version == 1 and len(plan.rows) == 3
    assert [row.row_id for row in plan.rows] == ["row-0", "row-1", "row-2"]
    assert [row.title for row in plan.rows] == [item["title"] for item in card["items"]]
    assert all(row.scheduled_at is None and row.source_pin.catalog_batch_id == card["catalog_batch_id"] for row in plan.rows)
    assert "owner_id" not in reply.json()
    fetched = await client.get("/api/pick/plans/" + plan.id, headers={"test-owner": "alice"})
    assert fetched.json() == reply.json()
    listed = await client.get("/api/pick/plans?offset=0&limit=20", headers={"test-owner": "alice"})
    assert listed.json() == {"items": [reply.json()], "total": 1, "next_offset": None}


@pytest.mark.asyncio
async def test_plan_create_retry_returns_original_receipt_and_changed_body_conflicts(app_client):
    import asyncio

    client, service = app_client
    body, _ = await draft_input(service)
    headers = {"test-owner": "alice"}
    first, duplicate = await asyncio.gather(
        client.post("/api/pick/plans", headers=headers, json=body), client.post("/api/pick/plans", headers=headers, json=body)
    )
    assert first.status_code == duplicate.status_code == 200
    assert first.json() == duplicate.json()
    assert (await client.get("/api/pick/plans", headers=headers)).json()["total"] == 1
    changed = await client.post("/api/pick/plans", headers=headers, json={**body, "title": "Changed"})
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "version_conflict"


@pytest.mark.asyncio
async def test_plan_requires_owner_and_exact_source_identity(app_client):
    client, service = app_client
    body, _ = await draft_input(service)
    assert (await client.post("/api/pick/plans", json=body)).status_code == 401
    assert (await client.post("/api/pick/plans", headers={"test-owner": "default"}, json=body)).status_code == 401
    assert (await client.post("/api/pick/plans", headers={"test-owner": "bob"}, json=body)).status_code == 404
    bad = {**body, "rows": [{**body["rows"][0], "identity": "invented"}]}
    assert (await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=bad)).status_code == 422
    assert (await client.get("/api/pick/plans", headers={"test-owner": "alice"})).json()["total"] == 0
    created = (await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)).json()
    assert (await client.get("/api/pick/plans/" + created["id"], headers={"test-owner": "bob"})).status_code == 404
    assert (await client.get("/api/pick/plans", headers={"test-owner": "bob"})).json()["total"] == 0


def editable(plan):
    from ggwork_pick.completion_contracts import PlanRowInput

    return {"title": plan["title"], "timezone": plan["timezone"], "rows": [{key: row[key] for key in PlanRowInput.model_fields} for row in plan["rows"]]}


@pytest.mark.asyncio
async def test_replace_plan_rows_preserves_sources_version_and_original_retry_receipt(app_client):
    client, service = app_client
    body, _ = await draft_input(service)
    headers = {"test-owner": "alice"}
    created = (await client.post("/api/pick/plans", headers=headers, json=body)).json()
    patch = {**editable(created), "request_id": "edit-1", "expected_version": 1, "timezone_change": None}
    patch["rows"] = [{**patch["rows"][1], "note": "Keep this input"}, patch["rows"][0]]
    updated = await client.patch("/api/pick/plans/" + created["id"], headers=headers, json=patch)
    assert updated.status_code == 200, updated.text
    current = updated.json()
    assert current["version"] == 2 and [r["row_id"] for r in current["rows"]] == ["row-1", "row-0"]
    assert current["rows"][0]["source_pin"] == created["rows"][1]["source_pin"]
    assert current["rows"][0]["note"] == "Keep this input"
    repeated = await client.patch("/api/pick/plans/" + created["id"], headers=headers, json=patch)
    assert repeated.json() == current
    original_retry = await client.post("/api/pick/plans", headers=headers, json=body)
    assert original_retry.json() == created
    stale = await client.patch("/api/pick/plans/" + created["id"], headers=headers, json={**patch, "request_id": "stale", "title": "Do not overwrite"})
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    assert (await client.get("/api/pick/plans/" + created["id"], headers=headers)).json() == current


@pytest.mark.parametrize(
    "zone,local,fold,utc",
    [
        ("America/Chicago", "2026-01-15T09:00", None, "2026-01-15T15:00:00.000000+00:00"),
        ("America/New_York", "2026-11-01T01:30", 0, "2026-11-01T05:30:00.000000+00:00"),
        ("America/New_York", "2026-11-01T01:30", 1, "2026-11-01T06:30:00.000000+00:00"),
        ("Australia/Lord_Howe", "2026-04-05T01:45", 0, "2026-04-04T14:45:00.000000+00:00"),
        ("Australia/Lord_Howe", "2026-04-05T01:45", 1, "2026-04-04T15:15:00.000000+00:00"),
    ],
)
@pytest.mark.asyncio
async def test_plan_resolves_explicit_wall_time_and_fold_to_fixed_utc(app_client, zone, local, fold, utc):
    client, service = app_client
    body, _ = await draft_input(service, count=1)
    body["timezone"] = zone
    body["rows"][0].update(local_time=local, fold=fold)
    reply = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    assert reply.status_code == 200, reply.text
    assert reply.json()["rows"][0]["scheduled_at"] == utc
    assert reply.json()["rows"][0]["local_time"] == local


@pytest.mark.parametrize(
    "zone,local,fold",
    [
        ("America/New_York", "2026-03-08T02:30", None),
        ("America/New_York", "2026-11-01T01:30", None),
        ("America/Chicago", "2026-02-30T09:00", None),
        ("Not/A_Timezone", None, None),
        ("UTC", None, 1),
        ("Etc/GMT-14", "0001-01-01T00:00", None),
        ("Etc/GMT+12", "9999-12-31T23:59", None),
        ("posixrules", None, None),
    ],
)
@pytest.mark.asyncio
async def test_invalid_calendar_zone_or_ambiguous_time_does_not_create_plan(app_client, zone, local, fold):
    client, service = app_client
    body, _ = await draft_input(service, count=1)
    body["timezone"] = zone
    body["rows"][0].update(local_time=local, fold=fold)
    result = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    assert result.status_code == 422, result.text
    assert result.json()["detail"]["code"] == "invalid_query"
    assert (await client.get("/api/pick/plans", headers={"test-owner": "alice"})).json()["total"] == 0
