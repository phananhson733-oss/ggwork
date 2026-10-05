"""Synthetic model-only projection contracts; full persisted evidence stays authoritative."""

import copy
import json
from pathlib import Path

import pytest


def item_fixture():
    link = "https://synthetic.test/catalog/" + "row-locator/" * 30
    facts = [
        {"kind": "kd", "label": "Daily rank", "rank": 1, "value": None, "grade": "", "note": ""},
        {"kind": "sm", "label": "Rating", "rank": None, "value": None, "grade": "S", "note": ""},
        {"kind": "ad", "label": "Operations", "rank": None, "value": None, "grade": "", "note": "Authorization requires review"},
        {"kind": "clk", "label": "Clicks", "rank": None, "value": 0, "grade": "", "note": "", "unit": "clicks", "window": None},
        {"kind": "obs_trends", "label": "US · Synthetic", "rank": None, "value": "", "grade": "", "note": "Synthetic unobserved; not revenue"},
    ]
    evidence = [dict(citation_id=f"item-1:{i}", source_ref=link, observed_at=None, **fact) for i, fact in enumerate(facts)]
    evidence[-1] = json.loads((Path(__file__).parent / "fixtures/obs_contract/evidence.json").read_text())["valid"][0]["value"]
    evidence[-1]["future_metric"] = {"unit": "unknown", "value": 0}
    return {
        "item_id": "item-1",
        "identity": '["synthetic","1","en"]',
        "title": "Synthetic",
        "theater": "Synthetic",
        "language": "en",
        "availability": "unknown",
        "reason": "Evidence",
        "warnings": ["Check availability"],
        "detail_url": link,
        "evidence": evidence,
        "tags": ["synthetic"],
        "listed_at": None,
        "channel_rules": {"youtube": "unknown"},
        "posted": {"matched": False, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []},
    }


@pytest.mark.parametrize("shape", ["query", "detail"])
def test_projection_preserves_all_facts_unknown_fields_and_source_objects(shape):
    from ggwork_pick.model_projection import model_payload

    item = item_fixture()
    original = (
        {"id": "result-1", "items": [item], "conditions": {"limit": 1}, "matched_total": 1} if shape == "query" else {"result_id": "result-1", "item": item}
    )
    original.update(data_as_of={"shared": True}, data_notices=["old"], hot_scope={"counted": ["kd"]}, future_field={"nested": [0, None]})
    before = copy.deepcopy(original)
    projected = model_payload(original)
    shown = projected["items"][0] if shape == "query" else projected["item"]
    assert "detail_url" not in shown
    for source, compact in zip(item["evidence"], shown["evidence"], strict=True):
        removable = {"source_ref"} if source["source_ref"] == item["detail_url"] else set()
        assert compact == {key: value for key, value in source.items() if key not in removable}
    assert shown["evidence"][-1]["source_ref"].startswith("obs:")
    assert original == before
    assert model_payload(original) == projected
    # Projection owns its nested data too; later consumers cannot mutate the shared rows/snapshot.
    shown["posted"]["accounts"].append("changed")
    assert original == before


def test_source_ref_stays_when_it_is_the_only_source_or_cannot_be_mapped_back():
    from ggwork_pick.model_projection import model_payload

    item = item_fixture()
    item.pop("detail_url")
    assert model_payload({"item": item}) == {"item": item}
    item = item_fixture()
    item["evidence"][0].pop("citation_id")
    shown = model_payload({"item": item})["item"]
    assert shown["evidence"][0] == item["evidence"][0]


@pytest.mark.parametrize("count", [5, 10, 20])
def test_repeated_row_links_shrink_without_losing_items_or_evidence(count):
    from ggwork_pick.model_projection import model_payload

    original = {"id": "result-1", "items": [item_fixture() for _ in range(count)]}
    before = json.dumps(original, ensure_ascii=False).encode()
    projected = model_payload(original)
    after = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(projected["items"]) == count
    assert all(len(item["evidence"]) == 5 for item in projected["items"])
    assert len(after) < len(before) * 0.8


def test_all_existing_observation_contract_examples_remain_valid():
    from ggwork_pick.model_projection import model_payload
    from ggwork_pick.observe.contract import ObsEvidence

    fixture = json.loads((Path(__file__).parent / "fixtures/obs_contract/evidence.json").read_text())
    for case in fixture["valid"]:
        evidence = case["value"]
        item = {"detail_url": evidence["source_ref"], "evidence": [evidence]}
        shown = model_payload({"item": item})["item"]["evidence"][0]
        assert shown == evidence
        ObsEvidence.model_validate(shown)


@pytest.mark.asyncio
async def test_actual_tools_project_only_query_and_detail_and_preserve_http_and_checker(app_client, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from test_result_notes import _import, _runtime, drama, signal

    from ggwork_pick import tools
    from ggwork_pick.answer_check import with_posted
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    link = "https://synthetic.test/row"
    row = drama(1, signals=[{**signal("kd", "2026-10-04"), "source_ref": link}])
    row["detail_url"] = link
    row["posted"] = {"matched": True, "records": ["SD-1"], "post_count": 2, "sched_count": 0, "last_post_on": "2026-09-30", "accounts": ["account"]}
    await _import(service, [row])
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).knowledge(b"# Synthetic rules\nConfirm before saving", "rules.md", "synthetic:rules")
    runtime = await _runtime(service, "projection-query")
    queried = json.loads(await tools.query_candidates_tool.coroutine(filters={}, runtime=runtime))
    task = task_from_runtime(runtime)
    assert task.known_titles == {"Notes 1"}
    assert task.posted_seen == with_posted({}, queried["items"])
    item = queried["items"][0]
    assert "detail_url" not in item and "source_ref" not in item["evidence"][0]
    record = await repo.result(queried["id"])
    before = copy.deepcopy(record)
    rows = await repo.catalog_rows(record["catalog_batch_id"])
    rows_before = copy.deepcopy(rows)
    http = (await client.get(f"/api/pick/results/{queried['id']}", headers={"test-owner": "alice"})).json()
    assert http["items"][0]["detail_url"] == link
    assert http["items"][0]["evidence"][0]["source_ref"] == link
    full_detail = await SelectionService(repo).detail(queried["id"], item["item_id"], data_as_of=queried["data_as_of"])
    detail = json.loads(await tools.get_drama_detail_tool.coroutine(result_id=queried["id"], item_id=item["item_id"], runtime=runtime))
    assert detail["item"] == item
    assert full_detail["item"]["evidence"][0]["source_ref"] == link
    assert task.posted_seen == with_posted({}, queried["items"])
    assert await repo.result(queried["id"]) == before and rows == rows_before

    # No SQL occurs in projection, and count/knowledge/prepare never call it.
    def refuse_projection(_):
        pytest.fail("projection reached a non-candidate tool")

    monkeypatch.setattr(tools, "model_payload", refuse_projection)
    counted = json.loads(await tools.count_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert counted["total"] == 1
    knowledge = json.loads(await tools.search_knowledge_tool.coroutine(query="Synthetic", runtime=runtime))
    assert knowledge["documents"][0]["source_ref"] == "synthetic:rules"
    prepared = json.loads(await tools.prepare_selection_tool.coroutine(result_id=queried["id"], item_ids=[item["item_id"]], note="keep note", runtime=runtime))
    assert prepared["requires_confirmation"] is True and prepared["note"] == "keep note"
    assert prepared["item_ids"] == [item["item_id"]]

    from ggwork_pick.model_projection import model_payload

    statements = []

    def record_sql(*args):
        statements.append(args[2])

    event.listen(Engine, "before_cursor_execute", record_sql)
    try:
        model_payload(queried)
        model_payload(full_detail)
    finally:
        event.remove(Engine, "before_cursor_execute", record_sql)
    assert statements == []
