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


@pytest.fixture
def october_fifth(monkeypatch):
    from ggwork_pick import freshness

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 12, tzinfo=UTC)

    monkeypatch.setattr(freshness, "datetime", Clock)
    return Clock.now()


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


async def _freeze_result(service, result_id, *, created_at="2026-09-30T20:00:00+08:00", captured="2026-09-30T11:00:00+00:00"):
    from sqlalchemy import update

    from ggwork_pick.models import candidate_sets

    frozen = {"source_as_of": captured, "published_at": captured, "freshness": {"catalogImportedAt": captured}, "scope": None, "shared": False}
    async with service.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == result_id).values(created_at=created_at, data_as_of_json=frozen))
    return frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("created_at,stale", [("2026-09-30T20:00:00+08:00", False), ("2026-10-02T12:00:00+00:00", True)])
async def test_notes_recheck_freshness_at_creation_and_get_is_read_only(app_client, october_fifth, created_at, stale):
    import copy

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await _import(service, [drama(1, signals=[signal("kd", "2026-09-30")])])
    result = await _query(service, {"signal_kind": "kd"})
    await _freeze_result(service, result["id"], created_at=created_at)
    repo = PickRepository(service.session_factory, "alice")
    before = copy.deepcopy(await repo.result(result["id"]))
    rows = await repo.catalog_rows(result["catalog_batch_id"])
    rows_before = copy.deepcopy(rows)
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    event.listen(Engine, "before_cursor_execute", record_sql)
    try:
        response = await _notes(client, result["id"])
    finally:
        event.remove(Engine, "before_cursor_execute", record_sql)
    assert response.status_code == 200
    body = response.json()
    assert body["notices_reference_at"] == datetime.fromisoformat(created_at).astimezone(UTC).isoformat()
    assert bool(body.get("data_notices")) == stale
    assert all("查询时" in notice for notice in body.get("data_notices", []))
    assert not any(word in str(body) for word in ("同步可能停", "回答里", "没有新批次"))
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}
    assert await repo.result(result["id"]) == before
    assert rows == rows_before


@pytest.mark.asyncio
@pytest.mark.parametrize("created_at", [None, "", "not-a-time", "9999-12-31T23:59:59-23:59"])
async def test_notes_unknown_creation_does_not_fall_back_to_today(app_client, monkeypatch, created_at):
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await _import(service, [drama(1, signals=[signal("kd", "2026-09-20")])])
    result = await _query(service, {"signal_kind": "kd"})
    await _freeze_result(service, result["id"])
    original = PickRepository.result

    async def missing_time(self, result_id):
        record = await original(self, result_id)
        record["created_at"] = created_at
        return record

    monkeypatch.setattr(PickRepository, "result", missing_time)
    response = await _notes(client, result["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["notices_reference_at"] is None
    assert any("无法核对查询时点" in text for text in body["data_notices"])
    assert any("kd" in text and "采集时" in text for text in body["data_notices"])
    assert all("小时" not in text and "同步可能停" not in text for text in body["data_notices"])


@pytest.mark.asyncio
async def test_detail_tool_warns_about_current_age_of_frozen_result(app_client, october_fifth):
    from ggwork_pick.tools import get_drama_detail_tool, query_candidates_tool

    client, service = app_client
    await _import(service, [drama(1, signals=[signal("kd", "2026-09-30")])])
    runtime = await _runtime(service, "call1")
    queried = json.loads(await query_candidates_tool.coroutine(filters={"signal_kind": "kd"}, runtime=runtime))
    await _freeze_result(service, queried["id"])
    assert not (await _notes(client, queried["id"])).json().get("data_notices")
    detail = json.loads(await get_drama_detail_tool.coroutine(result_id=queried["id"], item_id=queried["items"][0]["item_id"], runtime=runtime))
    assert any("14 小时" in notice and "本轮" in notice for notice in detail["data_notices"])
    assert not any("同步可能停" in notice or "查询时" in notice for notice in detail["data_notices"])


@pytest.mark.asyncio
@pytest.mark.parametrize("hours,stale", [(14, False), (14 + 1 / 3600, True), (36, True), (36 + 1 / 3600, True)])
async def test_current_query_and_count_tools_use_current_pinned_batch_age(app_client, october_fifth, hours, stale):
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.pin import Pin
    from ggwork_pick.tools import count_candidates_tool, query_candidates_tool

    _, service = app_client
    batch = await _import(service, [drama(1)])
    runtime = await _runtime(service, "current")
    captured = (october_fifth - timedelta(hours=hours)).isoformat()
    data = {"source_as_of": captured, "published_at": captured, "freshness": {"catalogImportedAt": captured}, "scope": None, "shared": False}
    task = task_from_runtime(runtime)
    await task.repository(runtime)
    task.repin(Pin(batch["id"], data_as_of=data))
    queried = json.loads(await query_candidates_tool.coroutine(filters={}, runtime=runtime))
    counted = json.loads(await count_candidates_tool.coroutine(filters={}, runtime=runtime))
    for output in (queried, counted):
        assert bool(output.get("data_notices")) == stale
        assert any("36 小时" in notice for notice in output.get("data_notices", [])) == (hours > 36)
        assert all("本轮" in notice and "同步可能停" not in notice for notice in output.get("data_notices", []))
    assert queried.get("data_notices") == counted.get("data_notices")


@pytest.mark.asyncio
async def test_legacy_notes_fall_back_to_own_batch_not_another_owners(app_client):
    from sqlalchemy import update

    from ggwork_pick.imports import Importer
    from ggwork_pick.models import candidate_sets
    from ggwork_pick.repository import PickRepository

    client, service = app_client
    await _import(service, [drama(1, tags=["old-own-fact"])])
    result = await _query(service, {})
    async with service.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == result["id"]).values(data_as_of_json=None))
    await Importer(PickRepository(service.session_factory, "bob"), service.data_dir).catalog(json.dumps([drama(1, tags=["private-bob"])]).encode(), "json")
    response = await _notes(client, result["id"])
    assert response.status_code == 200
    assert list(response.json()["item_facts"].values())[0]["tags"] == ["old-own-fact"]
    assert "private-bob" not in response.text
    assert not response.json().get("data_notices")
