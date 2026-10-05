"""2026-10-05 (evaluation batch 2): what the query tool told the model beside a result (why nothing matched, what
counted as hot, the data page's stale warnings) never reached the card, and a candidate item dropped the row's tags,
listing date and channel rules, so neither the model nor the user could answer "what genre / when listed / can it go
on YouTube" without guessing. GET /api/pick/results/{id}/notes serves both for the card, read from the result's own
frozen batch; the result shape itself is unchanged (the frontend parses it strictly). The query and detail tools put
the same item facts in front of the model. Both dialects; synthetic data only."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

ALICE = {"test-owner": "alice"}
FACT_KEYS = {"tags", "listed_at", "channel_rules"}


def drama(i, *, tags=(), listed_at=None, rules=None, signals=(), availability="active", language="en"):
    row = {
        "source": "synthetic",
        "source_id": f"n{i}",
        "language": language,
        "title": f"Notes {i}",
        "theater": "KalosTV",
        "tags": list(tags),
        "availability": availability,
        "channel_rules": rules or {},
        "signals": list(signals),
    }
    return {**row, "listed_at": listed_at} if listed_at else row


def signal(kind, observed, rank=1):
    return {"kind": kind, "source_ref": f"ref:{kind}:{observed}", "observed_at": observed, "rank": rank}


async def _import(service, rows) -> dict:
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    repo = PickRepository(service.session_factory, "alice")
    return await Importer(repo, service.data_dir).catalog(json.dumps(rows, ensure_ascii=False).encode(), "json")


async def _query(service, filters, call_id="c"):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    return await SelectionService(PickRepository(service.session_factory, "alice")).query(filters, thread_id="t", run_id="r", call_id=call_id)


async def _notes(client, result_id, headers=ALICE):
    return await client.get(f"/api/pick/results/{result_id}/notes", headers=headers)


# ---- the card's notes ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_carry_each_items_facts_from_the_frozen_batch(app_client):
    client, service = app_client
    await _import(
        service,
        [
            drama(1, tags=["复仇", "豪门"], listed_at="2026-09-01", rules={"youtube": "allowed", "tiktok": "denied"}),
            drama(2),
        ],
    )
    result = await _query(service, {"language": "en"})
    response = await _notes(client, result["id"])
    assert response.status_code == 200
    body = response.json()
    by_title = {item["title"]: body["item_facts"][item["item_id"]] for item in result["items"]}
    assert by_title["Notes 1"] == {"tags": ["复仇", "豪门"], "listed_at": "2026-09-01", "channel_rules": {"youtube": "allowed", "tiktok": "denied"}}
    assert by_title["Notes 2"] == {"tags": [], "listed_at": None, "channel_rules": {}}
    assert not {"zero_diagnosis", "hot_scope", "data_notices"} & set(body)
    # The result itself keeps the shape the frontend parses strictly.
    view = (await client.get(f"/api/pick/results/{result['id']}", headers=ALICE)).json()
    assert all(not FACT_KEYS & set(item) for item in view["items"])


@pytest.mark.asyncio
async def test_notes_explain_an_empty_result_and_a_hot_question(app_client):
    client, service = app_client
    await _import(
        service,
        [
            drama(1, tags=["复仇"], language="ko", signals=[signal("kd", "2026-09-30")]),
            drama(2, signals=[signal("clk", "2026-09-30")]),
            drama(3, signals=[signal("kd", "2026-09-30")]),
        ],
    )
    # The only 复仇 drama is Korean: nothing English carries the tag.
    empty = await _query(service, {"language": "en", "tags": ["复仇"]}, "c1")
    body = (await _notes(client, empty["id"])).json()
    assert body["item_facts"] == {}
    without = {step["condition"]: step["matched_total"] for step in body["zero_diagnosis"]["without_each"]}
    assert without["tags"] == 2
    hot = await _query(service, {"hot_only": True}, "c2")
    body = (await _notes(client, hot["id"])).json()
    assert body["hot_scope"] == {"counted": ["kd"], "not_counted": ["clk"]}


@pytest.mark.asyncio
async def test_notes_carry_the_stale_board_warning(app_client):
    client, service = app_client
    stale_day = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
    await _import(service, [drama(1, signals=[signal("kd", stale_day)])])
    result = await _query(service, {"signal_kind": "kd"})
    notices = (await _notes(client, result["id"])).json()["data_notices"]
    assert any("kd" in notice and stale_day in notice for notice in notices)


@pytest.mark.asyncio
async def test_notes_are_owner_scoped_and_gone_with_a_pruned_batch(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    shared = PickRepository.shared(service.session_factory)
    old = await Importer(shared, service.data_dir).catalog(json.dumps([drama(1)]).encode(), "json")
    result = await _query(service, {})
    assert result["catalog_batch_id"] == old["id"]
    assert (await _notes(client, result["id"], {"test-owner": "bob"})).status_code == 404
    assert (await _notes(client, "no-such-result")).status_code == 404
    assert (await _notes(client, result["id"], {})).status_code == 401
    await Importer(shared, service.data_dir).catalog(json.dumps([drama(2)]).encode(), "json")
    await shared.prune_shared("catalog", 1, referenced_within=timedelta(0))
    response = await _notes(client, result["id"])
    assert response.status_code == 410
    assert result["id"] not in response.text


# ---- the model's view -------------------------------------------------------------------------------------------


async def _runtime(service, call_id):
    from ggwork_pick.context import PickLifecycle

    store = ExtensionData("task1")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task1", "run1", "thread1", "lead"))
    return SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id=call_id)


@pytest.mark.asyncio
async def test_the_query_and_detail_tools_show_the_model_each_items_facts(app_client):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.tools import get_drama_detail_tool, query_candidates_tool

    _, service = app_client
    await _import(service, [drama(1, tags=["复仇"], listed_at="2026-09-01", rules={"youtube": "allowed"})])
    runtime = await _runtime(service, "call1")
    queried = json.loads(await query_candidates_tool.coroutine(filters={"language": "en"}, runtime=runtime))
    (item,) = queried["items"]
    assert {key: item[key] for key in FACT_KEYS} == {"tags": ["复仇"], "listed_at": "2026-09-01", "channel_rules": {"youtube": "allowed"}}
    detail = json.loads(await get_drama_detail_tool.coroutine(result_id=queried["id"], item_id=item["item_id"], runtime=runtime))
    assert detail["item"]["tags"] == ["复仇"] and detail["item"]["listed_at"] == "2026-09-01"
    # Only the model's view: the stored snapshot, which saves and replays read, is unchanged.
    stored = await PickRepository(service.session_factory, "alice").result(queried["id"])
    assert not FACT_KEYS & set(stored["ordered_items_json"][0])


def test_the_instructions_and_the_skill_tell_the_model_to_answer_from_the_item_facts():
    from pathlib import Path

    from ggwork_pick.middleware import PICK_INSTRUCTIONS

    skill = (Path(__file__).resolve().parents[3] / "skills/public/pick-drama/SKILL.md").read_text(encoding="utf-8")
    for text in (PICK_INSTRUCTIONS, skill):
        assert all(key in text for key in FACT_KEYS)
