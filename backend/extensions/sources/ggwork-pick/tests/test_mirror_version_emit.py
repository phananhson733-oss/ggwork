"""P4-1: data_as_of.mirror_version, behind the Railway switch PICK_EMIT_MIRROR_VERSION (U17). Synthetic data only.

Sources: plan P4-1 (1726-1741) and 2.5 items 5 and 9; implementation note P4-1 and section 4 item 17. The switch is on
only when the variable is exactly "1"; off, no answer carries the key, so a browser tab still holding the frontend from
before P4-1 keeps parsing results (its data_as_of schema is strict). On, every result reader answers with the version the
result row recorded when it was written (null when that run published without a paired mirror); the count, which saves
no row, answers with the version _scope chose: a derived count (换一批) the parent's, a standalone one this turn's pin.
The key is merged after the frozen data_as_of is read, never stored: stored_data_as_of checks the stored key set.
"""

import json

import pytest
import pytest_asyncio
from mirror_pairs import open_query_service
from test_mirror_frozen import ALICE, _client, _turn
from test_mirror_pin import _old_card_then_new_pair

MIRROR_VERSION = "mirror_version"


@pytest.mark.parametrize(
    ("environ", "emits"),
    [
        ({"PICK_EMIT_MIRROR_VERSION": "1"}, True),
        ({}, False),
        ({"PICK_EMIT_MIRROR_VERSION": ""}, False),
        ({"PICK_EMIT_MIRROR_VERSION": "0"}, False),
        ({"PICK_EMIT_MIRROR_VERSION": " 1"}, False),
        ({"PICK_EMIT_MIRROR_VERSION": "1 "}, False),
        ({"PICK_EMIT_MIRROR_VERSION": "true"}, False),
    ],
)
def test_the_switch_is_exactly_one(environ, emits):
    from ggwork_pick.service import SyncSettings

    assert SyncSettings.from_env(environ).emits_mirror_version is emits


def test_the_switch_is_off_by_default():
    from ggwork_pick.service import PickService, SyncSettings

    assert SyncSettings().emits_mirror_version is False
    assert PickService("unused").sync_settings.emits_mirror_version is False


def test_nothing_to_add_to_without_a_data_as_of():
    """No data_as_of stays null: a bare {"mirror_version": ...} would fail the frontend's strict schema, which needs
    shared, source_as_of and published_at, and with it the thread's whole results list."""
    from ggwork_pick.repository import with_mirror_version

    for version in (3, None):
        assert with_mirror_version(None, version, emit=True) is None
        assert with_mirror_version(None, version, emit=False) is None


def _switch(service, *, on: bool) -> None:
    from ggwork_pick.service import SyncSettings

    service.sync_settings = SyncSettings(mirror_version_flag="1" if on else "")


@pytest_asyncio.fixture
async def world(pg_db_url, tmp_path):
    engine, service, shared, importer = await open_query_service(pg_db_url, tmp_path)
    async with _client(service) as client:
        yield engine, service, shared, importer, client
    await engine.dispose()


def _alice(service):
    from ggwork_pick.repository import PickRepository

    return PickRepository(service.session_factory, "alice")


async def _result_answers(client, service, record: dict) -> dict:
    """data_as_of of one result as each of its readers answers: the two results routes and the detail tool."""
    from ggwork_pick.tools import get_drama_detail_tool

    single = (await client.get(f"/api/pick/results/{record['id']}", headers=ALICE)).json()
    listed = (await client.get("/api/pick/results", params={"thread_id": "t"}, headers=ALICE)).json()["results"]
    runtime = await _turn(service, f"r-detail-{record['id']}", reference=record["id"], call_id="d1")
    item_id = record["ordered_items_json"][0]["item_id"]
    detail = json.loads(await get_drama_detail_tool.coroutine(result_id=record["id"], item_id=item_id, runtime=runtime))
    return {
        "result": single["data_as_of"],
        "results": next(row for row in listed if row["id"] == record["id"])["data_as_of"],
        "detail": detail["data_as_of"],
    }


async def _count(service, run_id: str, filters: dict, reference: str | None = None) -> dict:
    from ggwork_pick.tools import count_candidates_tool

    runtime = await _turn(service, run_id, reference=reference)
    return json.loads(await count_candidates_tool.coroutine(filters=filters, runtime=runtime))["data_as_of"]


async def _query(service, run_id: str) -> tuple[dict, dict]:
    """A new card through the query tool in a new turn: its answer and its stored row."""
    from ggwork_pick.tools import query_candidates_tool

    answer = json.loads(await query_candidates_tool.coroutine(filters={"limit": 1}, runtime=await _turn(service, run_id)))
    return answer, await _alice(service).result(answer["id"])


@pytest.mark.asyncio
async def test_every_result_reader_answers_with_the_rows_version(world):
    from ggwork_pick.repository import DATA_AS_OF_KEYS

    engine, service, shared, importer, client = world
    _switch(service, on=True)
    (version_a, _, card), (version_b, _) = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    record = await _alice(service).result(card["id"])
    answers = await _result_answers(client, service, record)
    frozen = record["data_as_of_json"]
    # The old card answers with the version it recorded, not the current one, beside the value it froze.
    assert answers == dict.fromkeys(("result", "results", "detail"), {**frozen, MIRROR_VERSION: version_a})
    assert tuple(answers["result"]) == (*DATA_AS_OF_KEYS, MIRROR_VERSION) and version_a != version_b
    # A new card this turn stands on the new pair; the stored value never gains the key.
    answer, fresh = await _query(service, "r-new")
    assert answer["data_as_of"] == {**fresh["data_as_of_json"], MIRROR_VERSION: version_b}
    assert tuple(fresh["data_as_of_json"]) == DATA_AS_OF_KEYS and fresh[MIRROR_VERSION] == version_b


@pytest.mark.asyncio
async def test_a_count_answers_with_the_version_its_scope_chose(world):
    """plan P4-1: no card at all, a bound card with a standalone count, and 换一批 on the bound card."""
    engine, service, shared, importer, _ = world
    _switch(service, on=True)
    (version_a, _, card), (version_b, _) = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    parent = await _alice(service).result(card["id"])
    unbound = await _count(service, "r-unbound", {})
    standalone = await _count(service, "r-standalone", {}, reference=card["id"])
    derived = await _count(service, "r-derived", {"exclude_previous": True}, reference=card["id"])
    pin = await _alice(service).current_pin()
    assert (pin.mirror_version, parent[MIRROR_VERSION]) == (version_b, version_a)
    assert unbound == standalone == {**pin.data_as_of, MIRROR_VERSION: version_b}
    assert derived == {**parent["data_as_of_json"], MIRROR_VERSION: version_a}


@pytest.mark.asyncio
async def test_a_run_without_a_paired_mirror_answers_null(world):
    """A degraded publish leaves the run without a version: the key is there, with null (the page shows no link)."""
    engine, service, shared, importer, client = world
    _switch(service, on=True)
    await _old_card_then_new_pair(engine, service, shared, importer, degraded=True)
    answer, record = await _query(service, "r-degraded")
    assert record[MIRROR_VERSION] is None
    assert answer["data_as_of"] == {**record["data_as_of_json"], MIRROR_VERSION: None}
    assert (await _result_answers(client, service, record))["result"][MIRROR_VERSION] is None
    counted = await _count(service, "r-degraded-count", {})
    assert MIRROR_VERSION in counted and counted[MIRROR_VERSION] is None


@pytest.mark.asyncio
async def test_switched_off_no_answer_carries_the_key(world):
    engine, service, shared, importer, client = world
    _switch(service, on=False)
    (_, _, card), _ = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    record = await _alice(service).result(card["id"])
    answers = await _result_answers(client, service, record)
    assert answers == dict.fromkeys(("result", "results", "detail"), record["data_as_of_json"])
    answer, fresh = await _query(service, "r-off")
    assert answer["data_as_of"] == fresh["data_as_of_json"]
    for counted in (await _count(service, "r-off-count", {}), await _count(service, "r-off-derived", {"exclude_previous": True}, reference=card["id"])):
        assert MIRROR_VERSION not in counted
    # The replay keeps its own top-level mirror_version either way; its data_as_of is the frozen value alone.
    replay = (await client.get("/api/pick/replay", params={"result_id": card["id"]}, headers=ALICE)).json()
    assert MIRROR_VERSION not in replay["data_as_of"] and replay[MIRROR_VERSION] == record[MIRROR_VERSION]


@pytest.mark.asyncio
async def test_a_result_with_no_data_as_of_answers_null_with_the_switch_on(world):
    """A result from before P2 froze nothing and falls back to its batch's row. With that row gone as well (no code path
    deletes one: prune_shared keeps it as history, so only a hand cleanup), every reader answers null, not the key alone."""
    from sqlalchemy import text

    engine, service, shared, importer, client = world
    _switch(service, on=True)
    (version_a, pair_a, card), _ = await _old_card_then_new_pair(engine, service, shared, importer, degraded=False)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE ggwp_candidate_sets SET data_as_of_json = NULL WHERE id = :id"), {"id": card["id"]})
        await conn.execute(text("DELETE FROM ggwp_import_batches WHERE id = :id"), {"id": pair_a[0]["id"]})
    record = await _alice(service).result(card["id"])
    assert (record["data_as_of_json"], record["catalog_batch_id"], record[MIRROR_VERSION]) == (None, pair_a[0]["id"], version_a)
    assert await _result_answers(client, service, record) == dict.fromkeys(("result", "results", "detail"), None)
