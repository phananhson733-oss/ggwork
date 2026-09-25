"""Migration 0007 (plan TR-11; design 3.4, 3.5; D12, D13, D23, D26, D27, D34): the observation radar's tables on both dialects.

Both dialects get the 21 tables, three nullable columns on ggwp_candidate_sets and the two runtime rows; PostgreSQL also gets
the eight pick_obs views, whose rows, readers and grants are test_0007_pick_obs.py. Every PostgreSQL half skips when
PICK_TEST_PG_URL is unset. The pan runbook's coverage of the new columns is test_pan_runbook_obs.py.
"""

import pg
import pytest
import pytest_asyncio
import revisions
import sqlalchemy as sa
from engines import host_engine
from obs_schema import (
    CANDIDATE_COLUMNS,
    CANDIDATE_INDEXES,
    OBS_TABLES,
    STAMP,
    assert_0007_in_place,
    columns_of,
    fetch_rows,
    first_column,
    index_names,
    insert,
    obs_table,
    runtime_rows,
    scalar,
    set_version,
    table_names,
    view_names,
)
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ggwork_pick.observe.contract_views import VIEW_COLUMNS, VIEW_NAMES, VIEW_PG_TYPES


@pytest.fixture(params=["sqlite", "postgres"])
def fresh_db_url(request, tmp_path):
    """No tables yet on either dialect, so a test can stop the chain wherever it likes."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    return request.getfixturevalue("empty_pg_url")


@pytest_asyncio.fixture
async def db_url(pick_db_url, tmp_path):
    """Migrated to the head on both dialects: the PostgreSQL copy of the template already is, the SQLite file not yet."""
    await pg.migrate(pick_db_url, tmp_path)
    return pick_db_url


# ---- tables, columns, keys ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0007_builds_the_tables_the_candidate_columns_and_the_runtime_rows(db_url):
    engine = host_engine(db_url)
    try:
        await assert_0007_in_place(engine)
        candidates = await columns_of(engine, "ggwp_candidate_sets")
        # json like every other ggwp JSON column, never jsonb (plan 3.7); the set ids are a set's 32-hex id.
        assert isinstance(candidates["obs_as_of_json"]["type"], sa.JSON)
        assert [candidates[name]["type"].length for name in ("trends_set_id", "gsc_set_id")] == [64, 64]
    finally:
        await engine.dispose()


def _column_level(diff) -> bool:
    return diff[0] in {"add_table", "remove_table", "add_column", "remove_column", "modify_type", "modify_nullable"}


# SQLite's reflection skips the expression index on ggwp_obs_links and says so; indexes are tested on their own below.
@pytest.mark.filterwarnings("ignore:Skipped unsupported reflection of expression-based index")
@pytest.mark.asyncio
async def test_models_match_migration(db_url):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from ggwork_pick.models import metadata

    def compare(sync):
        # Types and nullability of every column of every table; indexes and named constraints are tested on their own below.
        options = {"compare_type": True, "include_name": lambda name, kind, parent: kind != "table" or name != "ggwp_alembic_version"}
        diffs = compare_metadata(MigrationContext.configure(sync, opts=options), metadata)
        flat = [change for diff in diffs for change in (diff if isinstance(diff, list) else [diff])]
        found = inspect(sync)
        keys = {name: found.get_pk_constraint(name)["constrained_columns"] for name in found.get_table_names() if name != "ggwp_alembic_version"}
        return [change for change in flat if _column_level(change)], keys

    engine = host_engine(db_url)
    try:
        async with engine.connect() as conn:
            diffs, keys = await conn.run_sync(compare)
    finally:
        await engine.dispose()
    assert diffs == []
    assert keys == {table.name: [column.name for column in table.primary_key.columns] for table in metadata.sorted_tables}
    assert OBS_TABLES <= set(keys)


@pytest.mark.asyncio
async def test_runtime_seed_rows(fresh_db_url):
    engine = host_engine(fresh_db_url)
    try:
        await revisions.upgrade(engine)
        assert await runtime_rows(engine) == [("gsc", 0), ("trends", 0)]
        # A rerun neither duplicates the rows nor resets one a collector has already written (D34).
        async with engine.begin() as conn:
            await conn.execute(text("update ggwp_obs_runtime set lease_generation = 5, lease_owner = 'run-1' where channel = 'trends'"))
        await set_version(engine, "0006")
        await revisions.upgrade(engine)
        assert await runtime_rows(engine) == [("gsc", 0), ("trends", 5)]
        await revisions.downgrade(engine, "0006")
        assert "ggwp_obs_runtime" not in await table_names(engine)
        await revisions.upgrade(engine)
        assert await runtime_rows(engine) == [("gsc", 0), ("trends", 0)]
        nulls = await fetch_rows(engine, "select lease_owner, lease_until, paused_until, cookie_jar, state_json from ggwp_obs_runtime")
        assert nulls == [(None,) * 5] * 2
        with pytest.raises(IntegrityError, match="ggwp_obs_runtime_channel|CHECK constraint failed"):
            await insert(engine, "ggwp_obs_runtime", channel="bing")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_budget_day_key(db_url):
    # D23: one budget row per channel and budget day; for Trends the day is the target_date, not the UTC day.
    engine = host_engine(db_url)
    try:
        await insert(engine, "ggwp_obs_budget", channel="trends", budget_day="2026-09-25")
        with pytest.raises(IntegrityError, match="ggwp_obs_budget_pkey|UNIQUE constraint failed: ggwp_obs_budget"):
            await insert(engine, "ggwp_obs_budget", channel="trends", budget_day="2026-09-25", requests=3)
        await insert(engine, "ggwp_obs_budget", channel="gsc", budget_day="2026-09-25")
        await insert(engine, "ggwp_obs_budget", channel="trends", budget_day="2026-09-26")
        # The counters start at zero without the writer naming them.
        counters = await fetch_rows(engine, "select requests, breaker_trips, http_429, probe_failures, quota_errors from ggwp_obs_budget")
        assert counters == [(0, 0, 0, 0, 0)] * 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_slice_unique(db_url):
    # Design 5.3: one active version per dataset and PT date; any number of inactive ones beside it.
    engine = host_engine(db_url)
    try:
        await insert(engine, "ggwp_gsc_slices", dataset="C", pt_date="2026-09-24", active=True)
        with pytest.raises(IntegrityError, match="ggwp_gsc_slices_active|UNIQUE constraint failed: ggwp_gsc_slices.dataset, ggwp_gsc_slices.pt_date"):
            await insert(engine, "ggwp_gsc_slices", dataset="C", pt_date="2026-09-24", active=True)
        for _ in range(2):
            await insert(engine, "ggwp_gsc_slices", dataset="C", pt_date="2026-09-24", active=False)
        await insert(engine, "ggwp_gsc_slices", dataset="D", pt_date="2026-09-24", active=True)
        await insert(engine, "ggwp_gsc_slices", dataset="C", pt_date="2026-09-23", active=True)
        # Switching the pointer in one transaction: the old version off, then the new one on.
        async with engine.begin() as conn:
            await conn.execute(text("update ggwp_gsc_slices set active = false where dataset = 'C' and pt_date = '2026-09-24' and active"))
            newest = await conn.execute(text("select max(id) from ggwp_gsc_slices where dataset = 'C' and pt_date = '2026-09-24'"))
            await conn.execute(text("update ggwp_gsc_slices set active = true where id = :i"), {"i": newest.scalar()})
        assert await scalar(engine, "select count(*) from ggwp_gsc_slices where active") == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_trends_batch_unique_per_target_date(db_url):
    # Design 3.2: UNIQUE(target_date) for Trends batches; GSC rounds are never unique by date.
    engine = host_engine(db_url)
    try:
        await insert(engine, "ggwp_obs_batches", id="b1", channel="trends", target_date="2026-09-25")
        with pytest.raises(IntegrityError, match="ggwp_obs_batches_trends_day|UNIQUE constraint failed: ggwp_obs_batches.target_date"):
            await insert(engine, "ggwp_obs_batches", id="b2", channel="trends", target_date="2026-09-25")
        await insert(engine, "ggwp_obs_batches", id="b3", channel="trends", target_date="2026-09-26")
        for batch in ("g1", "g2"):
            await insert(engine, "ggwp_obs_batches", id=batch, channel="gsc", target_date="2026-09-25", round_id=batch)
        # The counters of a new batch start at zero.
        assert await fetch_rows(engine, "select requests, breaker_events from ggwp_obs_batches where id = 'b1'") == [(0, 0)]
    finally:
        await engine.dispose()


LINK = {"link_rules_version": "link-rules-v1", "trends_set_id": "t1", "gsc_set_id": "g1", "identity": "i1", "country": "USA"}
# (table, a row's key values, a second row with the same key) for every natural key 0007 declares.
NATURAL_KEYS = {
    "states": ("ggwp_obs_states", {"set_id": "s1", "identity": "i1", "scope": "US", "window_kind": "H"}, "ggwp_obs_states_row"),
    "links": ("ggwp_obs_links", LINK, "ggwp_obs_links_fact"),
    "links without a country": ("ggwp_obs_links", LINK | {"country": None}, "ggwp_obs_links_fact"),
    "decisions": ("ggwp_obs_decisions", {"owner_id": "alice", "request_id": "r1"}, "ggwp_obs_decision_request"),
    "aliases": ("ggwp_obs_identity_alias", {"alias_version": 3, "old_identity": "a", "new_identity": "b"}, "ggwp_obs_alias_pair"),
    "watch": ("ggwp_obs_watch", {"identity": "i1", "geo": "US", "query_shape": "clear"}, "ggwp_obs_watch_unit"),
    "legacy": ("ggwp_obs_legacy", {"snapshot_id": "snap-1", "raw_url": "https://dramashortstv.com/en?id=38000"}, "ggwp_obs_legacy_pkey"),
    "hourly": ("ggwp_gsc_hourly", {"slice_id": 1, "hour": STAMP, "page": "https://a/", "country": "usa"}, "ggwp_gsc_hourly_pkey"),
    "daily": ("ggwp_gsc_daily", {"slice_id": 1, "page": "https://a/", "country": "usa"}, "ggwp_gsc_daily_pkey"),
    "query daily": ("ggwp_gsc_query_daily", {"slice_id": 1, "page": "https://a/", "query": "q"}, "ggwp_gsc_query_daily_pkey"),
    "sets": ("ggwp_obs_sets", {"id": "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c"}, "ggwp_obs_sets_pkey"),
}


@pytest.mark.parametrize("name, key, constraint", NATURAL_KEYS.values(), ids=NATURAL_KEYS.keys())
@pytest.mark.asyncio
async def test_natural_keys_are_unique(db_url, name, key, constraint):
    engine = host_engine(db_url)
    try:
        await insert(engine, name, **key)
        with pytest.raises(IntegrityError, match=f"{constraint}|UNIQUE constraint failed: {name}"):
            await insert(engine, name, **key)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_link_facts_are_keyed_by_the_link_rules_version(db_url):
    # D13: the same pair judged by a newer link-rules version is a new fact, never an overwrite of the old one.
    engine = host_engine(db_url)
    try:
        await insert(engine, "ggwp_obs_links", **LINK)
        await insert(engine, "ggwp_obs_links", **LINK | {"link_rules_version": "link-rules-v2"})
        await insert(engine, "ggwp_obs_links", **LINK | {"country": "ALL", "trends_geo": "WW"})
        await insert(engine, "ggwp_obs_links", **LINK | {"country": None, "label": "different_markets"})
        assert await scalar(engine, "select count(*) from ggwp_obs_links") == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_trends_rows_carry_the_normalized_title_the_confirm_queue_keys_on(db_url):
    # Correspondence is confirmed per (identity, platform, normalized title) (D24): a Trends row without one never reaches the queue.
    engine = host_engine(db_url)
    try:
        with pytest.raises(IntegrityError, match="ggwp_obs_states_trends_title|CHECK constraint failed"):
            await insert(engine, "ggwp_obs_states", channel="trends", normalized_title=None)
        await insert(engine, "ggwp_obs_states", channel="gsc", normalized_title=None, scope="USA", window_kind="24h")
        await insert(engine, "ggwp_obs_states", channel="trends", normalized_title="the alpha's bride")
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "name, values, constraint",
    [
        ("ggwp_obs_sets", {"channel": "bing"}, "ggwp_obs_sets_channel"),
        ("ggwp_obs_sets", {"mode": "canary"}, "ggwp_obs_sets_mode"),
        ("ggwp_obs_sets", {"status": "draft"}, "ggwp_obs_sets_status"),
        ("ggwp_obs_batches", {"mode": "live "}, "ggwp_obs_batches_mode"),
        ("ggwp_obs_states", {"mode": "Live"}, "ggwp_obs_states_mode"),
        ("ggwp_obs_links", {"mode": "both"}, "ggwp_obs_links_mode"),
        ("ggwp_obs_alerts", {"channel": "trend"}, "ggwp_obs_alerts_channel"),
        ("ggwp_obs_budget", {"channel": "GSC"}, "ggwp_obs_budget_channel"),
    ],
)
@pytest.mark.asyncio
async def test_channel_mode_and_status_take_only_the_contract_values(db_url, name, values, constraint):
    good = {"channel": "gsc", "mode": "shadow", "status": "published"}
    engine = host_engine(db_url)
    try:
        table = obs_table(name)
        await insert(engine, name, **{key: good[key] for key in good if key in table.c} | _distinct(name, 1))
        with pytest.raises(IntegrityError, match=f"{constraint}|CHECK constraint failed"):
            await insert(engine, name, **{key: good[key] for key in good if key in table.c} | values | _distinct(name, 2))
    finally:
        await engine.dispose()


def _distinct(name: str, n: int) -> dict:
    """Key values that keep two rows of these tables apart."""
    return {"ggwp_obs_sets": {"id": f"s{n}"}, "ggwp_obs_batches": {"id": f"b{n}"}, "ggwp_obs_budget": {"budget_day": f"2026-09-2{n}"}}.get(
        name, {"identity": f"i{n}"}
    )


# ---- idempotent reruns and the way down ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_0007_idempotent(db_url):
    # A version table set back to 0006 by hand (realshort-sync.md) finds every table, column, index, row and view in place.
    engine = host_engine(db_url)
    try:
        await insert(engine, "ggwp_obs_sets", id="7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c", channel="trends", mode="shadow", status="published")
        await _insert_candidate(engine, trends_set_id="7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c", obs_as_of_json={"judged_at": STAMP})
        await set_version(engine, "0006")
        await revisions.upgrade(engine)
        await assert_0007_in_place(engine)
        assert await scalar(engine, "select count(*) from ggwp_obs_sets") == 1
        assert await scalar(engine, "select trends_set_id from ggwp_candidate_sets") == "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0007_restores_what_a_partial_run_left_out(db_url):
    # SQLite commits each DDL statement on its own: a crash can leave some of 0007 behind under version 0006.
    engine = host_engine(db_url)
    postgres = engine.dialect.name == "postgresql"
    try:
        async with engine.begin() as conn:
            if postgres:
                await conn.execute(text("drop view pick_obs.alerts"))
            await conn.execute(text("drop table ggwp_gsc_totals"))
            await conn.execute(text("drop index ggwp_obs_batches_trends_day"))
            await conn.execute(text("drop index ggwp_candidate_sets_gsc_set"))
            await conn.execute(text("delete from ggwp_obs_runtime where channel = 'gsc'"))
            await conn.execute(text("update ggwp_alembic_version set version_num = '0006'"))
        await revisions.upgrade(engine)
        await assert_0007_in_place(engine)
        assert "ggwp_obs_batches_trends_day" in await index_names(engine)
    finally:
        await engine.dispose()


CANDIDATE_ROW = {
    "id": "c1",
    "owner_id": "alice",
    "thread_id": "t1",
    "run_id": "r1",
    "tool_call_id": "call-1",
    "catalog_batch_id": "b1",
    "rule_version": "rules-1",
    "ranking_version": "rank-1",
    "conditions_json": {"sort": "obs"},
    "ordered_items_json": [],
    "created_at": STAMP,
}


async def _insert_candidate(engine, **values) -> None:
    async with engine.begin() as conn:
        await conn.execute(obs_table("ggwp_candidate_sets").insert(), CANDIDATE_ROW | values)


@pytest.mark.asyncio
async def test_0007_downgrade_roundtrip(fresh_db_url):
    engine = host_engine(fresh_db_url)
    postgres = engine.dialect.name == "postgresql"
    try:
        await revisions.upgrade(engine)
        await _insert_candidate(engine, gsc_set_id="3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c")
        await revisions.downgrade(engine, "0006")
        assert not OBS_TABLES & await table_names(engine)
        candidates = await columns_of(engine, "ggwp_candidate_sets")
        # Only 0007's own columns go: 0005's mirror columns stay, and SQLite's batch copy keeps the rows and older indexes.
        assert not set(CANDIDATE_COLUMNS) & set(candidates) and {"excluded_json", "mirror_version", "data_as_of_json"} <= set(candidates)
        indexes = await index_names(engine)
        assert not CANDIDATE_INDEXES & indexes and {"ggwp_candidates_owner_thread", "ggwp_candidate_sets_mirror"} <= indexes
        assert await scalar(engine, "select count(*) from ggwp_candidate_sets") == 1
        if postgres:
            # The views go; the schema stays, like pick_mirror under 0006 (U26).
            assert await view_names(engine) == set()
            assert await scalar(engine, "select count(*) from pg_namespace where nspname = 'pick_obs'") == 1
        await revisions.downgrade(engine, "0004")
        await revisions.upgrade(engine)
        await assert_0007_in_place(engine)
        assert await scalar(engine, "select count(*) from ggwp_candidate_sets") == 1
        assert await scalar(engine, "select gsc_set_id from ggwp_candidate_sets") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_views_pg_only(fresh_db_url, tmp_path):
    await pg.migrate(fresh_db_url, tmp_path)
    engine = host_engine(fresh_db_url)
    try:
        if engine.dialect.name == "sqlite":
            assert await fetch_rows(engine, "select name from sqlite_master where type = 'view'") == []
            assert OBS_TABLES <= await table_names(engine)
            return
        assert await view_names(engine) == set(VIEW_NAMES)
        # Only views in pick_obs: the runtime tables (cookie jar, request log, lease) are never exposed there.
        assert await fetch_rows(engine, "select tablename from pg_tables where schemaname = 'pick_obs'") == []
        found = {}
        for view, column, data_type in await fetch_rows(
            engine,
            "select table_name, column_name, data_type from information_schema.columns where table_schema = 'pick_obs' order by table_name, ordinal_position",
        ):
            found.setdefault(view, []).append((column, data_type))
        assert {view: [name for name, _ in columns] for view, columns in found.items()} == {
            view: [column.name for column in columns] for view, columns in VIEW_COLUMNS.items()
        }
        for view, columns in VIEW_COLUMNS.items():
            for column, (name, data_type) in zip(columns, found[view], strict=True):
                assert data_type in VIEW_PG_TYPES[column.type], (view, name, data_type)
        # Views run with their owner's rights (no security_invoker): the reader never needs the ggwp tables themselves.
        options = await first_column(
            engine, "select coalesce(array_to_string(reloptions, ','), '') from pg_class where relnamespace = 'pick_obs'::regnamespace"
        )
        assert options == [""] * len(VIEW_NAMES)
    finally:
        await engine.dispose()
