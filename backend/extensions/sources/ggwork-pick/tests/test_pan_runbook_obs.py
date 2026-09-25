"""The weekly pan check's coverage of the observation radar's locations (migration 0007; plan TR-11, D18).

pan-check.sql and pan-redact.sql run as in test_pan_runbook_sql.py, through psql as the deerflow_app stand-in, on a database
at the head or set back to 0006. Every 0007 location holds a hit of its own, so leaving any one out of either script turns a
test red. Skips when PICK_TEST_PG_URL is unset.
"""

import json

import pytest
import revisions
import sqlalchemy as sa
import test_pan_runbook_sql as pan_sql
from engines import host_engine
from pan_runbook import ADDED_BY_0007, CLEARED, cleared_at
from test_pan_runbook_sql import CLEARED_NONE, DELETED, NOTHING, PLACEHOLDER

# The production-like database of the other pan runbook tests: ggwp tables at head, owned by the deerflow_app stand-in.
workbench = pan_sql.workbench
HIT = "提取码 x7k2"
QUERY_HIT = "the alpha's bride pan.baidu.com/s/1AbC"
CLEAN_QUERY = "the alpha's bride full movie"
# A column that keeps two rows of a table apart, for the tables holding more than one location or needing a unique key.
SEPARATE = {
    "ggwp_obs_states": "identity",
    "ggwp_obs_watch": "identity",
    "ggwp_obs_identity_alias": "old_identity",
    "ggwp_obs_batches": "id",
    "ggwp_obs_sets": "id",
    "ggwp_obs_raw": "batch_id",
    "ggwp_obs_discoveries": "seed",
    "ggwp_obs_legacy": "raw_url",
    "ggwp_obs_decisions": "request_id",
    "ggwp_gsc_query_daily": "page",
}
# Columns whose CHECK takes only the contract's values.
CHECKED = {"channel": "gsc", "mode": "shadow", "status": "published"}


def _dummy(column: sa.Column):
    """A clean value of the column's type: nothing in it matches the pan pattern."""
    if column.name in CHECKED:
        return CHECKED[column.name]
    for kind, value in ((sa.JSON, {}), (sa.Boolean, False), (sa.Integer, 1), (sa.Float, 0.5)):
        if isinstance(column.type, kind):
            return value
    return "x"


def _obs_row(table: sa.Table, **values) -> dict:
    """The given values, and a clean dummy for every other NOT NULL column without a default or an autoincrement."""
    keys = list(table.primary_key.columns)
    serial = {keys[0].name} if len(keys) == 1 and isinstance(keys[0].type, sa.Integer) else set()
    required = [column for column in table.columns if not column.nullable and column.server_default is None and column.name not in serial]
    return {column.name: _dummy(column) for column in required if column.name not in values} | values


async def _put(engine, name: str, **values) -> None:
    from ggwork_pick.models import metadata

    table = metadata.tables[name]
    async with engine.begin() as conn:
        if name == "ggwp_obs_runtime":  # 0007 seeds the two rows; a collector only ever updates them
            await conn.execute(table.update().where(table.c.channel == "trends").values(**values))
        else:
            await conn.execute(table.insert(), _obs_row(table, **values))


def _hit_for(location: str):
    """What a 0007 location holds in its own hit row."""
    if location in DELETED:
        return QUERY_HIT
    if location == "ggwp_obs_legacy.hops_json":
        return [{"url": "https://dramashortstv.com/en?pwd=1", "status": 308}]
    return {"note": HIT} if location.endswith("_json") else f"剧名 {HIT}"


def _stored(workbench, location: str) -> list:
    """The location's value in the row that holds its hit (the row whose separating column names the location)."""
    table, column = location.split(".")
    key = SEPARATE.get(table)
    where = f" WHERE {key} = %s" if key else ""
    rows = workbench.fetch(f"SELECT {column} FROM deerflow.{table}{where}", *([location] if key else []))
    return [value for (value,) in rows]


@pytest.mark.asyncio
async def test_every_observation_location_holds_its_own_hit_and_is_cleared_or_kept(workbench):
    engine = host_engine(workbench.url)
    try:
        # One row per location with the hit in that location only; the query table also gets a clean row on the same page.
        for location in ADDED_BY_0007:
            table, column = location.split(".")
            if table != "ggwp_candidate_sets":  # a candidate row is written whole below
                await _put(engine, table, **({SEPARATE[table]: location} if table in SEPARATE else {}), **{column: _hit_for(location)})
        await _put(engine, "ggwp_gsc_query_daily", page="ggwp_gsc_query_daily.query", query=CLEAN_QUERY)
    finally:
        await engine.dispose()
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_candidate_sets (id, owner_id, thread_id, run_id, tool_call_id, catalog_batch_id, rule_version,"
        " ranking_version, conditions_json, ordered_items_json, created_at, obs_as_of_json)"
        " VALUES ('c-obs', 'alice', 't', 'r', 'call', 'b', 'rv', 'obs-v1', '{}', '[]', '2026-09-25T00:00:00.000000+00:00', %s::json) RETURNING id",
        json.dumps({"note": HIT}, ensure_ascii=False),
    )
    assert workbench.check() == (NOTHING | dict.fromkeys(ADDED_BY_0007, 1), [])
    # Everything but the legacy snapshot's hops is cleared: rewritten in place, or for the query, deleted.
    assert workbench.redact() == CLEARED_NONE | {location: 1 for location in ADDED_BY_0007 if location in CLEARED}
    assert workbench.check() == (NOTHING | {"ggwp_obs_legacy.hops_json": 1}, [])
    for location in ADDED_BY_0007:
        table = location.split(".")[0]
        if location in DELETED:
            # The hit's row is gone; the clean query on the same page stays.
            assert _stored(workbench, location) == [CLEAN_QUERY]
        elif location == "ggwp_obs_legacy.hops_json":
            assert _stored(workbench, location) == [_hit_for(location)]
        elif table == "ggwp_obs_runtime":
            assert workbench.fetch("SELECT state_json FROM deerflow.ggwp_obs_runtime ORDER BY channel") == [(None,), ({"note": PLACEHOLDER},)]
        elif table == "ggwp_candidate_sets":
            assert workbench.fetch("SELECT obs_as_of_json FROM deerflow.ggwp_candidate_sets") == [({"note": PLACEHOLDER},)]
        else:
            assert _stored(workbench, location) == [{"note": PLACEHOLDER} if location.endswith("_json") else PLACEHOLDER], location
    assert workbench.redact() == CLEARED_NONE


@pytest.mark.asyncio
async def test_identity_keys_in_observation_json_are_left_alone(workbench):
    # A manual pairing names two identities; an alert its root identity; a set summary the identities it could not cover.
    old, new, root = (json.dumps(["kalostv", f"k-pwd={n}", "en"], separators=(",", ":")) for n in (1, 2, 3))
    engine = host_engine(workbench.url)
    try:
        await _put(
            engine,
            "ggwp_obs_decisions",
            request_id="r-1",
            payload_json={"kind": "alias_pair", "request_id": "r-1", "old_identity": old, "new_identity": new, "note": HIT},
        )
        await _put(engine, "ggwp_obs_alerts", root_identity=root, evidence_json={"root_identity": root, "matched_identity": old, "note": HIT})
        await _put(engine, "ggwp_obs_sets", id="s1", summary_json={"uncovered_units": [{"identity": new, "geo": "US", "reason": "deadline"}], "note": HIT})
    finally:
        await engine.dispose()
    kept = {"ggwp_obs_decisions.payload_json": 1, "ggwp_obs_alerts.evidence_json": 1, "ggwp_obs_sets.summary_json": 1}
    assert workbench.check() == (NOTHING | kept, [])
    assert workbench.redact() == CLEARED_NONE | kept
    # The runbook stops here: the notes are gone, the identities are not the script's to change.
    assert workbench.check() == (NOTHING | kept, [])
    [(payload,)] = workbench.fetch("SELECT payload_json::text FROM deerflow.ggwp_obs_decisions")
    assert json.loads(payload) == {"kind": "alias_pair", "request_id": "r-1", "old_identity": old, "new_identity": new, "note": PLACEHOLDER}
    [(evidence,)] = workbench.fetch("SELECT evidence_json::text FROM deerflow.ggwp_obs_alerts")
    assert json.loads(evidence) == {"root_identity": root, "matched_identity": old, "note": PLACEHOLDER}
    [(summary,)] = workbench.fetch("SELECT summary_json::text FROM deerflow.ggwp_obs_sets")
    assert json.loads(summary)["uncovered_units"][0]["identity"] == new
    assert workbench.redact() == CLEARED_NONE


@pytest.mark.asyncio
async def test_query_daily_redact_deletes(workbench):
    # D18: the query is part of the row's key, so a hit drops the row; the rest of that page's queries stay.
    engine = host_engine(workbench.url)
    try:
        for query in (QUERY_HIT, CLEAN_QUERY, "密码：ab12 在哪"):
            await _put(engine, "ggwp_gsc_query_daily", slice_id=7, pt_date="2026-09-24", page="https://dramashortstv.com/en/drama/x", query=query)
    finally:
        await engine.dispose()
    assert workbench.check()[0]["ggwp_gsc_query_daily.query"] == 2
    assert workbench.redact() == CLEARED_NONE | {"ggwp_gsc_query_daily.query": 2}
    assert workbench.fetch("SELECT query FROM deerflow.ggwp_gsc_query_daily") == [(CLEAN_QUERY,)]
    assert workbench.check() == (NOTHING, [])


@pytest.mark.asyncio
async def test_scripts_run_on_0006_db(workbench):
    # Production is at 0006 until the observation radar ships, while the checkout the weekly check runs from has these
    # scripts already: 0007's locations count as 0 and their lines are left out of the redaction.
    engine = host_engine(workbench.url)
    try:
        await revisions.downgrade(engine, "0006")
    finally:
        await engine.dispose()
    assert workbench.fetch("SELECT version_num FROM deerflow.ggwp_alembic_version") == [("0006",)]
    assert workbench.fetch("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'ggwp_obs%%' OR tablename LIKE 'ggwp_gsc%%'") == [(0,)]
    workbench.fetch(
        "INSERT INTO deerflow.ggwp_selection_commands (owner_id, request_id, payload_hash, receipt_json, created_at)"
        " VALUES ('alice', 'req-old', 'h', %s::json, '2026-09-24T00:00:00.000000+00:00') RETURNING request_id",
        json.dumps({"request_id": "req-old", "note": HIT}, ensure_ascii=False),
    )
    assert workbench.check() == (NOTHING | {"ggwp_selection_commands.receipt_json": 1}, [])
    assert workbench.redact("0006") == dict.fromkeys(cleared_at("0006"), 0) | {"ggwp_selection_commands.receipt_json": 1}
    assert workbench.check() == (NOTHING, [])
