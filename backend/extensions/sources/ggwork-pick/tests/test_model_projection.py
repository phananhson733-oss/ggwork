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
    assert {key: value for key, value in projected.items() if key not in {"items", "item"}} == {
        key: value for key, value in original.items() if key not in {"items", "item"}
    }
    assert {key: value for key, value in shown.items() if key != "evidence"} == {
        key: value for key, value in item.items() if key not in {"detail_url", "evidence"}
    }
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


def test_independent_non_observation_source_and_unknown_nested_fact_survive():
    from ggwork_pick.model_projection import model_payload

    item = item_fixture()
    item["evidence"][0]["source_ref"] = "https://independent.synthetic.test/rank"
    item["evidence"][0]["future_fact"] = {"values": [None, 0, ""], "nested": {"grade": "S"}}
    projected = model_payload({"item": item})
    assert projected["item"]["evidence"][0] == item["evidence"][0]
    projected["item"]["evidence"][0]["future_fact"]["values"].append("changed")
    assert item["evidence"][0]["future_fact"]["values"] == [None, 0, ""]


@pytest.mark.parametrize("kind", [None, 0])
def test_catalog_ingestion_rejects_non_string_evidence_kind(kind):
    from pydantic import ValidationError

    from ggwork_pick.imports import parse_catalog

    row = {
        "source": "synthetic",
        "source_id": "kind-test",
        "language": "en",
        "title": "Synthetic",
        "detail_url": "https://synthetic.test/row",
        "signals": [{"kind": kind, "source_ref": "https://synthetic.test/row"}],
    }
    with pytest.raises(ValidationError) as refused:
        parse_catalog(json.dumps([row]).encode(), "json")
    assert any(error["loc"] == ("signals", 0, "kind") and error["type"] == "string_type" for error in refused.value.errors())


def decode_model_evidence(payload):
    """Independent consumer: expand only the explicitly versioned model dictionary."""
    decoded = copy.deepcopy(payload)
    if decoded.get("evidence_encoding") == "inline-v1":
        return decoded["inline_payload"]
    if decoded.get("evidence_encoding") != "facts-ref-v1":
        return decoded
    decoded.pop("evidence_encoding")
    table = decoded.pop("evidence_facts")
    for item in decoded.get("items", []) if "items" in decoded else [decoded["item"]]:
        for evidence in item.get("evidence", []):
            if "facts_ref" in evidence:
                facts = table[evidence.pop("facts_ref")]
                assert not facts.keys() & evidence.keys()
                evidence.update(copy.deepcopy(facts))
    return decoded


def canonical_json(value):
    # Dict equality alone conflates True/1 and 1/1.0; JSON preserves these distinctions.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("count", [5, 10, 20])
def test_dictionary_roundtrips_original_ordinary_evidence_without_reordering(count):
    from ggwork_pick.model_projection import model_payload

    original = json.loads((Path(__file__).parent / f"fixtures/model_projection/ordinary-{max(10, count)}.json").read_text())
    if count == 5:
        original["items"] = original["items"][:5]
    before = canonical_json(original)
    encoded = model_payload(original)
    assert encoded.get("evidence_encoding") == "facts-ref-v1"
    assert canonical_json(decode_model_evidence(encoded)) == before
    assert canonical_json(original) == before
    assert model_payload(original) == encoded
    assert [item["item_id"] for item in encoded["items"]] == [item["item_id"] for item in original["items"]]
    for source, shown in zip(original["items"], encoded["items"], strict=True):
        assert len(source["evidence"]) == len(shown["evidence"])
        for a, b in zip(source["evidence"], shown["evidence"], strict=True):
            assert (b["citation_id"], b["source_ref"]) == (a["citation_id"], a["source_ref"])
            if a["kind"].startswith("obs_"):
                assert b == a
    encoded["evidence_facts"][next(iter(encoded["evidence_facts"]))]["note"] = "changed"
    assert canonical_json(original) == before


@pytest.mark.parametrize("shape", ["items", "item"])
def test_dictionary_preserves_missing_fields_unknowns_and_type_distinctions(shape):
    from ggwork_pick.model_projection import model_payload

    evidence = [
        {
            "citation_id": f"x:{i}",
            "source_ref": f"opaque:{i}",
            "kind": "ad",
            "label": "A" * 80,
            "note": "Only operational fact " * 8,
            "value": value,
            "rank": None,
            "grade": "S",
            "observed_at": None,
            "unit": "unknown",
            "future": {"values": [None, "", 0, False, 1, True, 1.0]},
        }
        for i, value in enumerate([None, "", 0, False, 1, True, 1.0, -0.0])
    ]
    evidence[0].pop("rank")
    item = {"item_id": "x", "evidence": evidence}
    original = {shape: [item] if shape == "items" else item}
    encoded = model_payload(original)
    assert encoded.get("evidence_encoding") == "facts-ref-v1"
    assert canonical_json(decode_model_evidence(encoded)) == canonical_json(original)
    shown = encoded[shape][0] if shape == "items" else encoded[shape]
    for source, compact in zip(evidence, shown["evidence"], strict=True):
        assert compact["future"] == source["future"] and compact["unit"] == "unknown"
        assert canonical_json(compact["value"]) == canonical_json(source["value"])
    restored = decode_model_evidence(encoded)
    restored_item = restored[shape][0] if shape == "items" else restored[shape]
    assert "rank" not in restored_item["evidence"][0]


@pytest.mark.parametrize("collision", ["evidence_facts", "evidence_encoding", "facts_ref"])
def test_dictionary_reserved_field_conflicts_fall_back_without_overwriting(collision):
    from ggwork_pick.model_projection import model_payload

    original = json.loads((Path(__file__).parent / "fixtures/model_projection/ordinary-10.json").read_text())
    if collision == "facts_ref":
        original["items"][0]["evidence"][0][collision] = {"future": "must survive"}
    else:
        original[collision] = {"future": "must survive"}
    encoded = model_payload(original)
    assert encoded["evidence_encoding"] == "inline-v1"
    assert decode_model_evidence(encoded) == original


def test_original_ordinary_samples_reach_target_with_reversible_dictionary():
    from test_projection_benchmark import benchmark_module

    assert all(sample["target_met"] for sample in benchmark_module().benchmark_report()["samples"])


def test_small_or_missing_kind_evidence_stays_inline_and_never_grows():
    from ggwork_pick.model_projection import model_payload

    for evidence in [[], [{"kind": "a"}, {"kind": "a"}], [{"note": "unknown source"}, {"note": "unknown source"}]]:
        original = {"item": {"evidence": evidence}}
        assert model_payload(original) == original


def test_dictionary_does_not_mix_sources_or_move_unknown_nested_fields():
    from ggwork_pick.model_projection import model_payload

    original = {
        "item": {
            "evidence": [
                {
                    "kind": kind,
                    "source_ref": source,
                    "citation_id": f"i:{i}",
                    "label": "repeated label " * 10,
                    "note": "shared caveat " * 10,
                    "value": None,
                    "future": {"note": "retain inline"},
                }
                for i, (kind, source) in enumerate([("kd", "opaque:a"), ("kd", "opaque:b"), ("ad", "opaque:c"), ("ad", "opaque:d")])
            ]
        }
    }
    encoded = model_payload(original)
    assert encoded["evidence_encoding"] == "facts-ref-v1"
    assert canonical_json(decode_model_evidence(encoded)) == canonical_json(original)
    evidence = encoded["item"]["evidence"]
    assert evidence[0]["facts_ref"] != evidence[2]["facts_ref"]
    assert [e["source_ref"] for e in evidence] == ["opaque:a", "opaque:b", "opaque:c", "opaque:d"]
    assert all(e["future"] == {"note": "retain inline"} for e in evidence)
    evidence[0]["future"]["note"] = "changed"
    assert original["item"]["evidence"][0]["future"]["note"] == "retain inline"


def test_only_query_and_detail_tool_descriptions_explain_dictionary_reading():
    from ggwork_pick import tools

    for tool in (tools.query_candidates_tool, tools.get_drama_detail_tool):
        assert all(word in tool.description for word in ("facts-ref-v1", "facts_ref", "evidence_facts", "合并", "obs"))
    for tool in (tools.count_candidates_tool, tools.search_knowledge_tool, tools.prepare_selection_tool):
        assert "facts_ref" not in tool.description


@pytest.mark.asyncio
async def test_real_query_and_detail_encode_but_http_cache_and_checker_keep_full_data(app_client):
    from test_result_notes import _import, _runtime, drama

    from ggwork_pick import tools
    from ggwork_pick.answer_check import with_posted
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    await _import(
        service,
        [
            drama(
                i,
                signals=[
                    {
                        "kind": "ad",
                        "source_ref": f"opaque:{i}:{j}",
                        "observed_at": None,
                        "label": "Synthetic note " * 10,
                        "note": "Synthetic authorization caveat " * 10,
                        "value": None,
                        "grade": "S",
                    }
                    for j in range(2)
                ],
            )
            for i in range(10)
        ],
    )
    runtime = await _runtime(service, "dictionary-query")
    queried = json.loads(await tools.query_candidates_tool.coroutine(filters={"limit": 10}, runtime=runtime))
    assert queried["evidence_encoding"] == "facts-ref-v1"
    repo = PickRepository(service.session_factory, "alice")
    stored = await repo.result(queried["id"])
    cached_rows = await repo.catalog_rows(stored["catalog_batch_id"])
    cached_before = copy.deepcopy(cached_rows)
    stored_before = copy.deepcopy(stored)
    data_as_of = await repo.result_data_as_of(stored, emit_mirror_version=False)
    model_view = await SelectionService(repo).model_view(stored, data_as_of=data_as_of)
    decoded = decode_model_evidence(queried)
    assert canonical_json(decoded["items"]) == canonical_json(model_view["items"])
    task = task_from_runtime(runtime)
    assert task.known_titles == {item["title"] for item in model_view["items"]}
    assert task.posted_seen == with_posted({}, model_view["items"])
    item = decoded["items"][0]
    detail = json.loads(await tools.get_drama_detail_tool.coroutine(result_id=queried["id"], item_id=item["item_id"], runtime=runtime))
    assert detail["evidence_encoding"] == "facts-ref-v1"
    full_detail = await SelectionService(repo).detail(queried["id"], item["item_id"], data_as_of=data_as_of)
    assert canonical_json(decode_model_evidence(detail)) == canonical_json({**full_detail, "data_as_of": data_as_of})
    http = (await client.get(f"/api/pick/results/{queried['id']}", headers={"test-owner": "alice"})).json()
    assert "evidence_facts" not in http and "evidence_encoding" not in http
    assert all("kind" in evidence and "facts_ref" not in evidence for row in http["items"] for evidence in row["evidence"])
    assert await repo.result(queried["id"]) == stored_before
    assert cached_rows == cached_before


def test_colliding_valid_looking_marker_is_preserved_as_inline_data_not_decoded():
    from ggwork_pick.model_projection import model_payload

    original = {
        "id": "r",
        "evidence_encoding": "facts-ref-v1",
        "evidence_facts": {"0": {"note": "future meaning"}},
        "items": [{"evidence": [{"facts_ref": "0", "note": "original meaning", "value": None}]}],
    }
    encoded = model_payload(original)
    assert encoded["id"] == "r"
    assert encoded["evidence_encoding"] == "inline-v1"
    assert encoded["inline_payload"] == original
    assert decode_model_evidence(encoded) == original
    assert original["items"][0]["evidence"][0]["note"] == "original meaning"


def test_collision_fallback_preserves_falsy_root_ids_and_does_not_recurse():
    from ggwork_pick.model_projection import model_payload

    original = {
        "id": None,
        "result_id": "",
        "evidence_encoding": "facts-ref-v1",
        "evidence_facts": {"0": {"note": "future field, not inherited"}},
        "inline_payload": {"future": [0, None, ""]},
        "items": [{"evidence": [{"facts_ref": "0", "value": None}]}],
    }
    before = canonical_json(original)
    shown = model_payload(original)
    assert "id" in shown and shown["id"] is None
    assert "result_id" in shown and shown["result_id"] == ""
    assert canonical_json(decode_model_evidence(shown)) == before
    assert "note" not in decode_model_evidence(shown)["items"][0]["evidence"][0]
    shown["inline_payload"]["inline_payload"]["future"].append("changed")
    assert canonical_json(original) == before


def test_valid_observation_schema_stays_inline_beside_encoded_ordinary_evidence():
    from ggwork_pick.model_projection import model_payload
    from ggwork_pick.observe.contract import ObsEvidence

    examples = json.loads((Path(__file__).parent / "fixtures/obs_contract/evidence.json").read_text())["valid"]
    observations = [copy.deepcopy(example["value"]) for example in examples]
    ordinary = [{"kind": "ad", "citation_id": f"i:{i}", "source_ref": f"opaque:{i}", "note": "same operational caveat " * 20} for i in range(3)]
    original = {"item": {"evidence": observations + ordinary}}
    shown = model_payload(original)
    assert shown["evidence_encoding"] == "facts-ref-v1"
    for expected, actual in zip(observations, shown["item"]["evidence"], strict=False):
        assert actual == expected and "facts_ref" not in actual
        ObsEvidence.model_validate(actual)
    assert canonical_json(decode_model_evidence(shown)) == canonical_json(original)
