"""P2-8a: a result answers with the data_as_of it froze when it was written, not with its batch's current one.

Sources: plan:1622 and plan 2.5 items 1, 5 and 9; brief P2-8a tests 8-10 (U51). The scenario: batch pair A is published
with version v1 (as_of t1), result C1 freezes t1 and v1's freshness, then the same content pairs again with v2 (t2),
which rewrites the batch's source_as_of. C1 must keep answering with t1 everywhere: the results routes, the query
and detail tools, the replay. Those run on PostgreSQL only, since only it has pick_mirror. The same holds without a
mirror, where a v1 re-import of the same content rewrites the batch (U35), and so do the fallbacks for a result that
froze nothing or a value of another shape: those run on both dialects. Synthetic data only.
"""

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from mirror_pairs import (
    AS_OF,
    AS_OF_TEXT,
    NO_ACCEPT_EMPTY,
    V1_FRESHNESS,
    V2_FRESHNESS,
    building_version,
    catalog_payload,
    now,
    open_service,
    publish_pair,
    stage_pair,
)
from sqlalchemy import null, update
from test_frontend_contract import FIXTURE, _shape

ALICE = {"test-owner": "alice"}
LATER = AS_OF.replace(hour=15)
LATER_TEXT = "2026-09-24T15:38:00.000Z"
LATER_V2_FRESHNESS = {**V2_FRESHNESS, "importedAt": "2026-09-24T14:10:00.000Z", "rsSyncedAt": "2026-09-24T13:05:00.000Z", "rows": 121}


@asynccontextmanager
async def _client(service):
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
    from fastapi import FastAPI

    from ggwork_pick.routes import build_router

    service.run_evidence_reader = SimpleNamespace(get_run_status=AsyncMock(return_value=SimpleNamespace(status="success")))
    app = FastAPI()

    def principal(request):
        # Test-only identity resolver, as in conftest's app_client.
        return ExtensionPrincipal(request.headers["test-owner"]) if "test-owner" in request.headers else None

    setattr(app.state, EXTENSION_PRINCIPAL_RESOLVER_KEY, principal)
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def world(pg_db_url, tmp_path):
    engine, service, shared, importer = await open_service(pg_db_url, tmp_path)
    async with _client(service) as client:
        yield SimpleNamespace(engine=engine, service=service, shared=shared, importer=importer, client=client)
    await engine.dispose()


def _repo(service):
    from ggwork_pick.repository import PickRepository

    return PickRepository(service.session_factory, "alice")


def _alice(world):
    return _repo(world.service)


async def _set_frozen(service, result_id: str, value) -> None:
    from ggwork_pick.models import candidate_sets

    async with service.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == result_id).values(data_as_of_json=value))


async def _repair(world) -> int:
    """The same content pairs again, with a later version: the batches are reused and their source_as_of rewritten."""
    staged = await stage_pair(world.importer, "a", as_of_text=LATER_TEXT)
    version_id, schema = await building_version(world.engine, as_of=LATER, freshness=LATER_V2_FRESHNESS)
    await world.shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), **NO_ACCEPT_EMPTY)
    return version_id


async def _card_then_repaired(world):
    from ggwork_pick.selection import SelectionService

    version_1, pair = await publish_pair(world.engine, world.shared, world.importer, "a")
    card = await SelectionService(_alice(world)).query({"limit": 2}, thread_id="t", run_id="r0", call_id="c0")
    version_2 = await _repair(world)
    return version_1, version_2, pair, await _alice(world).result(card["id"])


async def _turn(service, run_id: str, reference: str | None = None, call_id: str = "q1"):
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle

    store = ExtensionData(run_id)
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run_id, run_id, "t", "lead"))
    context = {"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}
    if reference is not None:
        context["pick_reference"] = {"result_id": reference}
    return SimpleNamespace(context=context, tool_call_id=call_id)


async def _answers(client, service, record: dict) -> dict:
    """What C1 answers with, per reader: the two results routes, the replay, and the detail tool in a new turn."""
    from ggwork_pick.tools import get_drama_detail_tool

    single = (await client.get(f"/api/pick/results/{record['id']}", headers=ALICE)).json()
    listed = (await client.get("/api/pick/results", params={"thread_id": "t"}, headers=ALICE)).json()["results"]
    replay = (await client.get("/api/pick/replay", params={"result_id": record["id"]}, headers=ALICE)).json()
    runtime = await _turn(service, "r-detail", reference=record["id"], call_id="d1")
    item_id = record["ordered_items_json"][0]["item_id"]
    detail = json.loads(await get_drama_detail_tool.coroutine(result_id=record["id"], item_id=item_id, runtime=runtime))
    return {
        "result": single["data_as_of"],
        "results": next(row for row in listed if row["id"] == record["id"])["data_as_of"],
        "replay": replay["data_as_of"],
        "detail": detail["data_as_of"],
    }


@pytest.mark.asyncio
async def test_an_old_result_keeps_its_frozen_data_as_of(world):
    from ggwork_pick.pin import v1_freshness
    from ggwork_pick.repository import DATA_AS_OF_KEYS
    from ggwork_pick.tools import count_candidates_tool

    version_1, version_2, pair, record = await _card_then_repaired(world)
    frozen = record["data_as_of_json"]
    assert record["mirror_version"] == version_1 and frozen["source_as_of"] == AS_OF_TEXT and frozen["freshness"] == V1_FRESHNESS
    # The batch itself has moved on to t2.
    batch_now = await _alice(world).data_as_of(pair[0]["id"])
    assert batch_now["source_as_of"] == LATER_TEXT and batch_now != frozen
    answers = await _answers(world.client, world.service, record)
    assert answers == {"result": frozen, "results": frozen, "replay": frozen, "detail": frozen}
    replay = (await world.client.get("/api/pick/replay", params={"result_id": record["id"]}, headers=ALICE)).json()
    assert replay["mirror_version"] == version_1
    # The frontend reads freshness under the v1 names (frontend/src/core/pick/format.ts:22-23); the keys stay DATA_AS_OF_KEYS.
    assert tuple(answers["result"]) == DATA_AS_OF_KEYS
    assert {"catalogImportedAt", "reelshortSyncedAt"} <= set(answers["result"]["freshness"])
    # The payload keeps the fixture's keys and, freshness aside (a free-form record there), its data_as_of shape.
    fixture = json.loads(FIXTURE.read_text())
    single = (await world.client.get(f"/api/pick/results/{record['id']}", headers=ALICE)).json()
    assert set(single) == set(fixture)
    assert _shape({**single["data_as_of"], "freshness": {}}) == _shape({**fixture["data_as_of"], "freshness": {}})
    # A count this turn stands on this turn's pin: v2 and its freshness.
    counted = json.loads(await count_candidates_tool.coroutine(filters={}, runtime=await _turn(world.service, "r-count")))
    assert counted["data_as_of"]["source_as_of"] == LATER_TEXT and counted["data_as_of"]["freshness"] == v1_freshness(LATER_V2_FRESHNESS)
    assert counted["data_as_of"] == (await _alice(world).current_pin()).data_as_of
    assert (await _alice(world).current_pin()).mirror_version == version_2


@pytest.mark.asyncio
async def test_a_result_that_froze_nothing_falls_back_to_its_batch(world):
    from ggwork_pick.models import candidate_sets

    _, _, pair, record = await _card_then_repaired(world)
    async with world.service.session_factory() as session, session.begin():
        await session.execute(update(candidate_sets).where(candidate_sets.c.id == record["id"]).values(data_as_of_json=null()))
    batch_now = await _alice(world).data_as_of(pair[0]["id"])
    answers = await _answers(world.client, world.service, await _alice(world).result(record["id"]))
    assert answers == {"result": batch_now, "results": batch_now, "replay": batch_now, "detail": batch_now}
    assert batch_now["source_as_of"] == LATER_TEXT


@pytest.mark.asyncio
async def test_a_repeated_query_call_answers_with_the_frozen_value(world):
    from ggwork_pick.selection import SelectionService, result_view
    from ggwork_pick.tools import query_candidates_tool

    await publish_pair(world.engine, world.shared, world.importer, "a")
    runtime = await _turn(world.service, "r1")
    first = json.loads(await query_candidates_tool.coroutine(filters={"limit": 2}, runtime=runtime))
    record = await _alice(world).result(first["id"])
    assert first["data_as_of"] == record["data_as_of_json"] and first["data_as_of"]["freshness"] == V1_FRESHNESS
    # A paired publish between the two calls rewrites the batch the result stands on.
    await _repair(world)
    second = json.loads(await query_candidates_tool.coroutine(filters={"limit": 2}, runtime=runtime))
    assert second["id"] == first["id"] and second["data_as_of"] == first["data_as_of"]
    assert second["data_as_of"]["source_as_of"] == AS_OF_TEXT
    # query() still answers with exactly the view, as before; query_with_record adds the stored row beside it.
    selection = SelectionService(_alice(world))
    call = dict(thread_id="t", run_id="r1", call_id="q1", use_latest=False)
    assert await selection.query({"limit": 2}, **call) == result_view(record)
    view, stored = await selection.query_with_record({"limit": 2}, **call)
    assert view == result_view(record) and stored == record
    assert "data_as_of" not in view and not {"excluded_json", "mirror_version", "data_as_of_json"} & set(view)


@pytest.mark.asyncio
async def test_a_new_query_answers_with_what_it_froze(world):
    from ggwork_pick.tools import query_candidates_tool

    await publish_pair(world.engine, world.shared, world.importer, "a")
    version_2 = await _repair(world)
    fresh = json.loads(await query_candidates_tool.coroutine(filters={"limit": 1}, runtime=await _turn(world.service, "r2")))
    record = await _alice(world).result(fresh["id"])
    assert record["mirror_version"] == version_2 and fresh["data_as_of"] == record["data_as_of_json"]
    assert fresh["data_as_of"]["source_as_of"] == LATER_TEXT


@pytest.mark.asyncio
async def test_a_malformed_frozen_value_falls_back_to_the_batch(app_client, caplog):
    from ggwork_pick.imports import Importer
    from ggwork_pick.models import candidate_sets
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    alice = PickRepository(service.session_factory, "alice")
    batch = await Importer(alice, service.data_dir).catalog(catalog_payload("m"), "json")
    result = await SelectionService(alice).query({}, thread_id="t", run_id="r", call_id="c1")
    async with service.session_factory() as session, session.begin():
        stored = update(candidate_sets).where(candidate_sets.c.id == result["id"]).values(data_as_of_json={"source_as_of": "坏掉的冻结值"})
        await session.execute(stored)
    with caplog.at_level(logging.WARNING, logger="ggwork_pick.repository"):
        view = (await client.get(f"/api/pick/results/{result['id']}", headers=ALICE)).json()
    assert view["data_as_of"] == await alice.data_as_of(batch["id"])
    assert any(result["id"] in record.getMessage() for record in caplog.records)
    assert not any("坏掉的冻结值" in record.getMessage() for record in caplog.records)


async def _reused_card(service):
    """A shared batch imported at t1, a card on it, then the same content again at t2: _reuse rewrites the batch."""
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    importer = Importer(PickRepository.shared(service.session_factory), service.data_dir)
    batch = await importer.catalog(catalog_payload("s"), "json", source_as_of=AS_OF_TEXT)
    card = await SelectionService(_repo(service)).query({"limit": 2}, thread_id="t", run_id="r0", call_id="c0")
    assert (await importer.catalog(catalog_payload("s"), "json", source_as_of=LATER_TEXT))["id"] == batch["id"]
    return batch, await _repo(service).result(card["id"])


def _repository_records(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "ggwork_pick.repository"]


@pytest.mark.asyncio
async def test_a_result_keeps_what_it_froze_when_its_batch_is_reused_without_a_mirror(app_client):
    client, service = app_client
    batch, record = await _reused_card(service)
    frozen = record["data_as_of_json"]
    assert frozen["source_as_of"] == AS_OF_TEXT and record["mirror_version"] is None
    assert (await _repo(service).data_as_of(batch["id"]))["source_as_of"] == LATER_TEXT
    assert await _answers(client, service, record) == {"result": frozen, "results": frozen, "replay": frozen, "detail": frozen}


@pytest.mark.asyncio
async def test_a_result_that_froze_nothing_reads_its_batch_quietly(app_client, caplog):
    """Every result from before P2 froze nothing: expected, so the fallback logs nothing."""
    client, service = app_client
    batch, record = await _reused_card(service)
    await _set_frozen(service, record["id"], null())
    batch_now = await _repo(service).data_as_of(batch["id"])
    with caplog.at_level(logging.WARNING, logger="ggwork_pick.repository"):
        answers = await _answers(client, service, await _repo(service).result(record["id"]))
    assert answers == {"result": batch_now, "results": batch_now, "replay": batch_now, "detail": batch_now}
    assert batch_now["source_as_of"] == LATER_TEXT
    assert _repository_records(caplog) == []


@pytest.mark.asyncio
async def test_a_malformed_parent_value_is_not_passed_on(app_client, caplog):
    """换一批 carries the parent's frozen value only in the DATA_AS_OF_KEYS shape; another shape falls back to the batch."""
    from ggwork_pick.selection import SelectionService

    _, service = app_client
    batch, parent = await _reused_card(service)
    await _set_frozen(service, parent["id"], {"source_as_of": "坏掉的冻结值"})
    selection = SelectionService(_repo(service))
    with caplog.at_level(logging.WARNING, logger="ggwork_pick.repository"):
        counted = await selection.count({"exclude_previous": True}, parent=await _repo(service).result(parent["id"]))
        child = await selection.query({"exclude_previous": True}, thread_id="t", run_id="r1", call_id="c1", parent_result_id=parent["id"])
    assert counted["data_as_of"] == await _repo(service).data_as_of(batch["id"])
    assert (await _repo(service).result(child["id"]))["data_as_of_json"] is None
    messages = _repository_records(caplog)
    assert any(parent["id"] in message for message in messages) and not any("坏掉的冻结值" in message for message in messages)
