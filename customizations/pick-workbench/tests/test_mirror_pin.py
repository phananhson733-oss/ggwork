"""P2-5b: one read pins (catalog, knowledge, mirror version) and the data_as_of a result freezes when it is written.

Sources: plan:1576-1585 and brief section 2.5 items 1 and 5. The frontend reads freshness under the v1 key names
(frontend/src/core/pick/format.ts:22-23), so a pinned version's v2 freshness is mapped onto them (U5).
"""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from mirror_pairs import (
    AS_OF,
    AS_OF_TEXT,
    V1_FRESHNESS,
    batch,
    behind,
    building_version,
    catalog_payload,
    degrade,
    fetch,
    now,
    open_service,
    publish_pair,
    stage_pair,
    version,
)
from sqlalchemy import event, text


@pytest_asyncio.fixture
async def pg_world(pg_db_url, tmp_path):
    engine, service, shared, importer = await open_service(pg_db_url, tmp_path)
    yield engine, service, shared, importer
    await engine.dispose()


@pytest_asyncio.fixture
async def world(pick_db_url, tmp_path):
    engine, service, shared, importer = await open_service(pick_db_url, tmp_path)
    yield engine, service, shared, importer
    await engine.dispose()


def _alice(service):
    from ggwork_pick.repository import PickRepository

    return PickRepository(service.session_factory, "alice")


async def _record(service, result_id: str) -> dict:
    return await _alice(service).result(result_id)


@pytest.mark.asyncio
async def test_pin_paired_version(pg_world):
    from ggwork_pick.repository import stamp

    engine, service, shared, importer = pg_world
    version_id, staged = await publish_pair(engine, shared, importer, "a")
    pin = await _alice(service).current_pin()
    assert pin.catalog_id == staged[0]["id"] and pin.knowledge_id == staged[1]["id"]
    assert pin.mirror_version == version_id
    published = (await version(engine, version_id))["published_at"]
    assert pin.data_as_of == {"source_as_of": AS_OF_TEXT, "published_at": stamp(published), "freshness": V1_FRESHNESS, "scope": "scope-a", "shared": True}
    # Paired, the batch carries the very same instant.
    assert (await batch(engine, staged[0]["id"]))["published_at"] == pin.data_as_of["published_at"]


@pytest.mark.asyncio
async def test_pin_none_after_degraded_new_batch(pg_world):
    engine, service, shared, importer = pg_world
    await publish_pair(engine, shared, importer, "a")
    staged = await degrade(shared, importer, "b")
    pin = await _alice(service).current_pin()
    assert pin.catalog_id == staged[0]["id"] and pin.mirror_version is None
    assert pin.data_as_of == await _alice(service).data_as_of(staged[0]["id"])
    assert await behind(engine, shared) is True


@pytest.mark.asyncio
async def test_pin_none_for_personal_import(pg_world):
    from ggwork_pick.imports import Importer

    engine, service, shared, importer = pg_world
    await publish_pair(engine, shared, importer, "a")
    own = await Importer(_alice(service), service.data_dir).catalog(catalog_payload("mine"), "json")
    pin = await _alice(service).current_pin()
    assert pin.catalog_id == own["id"] and pin.mirror_version is None
    assert pin.data_as_of["shared"] is False
    # Everyone else still reads the pair.
    assert (await shared.current_pin()).mirror_version is not None


@pytest.mark.asyncio
async def test_pin_kept_when_degrade_dedupes_to_pair(pg_world):
    engine, service, shared, importer = pg_world
    version_id, staged = await publish_pair(engine, shared, importer, "a")
    again = await degrade(shared, importer, "a")
    assert [item["id"] for item in again] == [item["id"] for item in staged]
    pin = await _alice(service).current_pin()
    assert pin.mirror_version == version_id and pin.data_as_of["source_as_of"] == AS_OF_TEXT
    assert await behind(engine, shared) is False


async def _old_card_then_new_pair(engine, service, shared, importer, *, degraded: bool):
    from ggwork_pick.selection import SelectionService

    version_a, pair_a = await publish_pair(engine, shared, importer, "a")
    card = await SelectionService(_alice(service)).query({"limit": 1}, thread_id="t", run_id="r0", call_id="c0")
    if degraded:
        pair_b, version_b = await degrade(shared, importer, "b"), None
    else:
        version_b, pair_b = await publish_pair(engine, shared, importer, "b")
    return (version_a, pair_a, card), (version_b, pair_b)


@pytest.mark.asyncio
async def test_exclude_previous_use_latest_takes_new(pg_world):
    from ggwork_pick.selection import SelectionService

    engine, service, shared, importer = pg_world
    (version_a, pair_a, card), (version_b, pair_b) = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    assert (await _record(service, card["id"]))["mirror_version"] == version_a
    more = await SelectionService(_alice(service)).query(
        {"exclude_previous": True}, thread_id="t", run_id="r1", call_id="c1", parent_result_id=card["id"], use_latest=True
    )
    record = await _record(service, more["id"])
    assert (record["catalog_batch_id"], record["knowledge_batch_id"], record["mirror_version"]) == (pair_b[0]["id"], pair_b[1]["id"], version_b)
    assert record["data_as_of_json"] == (await _alice(service).current_pin()).data_as_of
    assert record["excluded_json"] == sorted(item["identity"] for item in card["items"])


@pytest.mark.asyncio
async def test_exclude_previous_use_latest_degraded_new_has_no_version(pg_world):
    from ggwork_pick.selection import SelectionService

    engine, service, shared, importer = pg_world
    (_, _, card), (_, pair_b) = await _old_card_then_new_pair(engine, service, shared, importer, degraded=True)
    more = await SelectionService(_alice(service)).query(
        {"exclude_previous": True}, thread_id="t", run_id="r1", call_id="c1", parent_result_id=card["id"], use_latest=True
    )
    record = await _record(service, more["id"])
    assert record["catalog_batch_id"] == pair_b[0]["id"] and record["mirror_version"] is None
    assert record["data_as_of_json"] == await _alice(service).data_as_of(pair_b[0]["id"])


async def _turn(service, card_id: str, run_id: str):
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.context import PickLifecycle

    store = ExtensionData(run_id)
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run_id, run_id, "t", "lead"))
    return SimpleNamespace(context={"user_id": "alice", "pick_reference": {"result_id": card_id}, EXTENSION_TASK_STORE_KEY: store}, tool_call_id="q1")


@pytest.mark.asyncio
async def test_refreshed_turn_then_more(pg_world):
    from ggwork_pick.tools import query_candidates_tool

    engine, service, shared, importer = pg_world
    (_, _, card), (version_b, pair_b) = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    runtime = await _turn(service, card["id"], "r1")
    latest = json.loads(await query_candidates_tool.coroutine(filters={"limit": 1}, runtime=runtime, use_latest=True))
    assert (await _record(service, latest["id"]))["mirror_version"] == version_b
    runtime.tool_call_id = "q2"
    more = json.loads(await query_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    record = await _record(service, more["id"])
    assert record["catalog_batch_id"] == pair_b[0]["id"] and record["mirror_version"] == version_b


@pytest.mark.asyncio
async def test_unrefreshed_more_keeps_parent(pg_world):
    from ggwork_pick.tools import query_candidates_tool

    engine, service, shared, importer = pg_world
    (version_a, pair_a, card), _ = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    parent = await _record(service, card["id"])
    runtime = await _turn(service, card["id"], "r1")
    more = json.loads(await query_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    record = await _record(service, more["id"])
    assert (record["catalog_batch_id"], record["mirror_version"]) == (pair_a[0]["id"], version_a)
    assert record["data_as_of_json"] == parent["data_as_of_json"] is not None


@pytest.mark.asyncio
async def test_count_data_as_of_follows_pin_or_parent(pg_world):
    from ggwork_pick.tools import count_candidates_tool

    engine, service, shared, importer = pg_world
    (_, _, card), _ = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    parent = await _record(service, card["id"])
    runtime = await _turn(service, card["id"], "r1")
    derived = json.loads(await count_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert derived["data_as_of"] == parent["data_as_of_json"]
    standalone = json.loads(await count_candidates_tool.coroutine(filters={}, runtime=runtime))
    assert standalone["data_as_of"] == (await _alice(service).current_pin()).data_as_of != parent["data_as_of_json"]
    # A parent from before the mirror froze nothing: the count falls back to that batch.
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE ggwp_candidate_sets SET data_as_of_json = NULL WHERE id = :id"), {"id": card["id"]})
    fallback = json.loads(await count_candidates_tool.coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert fallback["data_as_of"] == await _alice(service).data_as_of(parent["catalog_batch_id"])


@pytest.mark.asyncio
async def test_frozen_on_write(pg_world):
    from ggwork_pick.selection import SelectionService

    engine, service, shared, importer = pg_world
    version_1, pair = await publish_pair(engine, shared, importer, "a")
    service_alice = SelectionService(_alice(service))
    c1 = await _record(service, (await service_alice.query({}, thread_id="t", run_id="r1", call_id="c1"))["id"])
    assert c1["mirror_version"] == version_1 and c1["data_as_of_json"]["source_as_of"] == AS_OF_TEXT
    # A degraded run dedupes to the same pair and writes its own as_of onto the batches (U10) ...
    later = "2026-09-24T15:38:00.000Z"
    staged = await stage_pair(importer, "a", as_of_text=later)
    await shared.publish_agent_only(batches=staged, reason="degraded:G8", t=now())
    assert (await batch(engine, pair[0]["id"]))["source_as_of"] == later
    # ... while a result written now still carries the pinned version's moment.
    c2 = await _record(service, (await service_alice.query({}, thread_id="t", run_id="r2", call_id="c2"))["id"])
    assert c2["mirror_version"] == version_1 and c2["data_as_of_json"] == c1["data_as_of_json"]
    # The same batches then pair with a new version: earlier results keep what they froze.
    staged = await stage_pair(importer, "a", as_of_text=later)
    version_2, schema = await building_version(engine, as_of=AS_OF.replace(hour=15))
    await shared.publish_mirror_pair(version_id=version_2, schema_name=schema, batches=staged, t=now())
    assert await _record(service, c1["id"]) == c1 and await _record(service, c2["id"]) == c2
    c3 = await _record(service, (await service_alice.query({}, thread_id="t", run_id="r3", call_id="c3"))["id"])
    assert c3["mirror_version"] == version_2 and c3["data_as_of_json"]["source_as_of"] == later


@pytest.mark.asyncio
async def test_sqlite_current_pin_runs(tmp_path):
    from ggwork_pick.pin import Pin

    engine, service, shared, importer = await open_service(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}", tmp_path)
    try:
        assert await _alice(service).current_pin() == Pin(None, None, None, None)
        staged = await stage_pair(importer, "a")
        await shared.publish_agent_only(batches=staged, reason="degraded:G5", t=AS_OF)
        pin = await _alice(service).current_pin()
        assert (pin.catalog_id, pin.knowledge_id, pin.mirror_version) == (staged[0]["id"], staged[1]["id"], None)
        assert isinstance(pin.data_as_of["scope"], str) and pin.data_as_of == await _alice(service).data_as_of(staged[0]["id"])
        # Pin(id, None) still reads like the old pair.
        assert Pin("x", None) == ("x", None, None, None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pin_single_statement(world):
    engine, service, shared, importer = world
    if engine.dialect.name == "postgresql":
        version_id, staged = await publish_pair(engine, shared, importer, "a")
    else:
        staged, version_id = await degrade(shared, importer, "a"), None
    repo = _alice(service)
    await repo.current_pin()  # the first connection's dialect set-up is not the pin's business
    seen: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        pin = await repo.current_pin()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(seen) == 1 and seen[0].lstrip().upper().startswith("SELECT") and "ggwp_import_batches" in seen[0]
    assert (pin.catalog_id, pin.knowledge_id, pin.mirror_version) == (staged[0]["id"], staged[1]["id"], version_id)


@pytest.mark.asyncio
async def test_freshness_keys_for_frontend(pg_world):
    engine, service, shared, importer = pg_world
    await publish_pair(engine, shared, importer, "a")
    freshness = (await _alice(service).current_pin()).data_as_of["freshness"]
    assert {"catalogImportedAt", "reelshortSyncedAt"} <= set(freshness)
    assert freshness == V1_FRESHNESS


@pytest.mark.asyncio
async def test_task_pins_the_triple_once_per_turn(pg_world):
    from ggwork_pick.context import PickTask, task_from_runtime

    engine, service, shared, importer = pg_world
    (_, _, card), _ = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    runtime = await _turn(service, card["id"], "r1")
    version_c, pair_c = await publish_pair(engine, shared, importer, "c")
    task = task_from_runtime(runtime)
    assert isinstance(task, PickTask)
    await task.repository(runtime)
    pinned = task.pin()
    assert pinned == await _alice(service).current_pin()
    assert (pinned.catalog_id, pinned.knowledge_id, pinned.mirror_version) == (pair_c[0]["id"], pair_c[1]["id"], version_c)
    # Later publishes do not move this turn's pin.
    await publish_pair(engine, shared, importer, "d")
    await task.repository(runtime)
    assert task.pin() == pinned != await _alice(service).current_pin()
    rows = await fetch(engine, "SELECT count(*) AS n FROM pick_mirror.versions WHERE status = 'published'")
    assert rows[0]["n"] == 4
