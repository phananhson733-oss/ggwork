"""Migrations 0005 (both dialects) and 0006 (PostgreSQL only): the mirror's bookkeeping (plan 3.2, 3.7, P2-1).

0005 adds nullable, never backfilled columns to two ggwp tables. 0006 builds pick_mirror's four tables and, when the
reader role exists, grants it SELECT on three of them. The pick_mirror schema itself belongs to the Supabase bootstrap,
so the downgrade leaves it in place (U26, U27). The PostgreSQL halves skip when PICK_TEST_PG_URL is unset.
"""

import json
import logging
from datetime import date
from types import SimpleNamespace

import pg
import pytest
import revisions
import sqlalchemy as sa
from engines import host_engine
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

CANDIDATE_COLUMNS = ("excluded_json", "mirror_version", "data_as_of_json")
MIRROR_INDEX = "ggwp_candidate_sets_mirror"
MIRROR_TABLES = {"versions", "series", "series_state", "control"}
# control stays with the writer: the reader never needs the failure counters (U27).
READABLE = {"versions", "series", "series_state"}
FINGERPRINT = "0123456789abcdef" * 4
CANDIDATE_ROW = {
    "id": "c1",
    "owner_id": "alice",
    "thread_id": "t1",
    "run_id": "r1",
    "tool_call_id": "call-1",
    "request_hash": "h1",
    "parent_result_id": None,
    "catalog_batch_id": "b1",
    "knowledge_batch_id": None,
    "rule_version": "rules-1",
    "ranking_version": "rank-1",
    "conditions_json": '{"sort": "hot"}',
    "ordered_items_json": '["a", "b"]',
    "created_at": "2026-09-24T03:40:00.000000+00:00",
}
RUN_ROW = {"id": "run-1", "source": "realshort", "trigger": "cron", "status": "success", "started_at": "2026-09-24T03:40:00.000000+00:00"}
# information_schema's udt_name and is_nullable for every pick_mirror column (plan 3.2; U1, U5, U12; critique I.6).
MIRROR_COLUMNS = {
    "versions": {
        "id": ("int8", "NO"),
        "schema_name": ("text", "NO"),
        "status": ("text", "NO"),
        "as_of": ("timestamptz", "NO"),
        "fingerprint": ("text", "NO"),
        "counts": ("jsonb", "YES"),
        "latest_snapshot": ("date", "YES"),
        "freshness": ("jsonb", "YES"),
        "warnings": ("jsonb", "YES"),
        "agent_catalog_batch_id": ("text", "YES"),
        "agent_knowledge_batch_id": ("text", "YES"),
        "sync_run_id": ("text", "YES"),
        "created_at": ("timestamptz", "NO"),
        "published_at": ("timestamptz", "YES"),
        "superseded_at": ("timestamptz", "YES"),
        "dropped_at": ("timestamptz", "YES"),
        "error": ("text", "YES"),
    },
    "series": {
        "drama_id": ("text", "NO"),
        "days": ("_date", "NO"),
        "revenue_cents": ("_float8", "NO"),
        "promoters": ("_int4", "NO"),
        "updated_at": ("timestamptz", "NO"),
    },
    "series_state": {
        "id": ("int2", "NO"),
        "through": ("date", "YES"),
        "trimmed_before": ("date", "YES"),
        "updated_at": ("timestamptz", "YES"),
    },
    "control": {
        "id": ("int2", "NO"),
        "accept_empty_once": ("bool", "NO"),
        "accept_empty_set_at": ("timestamptz", "YES"),
        "consecutive_failures": ("int4", "NO"),
        "last_failure_at": ("timestamptz", "YES"),
        "last_failure": ("text", "YES"),
        "lock_holder_since": ("timestamptz", "YES"),
        "lock_holder": ("text", "YES"),
    },
}


@pytest.fixture(params=["sqlite", "postgres"])
def fresh_db_url(request, tmp_path):
    """No tables yet on either dialect, so a test can stop the chain wherever it likes."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    return request.getfixturevalue("empty_pg_url")


async def _inspect(engine, read):
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync: read(inspect(sync)))


async def _columns(engine, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in await _inspect(engine, lambda found: found.get_columns(table))}


async def _indexes(engine, table: str) -> dict[str, list]:
    return {index["name"]: index["column_names"] for index in await _inspect(engine, lambda found: found.get_indexes(table))}


async def _scalar(engine, sql: str, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _tables_in(engine, schema: str) -> set[str]:
    async with engine.connect() as conn:
        return set((await conn.execute(text("select tablename from pg_tables where schemaname = :s"), {"s": schema})).scalars())


async def _schema_exists(engine, schema: str) -> bool:
    return await _scalar(engine, "select count(*) from pg_namespace where nspname = :s", s=schema) == 1


async def _insert(engine, table: str, row: dict) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"insert into {table} ({', '.join(row)}) values ({', '.join(':' + key for key in row)})"), row)


async def _refused(engine, statement: str, params: dict, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with engine.begin() as conn:
            await conn.execute(text(statement), params)


def _decoded(row: dict) -> dict:
    """The host's asyncpg engine hands json columns back decoded even to a text() query; SQLite hands back the text."""
    return {key: json.loads(value) if key.endswith("_json") and isinstance(value, str) else value for key, value in row.items()}


async def _privileges(engine, role: str) -> tuple[bool, dict[str, tuple[bool, bool]]]:
    """USAGE on pick_mirror, and per table (SELECT, any write)."""
    async with engine.connect() as conn:
        usage = (await conn.execute(text("select has_schema_privilege(:r, 'pick_mirror', 'USAGE')"), {"r": role})).scalar_one()
        tables = {}
        for table in sorted(MIRROR_TABLES):
            row = await conn.execute(
                text("select has_table_privilege(:r, :t, 'SELECT'), has_table_privilege(:r, :t, 'INSERT, UPDATE, DELETE, TRUNCATE')"),
                {"r": role, "t": f"pick_mirror.{table}"},
            )
            tables[table] = tuple(row.one())
    return usage, tables


GRANTED = (True, {table: (table in READABLE, False) for table in sorted(MIRROR_TABLES)})


async def _assert_0005_columns(engine) -> None:
    candidates = await _columns(engine, "ggwp_candidate_sets")
    runs = await _columns(engine, "ggwp_sync_runs")
    assert set(CANDIDATE_COLUMNS) <= set(candidates)
    assert "details_json" in runs
    for column in (*(candidates[name] for name in CANDIDATE_COLUMNS), runs["details_json"]):
        assert column["nullable"] is True, column["name"]
    assert isinstance(candidates["mirror_version"]["type"], sa.Integer)
    for column in (candidates["excluded_json"], candidates["data_as_of_json"], runs["details_json"]):
        # json like every other ggwp JSON column, never jsonb: json keeps an escaped NUL (plan 3.7).
        assert isinstance(column["type"], sa.JSON) and not isinstance(column["type"], JSONB), column["name"]
    assert (await _indexes(engine, "ggwp_candidate_sets")).get(MIRROR_INDEX) == ["mirror_version", "created_at"]


@pytest.mark.asyncio
async def test_0005_adds_nullable_columns_and_the_mirror_index(pick_db_url):
    engine = host_engine(pick_db_url)
    try:
        await revisions.upgrade(engine)
        await _assert_0005_columns(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0005_does_not_backfill_existing_rows(fresh_db_url):
    engine = host_engine(fresh_db_url)
    try:
        await revisions.upgrade(engine, "0004")
        await _insert(engine, "ggwp_candidate_sets", CANDIDATE_ROW)
        await _insert(engine, "ggwp_sync_runs", RUN_ROW)
        await revisions.upgrade(engine)
        async with engine.connect() as conn:
            candidate = (await conn.execute(text("select * from ggwp_candidate_sets"))).mappings().one()
            run = (await conn.execute(text("select * from ggwp_sync_runs"))).mappings().one()
        assert _decoded({key: candidate[key] for key in CANDIDATE_ROW}) == _decoded(CANDIDATE_ROW)
        assert [candidate[name] for name in CANDIDATE_COLUMNS] == [None, None, None]
        assert {key: run[key] for key in RUN_ROW} == RUN_ROW
        assert run["details_json"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_head_to_0004_and_back_removes_and_restores_what_0005_and_0006_add(fresh_db_url):
    engine = host_engine(fresh_db_url)
    postgres = engine.dialect.name == "postgresql"
    try:
        await revisions.upgrade(engine)
        await _insert(engine, "ggwp_candidate_sets", CANDIDATE_ROW)
        await revisions.downgrade(engine, "0004")
        assert not set(CANDIDATE_COLUMNS) & set(await _columns(engine, "ggwp_candidate_sets"))
        assert "details_json" not in await _columns(engine, "ggwp_sync_runs")
        indexes = await _indexes(engine, "ggwp_candidate_sets")
        assert MIRROR_INDEX not in indexes
        # SQLite drops a column by copying the table: the rows and the older index come along.
        assert "ggwp_candidates_owner_thread" in indexes
        assert await _scalar(engine, "select count(*) from ggwp_candidate_sets") == 1
        if postgres:
            assert await _tables_in(engine, "pick_mirror") == set()
            assert await _schema_exists(engine, "pick_mirror")
        await revisions.upgrade(engine)
        await _assert_0005_columns(engine)
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
        if postgres:
            assert await _tables_in(engine, "pick_mirror") == MIRROR_TABLES
            assert await _scalar(engine, "select count(*) from pick_mirror.control") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0005_retried_after_a_partial_ddl_completes(tmp_path):
    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    try:
        await revisions.upgrade(engine, "0004")
        # SQLite commits each DDL statement on its own: a crash can leave part of 0005 behind under version 0004.
        async with engine.begin() as conn:
            await conn.execute(text("alter table ggwp_candidate_sets add column excluded_json JSON"))
            await conn.execute(text("alter table ggwp_candidate_sets add column mirror_version INTEGER"))
            await conn.execute(text(f"create index {MIRROR_INDEX} on ggwp_candidate_sets (mirror_version, created_at)"))
        await revisions.upgrade(engine)
        await _assert_0005_columns(engine)
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_version_table_reset_to_0004_upgrades_over_what_is_already_there(pick_db_url, tmp_path):
    # Rolling the code back below 0005 means setting the version table back by hand (realshort-sync.md); going
    # forward again then finds every column, index, table and row of 0005 and 0006 already in place.
    await pg.migrate(pick_db_url, tmp_path)
    engine = host_engine(pick_db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("update ggwp_alembic_version set version_num = '0004'"))
        await pg.migrate(pick_db_url, tmp_path)
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
        await _assert_0005_columns(engine)
        if engine.dialect.name == "postgresql":
            assert await _tables_in(engine, "pick_mirror") == MIRROR_TABLES
            assert await _scalar(engine, "select count(*) from pick_mirror.control") == 1
            assert await _scalar(engine, "select count(*) from pick_mirror.series_state") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_gets_the_columns_but_nothing_of_pick_mirror(tmp_path, monkeypatch):
    # 0006 returns before it reads the reader role on SQLite: not even a malformed name stops the upgrade.
    monkeypatch.setenv(pg.READER_ROLE_ENV, "Not A Role")
    url = f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    await pg.migrate(url, tmp_path)
    engine = host_engine(url)
    try:
        names = await _inspect(engine, lambda found: found.get_table_names())
        assert not MIRROR_TABLES & set(names)
        assert all(name.startswith("ggwp_") for name in names)
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
        await _assert_0005_columns(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_models_match_the_migrated_columns(pick_db_url, tmp_path):
    from ggwork_pick.models import metadata

    await pg.migrate(pick_db_url, tmp_path)
    engine = host_engine(pick_db_url)
    try:
        migrated = await _inspect(
            engine,
            lambda found: {name: {column["name"] for column in found.get_columns(name)} for name in found.get_table_names() if name != "ggwp_alembic_version"},
        )
    finally:
        await engine.dispose()
    assert migrated == {table.name: set(table.columns.keys()) for table in metadata.sorted_tables}


@pytest.mark.asyncio
async def test_pick_mirror_tables_columns_and_indexes(pg_db_url):
    engine = host_engine(pg_db_url)
    try:
        async with engine.connect() as conn:
            columns = await conn.execute(
                text("select table_name, column_name, udt_name, is_nullable from information_schema.columns where table_schema = 'pick_mirror'")
            )
            found = {}
            for table, column, udt, nullable in columns:
                found.setdefault(table, {})[column] = (udt, nullable)
            definitions = await conn.execute(text("select indexname, indexdef from pg_indexes where tablename = 'versions' and schemaname = 'pick_mirror'"))
            indexes = dict(definitions.all())
        assert found == MIRROR_COLUMNS
        assert "(status, published_at DESC)" in indexes["pick_mirror_versions_current"]
        assert "(agent_catalog_batch_id, published_at)" in indexes["pick_mirror_versions_catalog"]
    finally:
        await engine.dispose()


def _version(schema_name: str = "pickm_v000001", status: str = "building", fingerprint: str = FINGERPRINT) -> dict:
    return {"schema_name": schema_name, "status": status, "fingerprint": fingerprint}


async def _insert_version(engine, version: dict) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into pick_mirror.versions (schema_name, status, as_of, fingerprint, created_at)"
                " values (:schema_name, :status, now(), :fingerprint, now())"
            ),
            version,
        )


REFUSED_VERSIONS = {
    "duplicate schema name": (_version(), "versions_schema_name_key"),
    "short schema name": (_version("pickm_v1"), "pick_mirror_versions_schema_name"),
    "seven digits": (_version("pickm_v0000001"), "pick_mirror_versions_schema_name"),
    "not a version schema": (_version("deerflow"), "pick_mirror_versions_schema_name"),
    "unknown status": (_version("pickm_v000002", status="live"), "pick_mirror_versions_status"),
    "upper-case fingerprint": (_version("pickm_v000003", fingerprint=FINGERPRINT.upper()), "pick_mirror_versions_fingerprint"),
    "short fingerprint": (_version("pickm_v000004", fingerprint=FINGERPRINT[:-1]), "pick_mirror_versions_fingerprint"),
    # The plan's jsonb would have stored the string with its quotes (U1).
    "quoted fingerprint": (_version("pickm_v000005", fingerprint=f'"{FINGERPRINT[:62]}"'), "pick_mirror_versions_fingerprint"),
}


@pytest.mark.parametrize("version, constraint", REFUSED_VERSIONS.values(), ids=REFUSED_VERSIONS.keys())
@pytest.mark.asyncio
async def test_pick_mirror_versions_refuse_a_bad_row(pg_db_url, version, constraint):
    engine = host_engine(pg_db_url)
    try:
        await _insert_version(engine, _version())
        with pytest.raises(IntegrityError, match=constraint):
            await _insert_version(engine, version)
        for number, status in enumerate(("published", "failed", "dropped"), 2):
            await _insert_version(engine, _version(f"pickm_v{number:06d}", status=status))
        assert await _scalar(engine, "select count(*) from pick_mirror.versions") == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pick_mirror_series_arrays_stay_aligned(pg_db_url):
    series = "insert into pick_mirror.series (drama_id, days, revenue_cents, promoters, updated_at) values (:d, :days, :cents, :promoters, now())"
    days = [date(2026, 9, 22), date(2026, 9, 23)]
    engine = host_engine(pg_db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(series), {"d": "d1", "days": days, "cents": [1.5, 2.0], "promoters": [1, 2]})
        for cents, promoters in (([1.5], [1, 2]), ([1.5, 2.0], [1, 2, 3])):
            await _refused(engine, series, {"d": "d2", "days": days, "cents": cents, "promoters": promoters}, "pick_mirror_series_aligned")
        assert await _scalar(engine, "select count(*) from pick_mirror.series") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_control_and_series_state_hold_exactly_one_row(pg_db_url):
    engine = host_engine(pg_db_url)
    try:
        async with engine.connect() as conn:
            control = (await conn.execute(text("select * from pick_mirror.control"))).mappings().all()
            state = (await conn.execute(text("select * from pick_mirror.series_state"))).mappings().all()
        assert [dict(row) for row in control] == [
            {
                "id": 1,
                "accept_empty_once": False,
                "accept_empty_set_at": None,
                "consecutive_failures": 0,
                "last_failure_at": None,
                "last_failure": None,
                "lock_holder_since": None,
                "lock_holder": None,
            }
        ]
        assert [dict(row) for row in state] == [{"id": 1, "through": None, "trimmed_before": None, "updated_at": None}]
        refusals = [(table, 1, f"{table}_pkey") for table in ("control", "series_state")]
        refusals += [(table, 2, f"pick_mirror_{table}_singleton") for table in ("control", "series_state")]
        for table, row_id, constraint in refusals:
            await _refused(engine, f"insert into pick_mirror.{table} (id) values (:i)", {"i": row_id}, constraint)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_control_lock_holder_takes_only_the_three_holders(pg_db_url):
    # The sync, the backfill and the cleanup command are the only ones that take the mirror lock (U14).
    update = "update pick_mirror.control set lock_holder = :h where id = 1"
    engine = host_engine(pg_db_url)
    try:
        for holder in ("sync", "backfill", "cleanup", None):
            async with engine.begin() as conn:
                await conn.execute(text(update), {"h": holder})
            assert await _scalar(engine, "select lock_holder from pick_mirror.control") == holder
        for holder in ("Sync", "admin", "", "sync "):
            await _refused(engine, update, {"h": holder}, "pick_mirror_control_lock_holder")
        assert await _scalar(engine, "select lock_holder from pick_mirror.control") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_control_table_from_before_lock_holder_gets_it_when_0006_runs_again(pg_db_url):
    # lock_holder was added by editing 0006 in place before any deployment. A local database migrated by the earlier
    # 0006 and set back to 0005 by hand finds its control table already there: CREATE TABLE IF NOT EXISTS alone would
    # leave it without the column, and every try_mirror_lock there would fail.
    engine = host_engine(pg_db_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("alter table pick_mirror.control drop column lock_holder"))
            await conn.execute(text("update ggwp_alembic_version set version_num = '0005'"))
        await revisions.upgrade(engine)
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
        assert await _scalar(engine, "select lock_holder from pick_mirror.control where id = 1") is None
        await _refused(engine, "update pick_mirror.control set lock_holder = :h where id = 1", {"h": "admin"}, "pick_mirror_control_lock_holder")
        async with engine.begin() as conn:
            await conn.execute(text("update pick_mirror.control set lock_holder = 'sync' where id = 1"))
        assert await _scalar(engine, "select lock_holder from pick_mirror.control where id = 1") == "sync"
    finally:
        await engine.dispose()


def test_the_0006_lock_holders_are_the_ones_the_lock_takes():
    # The migration imports nothing from ggwork_pick, so the list is written twice; this keeps the two equal.
    from ggwork_pick.mirror.lock import LOCK_HOLDERS

    assert _migration("0006").LOCK_HOLDERS == LOCK_HOLDERS == ("sync", "backfill", "cleanup")


@pytest.mark.asyncio
async def test_a_restart_is_a_no_op_and_the_mirror_tables_live_in_pick_mirror_only(empty_pg_url, tmp_path):
    for _ in range(2):
        await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    try:
        assert await _tables_in(engine, "pick_mirror") == MIRROR_TABLES
        assert not MIRROR_TABLES & await _tables_in(engine, pg.SCHEMA)
        assert await _scalar(engine, "select count(*) from pick_mirror.control") == 1
        assert await _scalar(engine, "select count(*) from pick_mirror.series_state") == 1
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_reader_role_gets_usage_and_select_on_three_tables_when_it_exists(pg_reader_role, empty_pg_url, tmp_path):
    # pg_reader_role comes first so it is dropped after this database and the grants it holds here.
    await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    try:
        assert await _privileges(engine, pg_reader_role) == GRANTED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_applying_0006_again_grants_a_reader_younger_than_the_template(pg_db_url, pg_reader_role):
    # The session's template was migrated before this test created its role, so the copy starts without the grants.
    engine = host_engine(pg_db_url)
    try:
        usage, tables = await _privileges(engine, pg_reader_role)
        assert not usage and not any(select for select, _ in tables.values())
        await revisions.downgrade(engine, "0005")
        await revisions.upgrade(engine)
        assert await _privileges(engine, pg_reader_role) == GRANTED
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["pick_board_reader_absent_4f1c9e", ""])
@pytest.mark.asyncio
async def test_a_missing_reader_role_is_skipped_without_failing(empty_pg_url, tmp_path, monkeypatch, caplog, role):
    # "" falls back to the default name, which this cluster does not have either.
    monkeypatch.setenv(pg.READER_ROLE_ENV, role)
    with caplog.at_level(logging.WARNING, logger="ggwork_pick.migrations"):
        await pg.migrate(empty_pg_url, tmp_path)
    # Production has the role: a skipped grant is worth a line in the gateway log, under a name and prefix one can filter on.
    skipped = [record for record in caplog.records if pg.READER_ROLE_ENV in record.getMessage()]
    assert [(record.name, record.getMessage().startswith("[pick-mirror] ")) for record in skipped] == [("ggwork_pick.migrations", True)]
    engine = host_engine(empty_pg_url)
    try:
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head()
        assert await _tables_in(engine, "pick_mirror") == MIRROR_TABLES
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["Pick_Reader", "1reader", "pick-reader", 'reader"; drop schema deerflow cascade; --', "r" * 64, " pick_board_reader"])
@pytest.mark.asyncio
async def test_a_malformed_reader_role_name_fails_the_whole_migration(empty_pg_url, tmp_path, monkeypatch, role):
    monkeypatch.setenv(pg.READER_ROLE_ENV, role)
    with pytest.raises(ValueError, match=pg.READER_ROLE_ENV) as failure:
        await pg.migrate(empty_pg_url, tmp_path)
    assert role.strip() not in str(failure.value)
    engine = host_engine(empty_pg_url)
    try:
        # One transaction from 0001 to 0006: nothing of it stays behind.
        assert await _scalar(engine, "select count(*) from pg_tables where tablename like 'ggwp%'") == 0
        assert not await _schema_exists(engine, "pick_mirror")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_0006_downgrade_drops_registered_version_schemas_and_keeps_pick_mirror(pg_db_url):
    engine = host_engine(pg_db_url)
    try:
        await _insert_version(engine, _version("pickm_v000001", status="published"))
        # Dropped long ago: registered, but its schema is gone already.
        await _insert_version(engine, _version("pickm_v000002", status="dropped"))
        async with engine.begin() as conn:
            await conn.execute(text("create schema pickm_v000001"))
            await conn.execute(text("create table pickm_v000001.meta (key text primary key)"))
            await conn.execute(text("create schema unrelated_keep"))
        await revisions.downgrade(engine, "0005")
        assert not await _schema_exists(engine, "pickm_v000001")
        assert await _schema_exists(engine, "unrelated_keep")
        assert await _schema_exists(engine, "pick_mirror")
        assert await _tables_in(engine, "pick_mirror") == set()
        assert await _scalar(engine, "select version_num from ggwp_alembic_version") == "0005"
        await revisions.upgrade(engine)
        assert await _tables_in(engine, "pick_mirror") == MIRROR_TABLES
        assert await _scalar(engine, "select count(*) from pick_mirror.versions") == 0
    finally:
        await engine.dispose()


def _migration(revision: str):
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(revisions.config()).get_revision(revision).module


class _Bind:
    """A PostgreSQL bind answering each query, in order, with the given rows."""

    def __init__(self, *answers: list[tuple]):
        self.dialect = postgresql.dialect()
        self._answers = iter(answers)

    def execute(self, statement, params=None):
        rows = next(self._answers)
        return SimpleNamespace(
            first=lambda: rows[0] if rows else None,
            scalar=lambda: rows[0][0] if rows else None,
            scalars=lambda: SimpleNamespace(all=lambda: [row[0] for row in rows]),
        )


# Reserved words pass the name check; unquoted, "current_user" would even grant the migrating role itself.
@pytest.mark.parametrize("role", ["select", "user", "window", "current_user"])
def test_a_reader_role_named_like_a_keyword_is_quoted_in_the_grants(monkeypatch, role):
    migration = _migration("0006")
    executed = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=executed.append))
    monkeypatch.setenv(pg.READER_ROLE_ENV, role)
    migration._grant_reader(_Bind([(1,)]), migration._reader_role())
    readable = "pick_mirror.versions, pick_mirror.series, pick_mirror.series_state"
    assert executed == [f'GRANT USAGE ON SCHEMA pick_mirror TO "{role}"', f'GRANT SELECT ON {readable} TO "{role}"']


def test_the_downgrade_only_ever_drops_schemas_of_the_writers_own_shape():
    # The CHECK on versions already refuses other names; this is the second fence, for a table whose check was dropped.
    migration = _migration("0006")
    names = ["pickm_v000001", "deerflow", "pickm_v1", "pickm_v0000001", "PICKM_V000002", "pickm_v000002; drop", "public", "pickm_v000003"]
    assert migration._registered_version_schemas(_Bind([("pick_mirror.versions",)], [(name,) for name in names])) == ["pickm_v000001", "pickm_v000003"]
    assert migration._registered_version_schemas(_Bind([(None,)])) == []
