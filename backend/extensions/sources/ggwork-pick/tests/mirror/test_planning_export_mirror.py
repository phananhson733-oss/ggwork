"""Execution uses actual pinned raw mirror facts and canonical identities together."""

import json
from datetime import timedelta

import board_fixture as bf
import gate_world as gw
import pytest
import test_query_consistency

from ggwork_pick.completion_contracts import PlanCreate, PlanExportCommand, PlanVersionCommand
from ggwork_pick.planning import PlanningService
from ggwork_pick.planning_export import PlanningExportService
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.selection import SelectionService

canonical_world = test_query_consistency.canonical_world
common_board = test_query_consistency.common_board


async def source_plan(repo, keys, request_id):
    card = await SelectionService(repo).query({"limit": 20}, thread_id="plan-export", run_id=request_id, call_id="source")
    items = {json.loads(item["identity"])[1]: item for item in card["items"]}
    return await PlanningService(repo).create(
        PlanCreate(
            request_id=request_id,
            title="Mirrored execution",
            timezone="UTC",
            rows=[
                {
                    "row_id": f"row-{key}",
                    "identity": items[key]["identity"],
                    "source_result_id": card["id"],
                    "source_item_id": items[key]["item_id"],
                    "account": "Account A",
                    "channel": "youtube",
                    "local_time": "2026-10-15T09:00",
                }
                for key in keys
            ],
        )
    )


@pytest.mark.asyncio
async def test_mirror_batch_preserves_exact_identity_and_raw_delisting_overrides_import(canonical_world):
    query, repo, *_ = canonical_world
    plan = await source_plan(repo, ["c-1", "c-5", "c-4"], "raw-delisting")
    service = PlanningExportService(repo, query)
    preview = await service.preview(plan["id"], PlanVersionCommand(request_id="preview", expected_version=1))
    assert preview["exportable"] is False
    assert preview["checks"][0]["status"] == "ready"
    assert preview["checks"][1]["status"] == "ready"
    assert any("下架" in reason for reason in preview["checks"][2]["blockers"])
    with pytest.raises(QueryFailure) as refused:
        await service.export(plan["id"], PlanExportCommand(request_id="blocked-export", expected_version=1, preview_id=preview["preview_id"]))
    assert refused.value.code == "export_blocked"
    assert all(c["current_pin"]["mirror_version"] == 6 for c in preview["checks"])
    assert preview["plan"] == plan


@pytest.mark.asyncio
async def test_export_rechecks_published_mirror_rule_change_without_rewriting_source_pin(canonical_world):
    query, repo, world, conn, shared, importer, as_of = canonical_world
    plan = await source_plan(repo, ["c-1"], "rule-change")
    service = PlanningExportService(repo, query)
    preview = await service.preview(plan["id"], PlanVersionCommand(request_id="before", expected_version=1))
    assert preview["exportable"] is True
    raw = bf.board_world("Canonical B", "no", as_of + timedelta(days=1))
    changed = gw.with_v1(raw, world.v1_rows)
    with bf._reader_role_env(query.reader._dsn.split("://", 1)[1].split(":", 1)[0]):
        await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    with pytest.raises(QueryFailure) as rejected:
        await service.export(plan["id"], PlanExportCommand(request_id="after-change", expected_version=1, preview_id=preview["preview_id"]))
    assert rejected.value.code == "export_blocked"
    after = await service.preview(plan["id"], PlanVersionCommand(request_id="after", expected_version=1))
    assert any("不允许" in reason for reason in after["checks"][0]["blockers"])
    assert after["checks"][0]["current_pin"] != after["plan"]["rows"][0]["source_pin"]
    assert await PlanningService(repo).get(plan["id"]) == plan


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", ["unknown_language", "missing_raw_row", "missing_platform_rule", "reader_unavailable"])
async def test_required_mirror_source_gaps_block_without_imported_permission_fallback(canonical_world, gap):
    from ggwork_pick.query_service import CommonQueryService

    query, repo, world, conn, shared, importer, as_of = canonical_world
    plan = await source_plan(repo, ["c-1"], "source-gap")
    if gap == "reader_unavailable":
        query = CommonQueryService(repo)
    else:
        rows = []
        for row in world.tables["catalog_rows"]:
            if row["row_key"] == "c-1":
                if gap == "missing_raw_row":
                    continue
                row = {**row, **({"lang": " "} if gap == "unknown_language" else {"platform": "no-rule-source"})}
            rows.append(row)
        changed = gw.with_table(world, "catalog_rows", rows)
        with bf._reader_role_env(query.reader._dsn.split("://", 1)[1].split(":", 1)[0]):
            await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    preview = await PlanningExportService(repo, query).preview(plan["id"], PlanVersionCommand(request_id="gap-preview", expected_version=1))
    assert preview["exportable"] is False
    assert preview["checks"][0]["status"] == "blocked" and preview["checks"][0]["blockers"]
    assert preview["plan"] == plan


@pytest.mark.asyncio
async def test_actual_mirror_ready_plan_exports_immutable_current_checked_bytes(canonical_world):
    query, repo, *_ = canonical_world
    plan = await source_plan(repo, ["c-1"], "mirror-ready")
    service = PlanningExportService(repo, query)
    preview = await service.preview(plan["id"], PlanVersionCommand(request_id="preview", expected_version=1))
    receipt = await service.export(plan["id"], PlanExportCommand(request_id="export", expected_version=1, preview_id=preview["preview_id"]))
    saved, data = await service.download(receipt["id"])
    assert saved == receipt and data.startswith(b"\xef\xbb\xbf")
    assert '"[""synthetic"",""c-1"",""英语""]"'.encode() in data
    assert await PlanningService(repo).get(plan["id"]) == plan


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", [None, "warn", "unrecognized", "only"])
async def test_unknown_or_conditional_current_youtube_rule_never_inherits_positive_import(canonical_world, rule):
    query, repo, world, conn, shared, importer, as_of = canonical_world
    plan = await source_plan(repo, ["c-1"], "rule-value-gap")
    changed = gw.with_manifest(world, ("meta", "rules", "platformRules", "shortmax", "yt"), rule)
    if rule == "only":
        changed = gw.with_table(changed, "catalog_rows", [{**r, "youtube": False} if r["row_key"] == "c-1" else r for r in changed.tables["catalog_rows"]])
    with bf._reader_role_env(query.reader._dsn.split("://", 1)[1].split(":", 1)[0]):
        await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    service = PlanningExportService(repo, query)
    preview = await service.preview(plan["id"], PlanVersionCommand(request_id="rule-preview", expected_version=1))
    assert preview["exportable"] is False and preview["checks"][0]["blockers"]
    with pytest.raises(QueryFailure) as refused:
        await service.export(plan["id"], PlanExportCommand(request_id="rule-export", expected_version=1, preview_id=preview["preview_id"]))
    assert refused.value.code == "export_blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["facebook", "tiktok"])
async def test_mirror_target_channel_without_complete_source_contract_is_blocked(canonical_world, channel):
    from test_planning import editable

    from ggwork_pick.completion_contracts import PlanUpdate

    query, repo, world, conn, shared, importer, as_of = canonical_world
    changed = gw.with_v1(world, [{**r, "channel_rules": {"youtube": "allowed", channel: "allowed"}} for r in world.v1_rows])
    with bf._reader_role_env(query.reader._dsn.split("://", 1)[1].split(":", 1)[0]):
        await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    plan = await source_plan(repo, ["c-1"], "unsupported-channel")
    patch = {**editable(plan), "request_id": "channel", "expected_version": 1}
    patch["rows"][0]["channel"] = channel
    plan = await PlanningService(repo).update(plan["id"], PlanUpdate.model_validate(patch))
    preview = await PlanningExportService(repo, query).preview(plan["id"], PlanVersionCommand(request_id="channel-preview", expected_version=2))
    assert preview["exportable"] is False and preview["checks"][0]["blockers"]
