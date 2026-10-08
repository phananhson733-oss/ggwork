"""Actual private review HTTP contract on SQLite and disposable PostgreSQL."""

import pytest

from feedback.fakes import operating_rows, snapshot_from_rows
from ggwork_pick.feedback.repository import FeedbackRepository
from ggwork_pick.feedback.sync import FeedbackSyncService


async def published(service, source_rows=None, owner="alice"):
    repo = FeedbackRepository(service.session_factory, owner)
    run = await repo.claim("manual")
    return await repo.publish(run["id"], snapshot_from_rows(source_rows or operating_rows()))


@pytest.mark.asyncio
async def test_actual_posts_preserve_zero_unknown_window_and_owner(app_client):
    client, service = app_client
    headers = {"test-owner": "alice"}
    service.feedback = FeedbackSyncService(service.session_factory, owner_id="alice", enabled=True)
    version = await published(service)
    response = await client.get("/api/pick/feedback/posts", headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ok"
    assert result["feedback_version_id"] == version["id"]
    assert result["total"] == 3
    assert [post["views"] for post in result["items"]] == [150, None, 0]
    assert all(post["link"] is None and post["evidence_refs"] for post in result["items"])
    assert all(post["requested_observation_days"] == 7 and post["window_complete"] is False for post in result["items"])
    assert all(post["revenue"] == [] for post in result["items"])  # no drama/account revenue copied onto posts
    assert (await client.get("/api/pick/feedback/posts")).status_code == 401
    hidden = await client.get("/api/pick/feedback/posts", headers={"test-owner": "bob"})
    assert hidden.json()["status"] == "auth_required"
    assert hidden.json()["items"] == [] and hidden.json()["total"] is None
    service.feedback.enabled = False
    disabled = await client.get("/api/pick/feedback/posts", headers=headers)
    assert disabled.json()["status"] == "disabled" and disabled.json()["total"] is None


async def plan_and_version(client, service):
    from test_planning import draft_input

    service.feedback = FeedbackSyncService(service.session_factory, owner_id="alice", enabled=True)
    source = operating_rows()
    source["dramas"][0]["平台"] = ["Example"]
    version = await published(service, source)
    body, _ = await draft_input(service, count=2)
    response = await client.post("/api/pick/plans", headers={"test-owner": "alice"}, json=body)
    assert response.status_code == 200
    plan = response.json()
    command = dict(
        request_id="link1",
        plan_id=plan["id"],
        row_id=plan["rows"][0]["row_id"],
        expected_plan_version=plan["version"],
        feedback_version_id=version["id"],
        post_key="tiktok:post-0",
        confirmation="manual",
    )
    return source, plan, command


@pytest.mark.asyncio
async def test_explicit_link_is_idempotent_private_and_never_overwrites_conflict(app_client):
    import asyncio

    client, service = app_client
    _, plan, body = await plan_and_version(client, service)
    headers = {"test-owner": "alice"}
    assert (await client.post("/api/pick/feedback/plan-links", json=body)).status_code == 401
    responses = await asyncio.gather(*[client.post("/api/pick/feedback/plan-links", headers=headers, json=body) for _ in range(2)])
    assert all(reply.status_code == 200 for reply in responses), [r.text for r in responses]
    receipt = responses[0].json()
    assert receipt == responses[1].json()
    assert receipt["method"] == "manual" and receipt["status"] == "confirmed"
    assert receipt["feedback_version_id"] == body["feedback_version_id"] and receipt["evidence_refs"]
    duplicate = await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "same-target"})
    assert duplicate.json() == receipt
    conflict = await client.post(
        "/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "different", "row_id": plan["rows"][1]["row_id"]}
    )
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "link_conflict"
    key_conflict = await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "post_key": "tiktok:post-1"})
    assert key_conflict.status_code == 409
    listed = await client.get("/api/pick/feedback/plan-links", headers=headers, params={"plan_id": plan["id"]})
    assert listed.json() == {"items": [receipt]}
    review = (await client.get("/api/pick/feedback/posts", headers=headers)).json()
    assert review["items"][0]["link"] == receipt
    assert review["items"][1]["link"] is None
    forbidden = await client.get("/api/pick/feedback/plan-links", headers={"test-owner": "bob"}, params={"plan_id": plan["id"]})
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_source_and_plan_revisions_preserve_original_receipt_and_reconfirm(app_client):
    from test_planning import editable

    client, service = app_client
    source, plan, body = await plan_and_version(client, service)
    headers = {"test-owner": "alice"}
    receipt = (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json()
    source["observations"][1]["播放量"] = 200
    await published(service, source)
    metric = (await client.get("/api/pick/feedback/posts", headers=headers)).json()
    assert metric["items"][0]["link"] == receipt
    assert metric["items"][0]["views"] == 200
    historical = (await client.get("/api/pick/feedback/posts", headers=headers, params={"feedback_version_id": body["feedback_version_id"]})).json()
    assert historical["items"][0]["views"] == 150
    source["posts"][0]["账号"] = [{"id": "account-b"}]
    changed_version = await published(service, source)
    changed = (await client.get("/api/pick/feedback/posts", headers=headers)).json()
    assert changed["items"][0]["link"] == {**receipt, "status": "conflict"}
    stale = await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "stale-source"})
    assert stale.status_code == 409
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json() == receipt
    reconfirm = await client.post(
        "/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "current-source", "feedback_version_id": changed_version["id"]}
    )
    assert reconfirm.status_code == 200, reconfirm.text
    assert reconfirm.json()["feedback_version_id"] == changed_version["id"] and reconfirm.json()["id"] != receipt["id"]
    patch = {**editable(plan), "request_id": "plan-changed", "expected_version": 1, "timezone_change": None}
    patch["rows"][0]["note"] = "New revision"
    assert (await client.patch("/api/pick/plans/" + plan["id"], headers=headers, json=patch)).status_code == 200
    linked = (await client.get("/api/pick/feedback/plan-links", headers=headers, params={"plan_id": plan["id"]})).json()["items"][0]
    assert linked["status"] == "needs_review" and linked["plan_version"] == 1
    outdated = await client.post(
        "/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "stale-plan", "feedback_version_id": changed_version["id"]}
    )
    assert outdated.status_code == 409 and outdated.json()["detail"]["code"] == "version_conflict"
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json() == receipt


@pytest.mark.asyncio
async def test_observation_is_one_actual_snapshot_and_revenue_stays_at_source_grain(app_client):
    client, service = app_client
    service.feedback = FeedbackSyncService(service.session_factory, owner_id="alice", enabled=True)
    source = operating_rows()
    source["observations"][1].update({"快照日期": "2026-10-09T12:00:00+08:00", "点赞": 0, "评论": 2})
    source["observations"].append(
        {"record_id": "ob-later", "Post ID": "post-0", "关联发布记录": [{"id": "release-0"}], "快照日期": "2026-10-11T12:00:00+08:00", "播放量": 230}
    )
    source["posts"][0]["RS收益"] = "0"
    source["posts"].append({**source["posts"][0], "record_id": "release-duplicate", "RS收益": "4.20"})
    await published(service, source)
    result = (await client.get("/api/pick/feedback/posts", headers={"test-owner": "alice"})).json()
    item = result["items"][0]
    assert result["total"] == 3
    assert item["observation_days"] == 10 and item["views"] == 230
    assert item["likes"] is None and item["comments"] is None and item["window_complete"] is False
    assert [(r["amount"], r["currency"], r["attribution"], r["grain"]) for r in item["revenue"]] == [
        ("0", "UNKNOWN", "unmatched", "post"),
        ("4.20", "UNKNOWN", "unmatched", "post"),
    ]
    assert any("release-duplicate" in ref for ref in item["evidence_refs"])


@pytest.mark.asyncio
async def test_reidentified_release_and_ambiguous_source_revisions_never_reassign(app_client):
    client, service = app_client
    source, plan, body = await plan_and_version(client, service)
    headers = {"test-owner": "alice"}
    source["posts"][0]["账号"] = [{"id": "account-a"}, {"id": "account-b"}]
    first_version = await published(service, source)
    body["feedback_version_id"] = first_version["id"]
    receipt = (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json()
    source["posts"][0]["账号"] = [{"id": "account-a"}, {"id": "account-c"}]
    await published(service, source)
    link = (await client.get("/api/pick/feedback/plan-links", headers=headers, params={"plan_id": plan["id"]})).json()["items"][0]
    assert link["status"] != "confirmed"
    source["posts"][0]["Post ID"] = "new-post-id"
    source["posts"][0]["视频链接"] = "https://www.tiktok.com/@synthetic/video/new-post-id"
    version = await published(service, source)
    changed = await client.post(
        "/api/pick/feedback/plan-links",
        headers=headers,
        json={**body, "request_id": "reidentified", "feedback_version_id": version["id"], "post_key": "tiktok:new-post-id"},
    )
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "link_conflict"
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json() == receipt


@pytest.mark.asyncio
async def test_filters_pagination_invalid_dates_foreign_versions_and_missing_post(app_client):
    client, service = app_client
    _, plan, body = await plan_and_version(client, service)
    headers = {"test-owner": "alice"}
    page = (
        await client.get(
            "/api/pick/feedback/posts", headers=headers, params={"limit": 1, "offset": 1, "account_id": "account-a", "channel": "tiktok", "language": "en"}
        )
    ).json()
    assert page["total"] == 3 and page["next_offset"] == 2 and page["items"][0]["post_key"] == "tiktok:post-1"
    empty = (await client.get("/api/pick/feedback/posts", headers=headers, params={"account_id": "wrong-account"})).json()
    assert empty["total"] == 0 and empty["items"] == []
    assert (await client.get("/api/pick/feedback/posts", headers=headers, params={"published_from": "2026-02-30"})).status_code == 422
    assert (
        await client.get("/api/pick/feedback/posts", headers=headers, params={"published_from": "2026-10-03", "published_to": "2026-10-01"})
    ).status_code == 422
    foreign = await published(service, owner="bob")
    assert (await client.get("/api/pick/feedback/posts", headers=headers, params={"feedback_version_id": foreign["id"]})).status_code == 404
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "feedback_version_id": foreign["id"]})).status_code == 404
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "post_key": "tiktok:absent"})).status_code == 404
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "row_id": "absent"})).status_code == 404
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "confirmation": "verified_external_id"})).status_code == 422
    receipt = (await client.post("/api/pick/feedback/plan-links", headers=headers, json=body)).json()
    service.feedback.enabled = False
    audit = (await client.get("/api/pick/feedback/plan-links", headers=headers, params={"plan_id": plan["id"]})).json()
    assert audit["items"] == [{**receipt, "status": "needs_review"}]
    assert (await client.post("/api/pick/feedback/plan-links", headers=headers, json={**body, "request_id": "disabled"})).status_code == 409
