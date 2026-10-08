"""Execution previews/export receipts through real owner-scoped HTTP and databases."""

import pytest
from test_planning import draft_input


@pytest.mark.asyncio
async def test_incomplete_draft_preview_blocks_whole_export_and_preserves_draft(app_client):
    client, service = app_client
    body, _ = await draft_input(service)
    headers = {"test-owner": "alice"}
    plan = (await client.post("/api/pick/plans", headers=headers, json=body)).json()
    preview = await client.post(f"/api/pick/plans/{plan['id']}/preview", headers=headers, json={"request_id": "preview", "expected_version": 1})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["plan"] == plan and data["exportable"] is False
    assert len(data["checks"]) == 3
    assert all(check["status"] == "blocked" and check["blockers"] for check in data["checks"])
    exported = await client.post(
        f"/api/pick/plans/{plan['id']}/exports", headers=headers, json={"request_id": "export", "expected_version": 1, "preview_id": data["preview_id"]}
    )
    assert exported.status_code == 409 and exported.json()["detail"]["code"] == "export_blocked"
    assert (await client.get(f"/api/pick/plans/{plan['id']}", headers=headers)).json() == plan


async def ready_plan(client, service, *, title="=Synthetic", availability="active", permission="allowed"):
    import json

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(
        json.dumps(
            [
                {
                    "source": "synthetic",
                    "source_id": "export-1",
                    "title": title,
                    "language": "en",
                    "theater": "Example",
                    "availability": availability,
                    "channel_rules": {"youtube": permission},
                }
            ]
        ).encode(),
        "json",
    )
    card = await SelectionService(repo).query({"limit": 1}, thread_id="export-source", run_id="export-source", call_id="source")
    item = card["items"][0]
    body = {
        "request_id": "ready-plan",
        "title": "Ready",
        "timezone": "America/Chicago",
        "rows": [
            {
                "row_id": "row-a",
                "identity": item["identity"],
                "source_result_id": card["id"],
                "source_item_id": item["item_id"],
                "selection_id": None,
                "account": "  +account",
                "channel": "youtube",
                "local_time": "2026-11-01T01:30",
                "fold": 1,
                "copy_text": 'line one, "quote"\nline two',
                "note": "\t=note",
            }
        ],
    }
    response = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_ready_export_is_exact_csv_and_retry_remains_frozen_after_edit(app_client):
    import csv
    import hashlib
    import io

    from test_planning import editable

    client, service = app_client
    headers = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    command = {"request_id": "ready-preview", "expected_version": 1}
    preview = await client.post(path + "/preview", headers=headers, json=command)
    assert preview.status_code == 200, preview.text
    assert preview.json()["exportable"] is True
    assert (await client.post(path + "/preview", headers=headers, json=command)).json() == preview.json()
    body = {"request_id": "ready-export", "expected_version": 1, "preview_id": preview.json()["preview_id"]}
    exported = await client.post(path + "/exports", headers=headers, json=body)
    assert exported.status_code == 200, exported.text
    receipt = exported.json()
    download = await client.get("/api/pick/exports/" + receipt["id"], headers=headers)
    assert download.status_code == 200 and download.headers["content-type"].startswith("text/csv")
    assert download.content.startswith(b"\xef\xbb\xbf")
    assert hashlib.sha256(download.content).hexdigest() == receipt["sha256"]
    rows = list(csv.reader(io.StringIO(download.content.decode("utf-8-sig"))))
    assert rows[0] == (
        "plan_id,plan_version,row_id,identity,source_result_id,source_item_id,title,theater,language,"
        "account,channel,local_time,timezone,scheduled_at,copy_text,note"
    ).split(",")
    assert rows[1] == [
        plan["id"],
        "1",
        "row-a",
        plan["rows"][0]["identity"],
        plan["rows"][0]["source_result_id"],
        plan["rows"][0]["source_item_id"],
        "'=Synthetic",
        "Example",
        "en",
        "'+account",
        "youtube",
        "2026-11-01T01:30",
        "America/Chicago",
        "2026-11-01T07:30:00.000000+00:00",
        'line one, "quote"\nline two',
        "'=note",
    ]
    patch = {**editable(plan), "request_id": "edit-after-export", "expected_version": 1, "timezone_change": None, "title": "Changed"}
    assert (await client.patch(path, headers=headers, json=patch)).status_code == 200
    assert (await client.post(path + "/exports", headers=headers, json=body)).json() == receipt
    assert (await client.get("/api/pick/exports/" + receipt["id"], headers=headers)).content == download.content
    assert (await client.get("/api/pick/exports/" + receipt["id"], headers={"test-owner": "bob"})).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["denied", "delisted", "unknown", "missing", "new_allowed_pin"])
async def test_export_rechecks_current_source_and_never_turns_old_preview_into_authority(app_client, change):
    import json

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    headers = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    preview = (await client.post(path + "/preview", headers=headers, json={"request_id": "preview", "expected_version": 1})).json()
    row = {
        "source": "synthetic",
        "source_id": "export-1",
        "title": "Changed source",
        "language": "en",
        "theater": "Example",
        "availability": "active",
        "channel_rules": {"youtube": "allowed"},
    }
    if change in {"delisted", "unknown"}:
        row["availability"] = change
    elif change == "denied":
        row["channel_rules"]["youtube"] = "denied"
    elif change == "missing":
        row["source_id"] = "other"
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(json.dumps([row]).encode(), "json")
    response = await client.post(path + "/exports", headers=headers, json={"request_id": "export", "expected_version": 1, "preview_id": preview["preview_id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == ("version_conflict" if change == "new_allowed_pin" else "export_blocked")
    assert (await client.get(path, headers=headers)).json() == plan
    fresh = (await client.post(path + "/preview", headers=headers, json={"request_id": "fresh-preview", "expected_version": 1})).json()
    assert fresh["exportable"] == (change == "new_allowed_pin")
    assert fresh["plan"]["rows"][0]["source_pin"] == plan["rows"][0]["source_pin"]
    assert fresh["checks"][0]["current_pin"] != plan["rows"][0]["source_pin"]


@pytest.mark.asyncio
async def test_export_commands_owner_binding_empty_plan_and_exact_retry(app_client):
    import asyncio

    client, service = app_client
    headers = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    path = f"/api/pick/plans/{plan['id']}"
    request = {"request_id": "preview", "expected_version": 1}
    assert (await client.post(path + "/preview", json=request)).status_code == 401
    assert (await client.post(path + "/preview", headers={"test-owner": "bob"}, json=request)).status_code == 404
    previews = await asyncio.gather(*(client.post(path + "/preview", headers=headers, json=request) for _ in range(2)))
    assert previews[0].json() == previews[1].json()
    body = {"request_id": "export", "expected_version": 1, "preview_id": previews[0].json()["preview_id"]}
    exports = await asyncio.gather(*(client.post(path + "/exports", headers=headers, json=body) for _ in range(2)))
    assert exports[0].status_code == exports[1].status_code == 200
    assert exports[0].json() == exports[1].json()
    assert (await client.post(path + "/exports", headers=headers, json={**body, "preview_id": "another"})).status_code == 409
    empty = (await client.post("/api/pick/plans", headers=headers, json={"request_id": "empty", "title": "Empty", "timezone": "UTC", "rows": []})).json()
    ep = await client.post(f"/api/pick/plans/{empty['id']}/preview", headers=headers, json={"request_id": "empty-preview", "expected_version": 1})
    assert ep.json()["exportable"] is False and ep.json()["checks"] == []
    wrong = await client.post(f"/api/pick/plans/{empty['id']}/exports", headers=headers, json={**body, "request_id": "wrong-plan"})
    assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_same_account_identity_duplicates_block_but_source_age_only_warns(app_client):
    from sqlalchemy import update
    from test_planning import editable

    from ggwork_pick.models import import_batches

    client, service = app_client
    h = {"test-owner": "alice"}
    plan = await ready_plan(client, service)
    async with service.session_factory() as session, session.begin():
        await session.execute(
            update(import_batches).where(import_batches.c.id == plan["rows"][0]["source_pin"]["catalog_batch_id"]).values(source_as_of="2020-01-01T00:00:00Z")
        )
    path = f"/api/pick/plans/{plan['id']}"
    old = (await client.post(path + "/preview", headers=h, json={"request_id": "old-data", "expected_version": 1})).json()
    assert old["exportable"] is True and old["checks"][0]["warnings"]
    patch = {**editable(plan), "request_id": "duplicate", "expected_version": 1}
    patch["rows"].append({**patch["rows"][0], "row_id": "row-b", "local_time": "2026-11-02T09:00", "fold": None})
    assert (await client.patch(path, headers=h, json=patch)).status_code == 200
    duplicate = (await client.post(path + "/preview", headers=h, json={"request_id": "duplicate-check", "expected_version": 2})).json()
    assert duplicate["exportable"] is False
    assert all(any("重复" in reason for reason in check["blockers"]) for check in duplicate["checks"])
