"""Writing a mirror version: COPY page by page, the meta rows, keys and indexes after COPY, ANALYZE (P2-3; plan 3.3, 5.2).

PostgreSQL only; skipped when PICK_TEST_PG_URL is unset. Everything runs on the dedicated connection, one autocommitted
step at a time.
"""

import asyncio
import json
import re
import time
import tracemalloc
from datetime import datetime

import pytest
import pytest_asyncio
from engines import host_engine
from mirror_rows import MANIFEST, PRIMARY_KEYS, T0, TABLES, Recording, expected_values, parsed, realshort_row, synthetic_row, version_args
from sqlalchemy import text

from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS

# conftest's guard for statements sent through SQLAlchemy; the bare asyncpg connection needs its own (0.3).
SESSION_SETTING = re.compile(r"(?is)^\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")
# The P2-3 finalize table: primary key, unique constraints, other indexes (plan 3.3, 3.4), written out by hand.
KEYS = {
    "catalog_rows": ("row_key", [], ["(platform, lang)", "(has_signal, latest_evidence_on)", "(title_key)"]),
    "catalog_signals": ("row_key, kind, ord", [], ["(kind, evidence_on)"]),
    "catalog_posted": ("sd", [], []),
    "catalog_accounts": ("id", [], []),
    "rs_rows": ("row_key", ["drama_id"], ["(has_signal, latest_evidence_on)", "(locale)", "(publish_at)"]),
    "rs_ids": ("id", [], ["(canonical_id)"]),
    "rs_clicks14": ("drama_id, day", [], []),
    "rs_bill_orders": ("bill_date, book_id, promotion_type", [], ["(canonical_id, bill_date DESC)"]),
    "meta": ("key", [], []),
}
STATE = "select state from pg_stat_activity where pid = $1"


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def mirror_conn(dsn):
    from ggwork_pick.mirror.connection import open_dedicated

    conn = await open_dedicated(dsn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def observer(dsn):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    yield conn
    await conn.close()


def _rows(resource: str) -> list[dict]:
    """RealShort's own row, one with every nullable column NULL, one with all set: primary keys all distinct."""
    return [realshort_row(resource), synthetic_row(resource, 1, nulls=True), synthetic_row(resource, 2)]


async def _build(conn, **overrides):
    """A version with every table written and finalized, the way a run builds one."""
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import copy_rows, finalize_version, write_meta

    version = await create_version(conn, **version_args(**overrides))
    for resource in TABLES:
        await copy_rows(conn, version.schema_name, resource, parsed(resource, _rows(resource)))
    await write_meta(conn, version.schema_name, MANIFEST)
    await finalize_version(conn, version.schema_name)
    return version


def _decoded(resource: str, record) -> dict:
    values = dict(record)
    for column in RESOURCE_COLUMNS[resource]:
        if column.type == "json":
            values[column.name] = json.loads(values[column.name])
    return values


@pytest.mark.asyncio
async def test_copy_roundtrip_each_resource(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import copy_rows

    version = await create_version(mirror_conn, **version_args())
    for resource in TABLES:
        rows = _rows(resource)
        assert await copy_rows(mirror_conn, version.schema_name, resource, parsed(resource, rows)) == len(rows)
        keys = PRIMARY_KEYS[resource]
        found = {
            tuple(record[key] for key in keys): _decoded(resource, record) for record in await observer.fetch(f"select * from {version.schema_name}.{resource}")
        }
        expected = {tuple(row[key] for key in keys): expected_values(resource, row) for row in rows}
        assert found == expected, resource


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource", "column", "value"),
    [
        ("catalog_rows", "imported_at", datetime(2026, 9, 24, 3, 40)),
        ("catalog_signals", "payload", {"d": "2026-09-01"}),
        ("catalog_posted", "posts", [{"d": "2026-09-01"}]),
    ],
)
async def test_copy_wrong_python_type_fails(mirror_conn, observer, resource, column, value):
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import MirrorRecordError, copy_records, records_for

    version = await create_version(mirror_conn, **version_args())
    records = records_for(resource, parsed(resource, [synthetic_row(resource, index) for index in range(1, 4)]))
    position = [c.name for c in RESOURCE_COLUMNS[resource]].index(column)
    # The last record of the page is the wrong one: nothing of the page may land.
    tampered = [*records[:-1], tuple(value if index == position else item for index, item in enumerate(records[-1]))]
    with pytest.raises(MirrorRecordError) as failure:
        await copy_records(mirror_conn, version.schema_name, resource, tampered)
    assert (failure.value.row, failure.value.column) == (2, column)
    assert await observer.fetchval(f"select count(*) from {version.schema_name}.{resource}") == 0
    assert not mirror_conn.is_in_transaction()


@pytest.mark.asyncio
async def test_copy_memory_bounded(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import copy_rows, records_for

    total, page_size = 10_000, 500
    version = await create_version(mirror_conn, **version_args())

    def page(start: int) -> list[dict]:
        return [synthetic_row("catalog_rows", index, title="标题" * 200) for index in range(start, start + page_size)]

    tracemalloc.start()
    try:
        # One page, parsed and converted: all 10,000 rows at once would cost 20 times this.
        before = tracemalloc.get_traced_memory()[0]
        one = records_for("catalog_rows", parsed("catalog_rows", page(0)))
        page_cost = tracemalloc.get_traced_memory()[1] - before
        del one
        before = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for start in range(0, total, page_size):
            await copy_rows(mirror_conn, version.schema_name, "catalog_rows", parsed("catalog_rows", page(start)))
        paged = tracemalloc.get_traced_memory()[1] - before
    finally:
        tracemalloc.stop()
    assert await observer.fetchval(f"select count(*) from {version.schema_name}.catalog_rows") == total
    assert paged < 3 * page_cost, (paged, page_cost)


@pytest.mark.asyncio
async def test_dedicated_connection_outside_pool(pg_db_url, dsn):
    from ggwork_pick.mirror.connection import APPLICATION_NAME, open_dedicated

    engine = host_engine(pg_db_url)
    try:
        async with engine.connect() as orm:
            checked_out = engine.sync_engine.pool.checkedout()
            conn = await open_dedicated(dsn)
            try:
                await _build(conn)
                assert engine.sync_engine.pool.checkedout() == checked_out
                named = await orm.execute(
                    text("select count(*) from pg_stat_activity where datname = current_database() and application_name = :a"), {"a": APPLICATION_NAME}
                )
                assert named.scalar_one() == 1
            finally:
                await conn.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_indexes_after_finalize(mirror_conn, observer):
    version = await _build(mirror_conn)
    constraints = await observer.fetch(
        "select c.conrelid::regclass::text as tbl, c.contype::text as contype, pg_get_constraintdef(c.oid) as def from pg_constraint c"
        " join pg_namespace n on n.oid = c.connamespace where n.nspname = $1 order by 1, 2, 3",
        version.schema_name,
    )
    indexes = await observer.fetch(
        "select tablename, indexname, indexdef from pg_indexes where schemaname = $1 order by tablename, indexname", version.schema_name
    )
    found = {table: ("", [], []) for table in KEYS}
    for row in constraints:
        table = row["tbl"].split(".")[-1]
        primary, unique, others = found[table]
        if row["contype"] == "p":
            found[table] = (row["def"].removeprefix("PRIMARY KEY (").removesuffix(")"), unique, others)
        elif row["contype"] == "u":
            found[table] = (primary, [*unique, row["def"].removeprefix("UNIQUE (").removesuffix(")")], others)
    constraint_indexes = {f"{table}_pkey" for table in KEYS} | {"rs_rows_drama_id_key"}
    for row in indexes:
        if row["indexname"] in constraint_indexes:
            continue
        assert "UNIQUE" not in row["indexdef"]
        primary, unique, others = found[row["tablename"]]
        found[row["tablename"]] = (primary, unique, sorted([*others, row["indexdef"].split(" USING btree ")[1]]))
    assert found == {table: (primary, unique, sorted(others)) for table, (primary, unique, others) in KEYS.items()}
    assert {row["indexname"] for row in indexes} >= constraint_indexes


@pytest.mark.asyncio
async def test_analyze_ran(mirror_conn, observer):
    version = await _build(mirror_conn)
    query = "select relname, last_analyze from pg_stat_user_tables where schemaname = $1"
    deadline = time.monotonic() + 5
    while True:
        await observer.execute("select pg_stat_clear_snapshot()")
        analyzed = {row["relname"]: row["last_analyze"] for row in await observer.fetch(query, version.schema_name)}
        if (set(analyzed) == {*TABLES, "meta"} and all(analyzed.values())) or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.1)
    assert set(analyzed) == {*TABLES, "meta"}
    assert [table for table, when in analyzed.items() if when is None] == []


@pytest.mark.asyncio
async def test_reader_has_no_usage_before_publish(mirror_conn, observer, pg_reader_role):
    version = await _build(mirror_conn)
    assert not await observer.fetchval("select has_schema_privilege($1, $2, 'USAGE')", pg_reader_role, version.schema_name)
    tables = await observer.fetch(
        "select table_name from information_schema.table_privileges where table_schema = $1 and grantee = $2", version.schema_name, pg_reader_role
    )
    assert tables == []


@pytest.mark.asyncio
async def test_duplicate_primary_key_fails_at_finalize(mirror_conn, observer):
    from ggwork_pick.mirror.versions import MirrorBuildError, create_version, mark_failed
    from ggwork_pick.mirror.writer import copy_rows, finalize_version

    version = await create_version(mirror_conn, **version_args())
    twice = [synthetic_row("catalog_rows", 1, row_key="dup-key-7f3a"), synthetic_row("catalog_rows", 2, row_key="dup-key-7f3a")]
    # COPY takes it: keys come after the data (U4).
    assert await copy_rows(mirror_conn, version.schema_name, "catalog_rows", parsed("catalog_rows", twice)) == 2
    with pytest.raises(MirrorBuildError) as failure:
        await finalize_version(mirror_conn, version.schema_name)
    message = str(failure.value)
    assert "catalog_rows" in message and "23505" in message and "dup-key-7f3a" not in message
    assert not mirror_conn.is_in_transaction()
    # A mirror-side failure: the run fails the version and its schema goes.
    assert await mark_failed(mirror_conn, version.id, error=message, clock=lambda: T0)
    assert not await observer.fetchval("select exists (select 1 from pg_namespace where nspname = $1)", version.schema_name)


@pytest.mark.asyncio
async def test_meta_rows_keep_the_manifest_keys(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import write_meta

    version = await create_version(mirror_conn, **version_args())
    assert await write_meta(mirror_conn, version.schema_name, MANIFEST) == 13
    stored = {row["key"]: json.loads(row["value"]) for row in await observer.fetch(f"select key, value from {version.schema_name}.meta")}
    meta_keys = ("freshness", "rsCounts", "growthBaseline", "sources", "rules", "control", "scrub", "warnings")
    top_keys = ("fingerprint", "sourceRevision", "latestSnapshot", "snapshotDays", "counts")
    assert stored == {**{key: MANIFEST["meta"][key] for key in meta_keys}, **{key: MANIFEST[key] for key in top_keys}}
    # jsonb keeps what RealShort sent: a missing rank kind stays missing, a null stays null (U2).
    assert stored["control"]["rankCounts"] == MANIFEST["meta"]["control"]["rankCounts"] and stored["sourceRevision"] is None


@pytest.mark.asyncio
async def test_no_open_transaction_between_steps(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version, mark_failed
    from ggwork_pick.mirror.writer import copy_rows, finalize_version, write_meta

    pid = await mirror_conn.fetchval("select pg_backend_pid()")

    async def idle() -> None:
        assert not mirror_conn.is_in_transaction()
        assert await observer.fetchval(STATE, pid) == "idle"

    version = await create_version(mirror_conn, **version_args())
    await idle()
    for resource in TABLES:
        await copy_rows(mirror_conn, version.schema_name, resource, parsed(resource, _rows(resource)))
        await idle()
    await write_meta(mirror_conn, version.schema_name, MANIFEST)
    await idle()
    await finalize_version(mirror_conn, version.schema_name)
    await idle()
    await mark_failed(mirror_conn, version.id, error="G8", clock=lambda: T0)
    await idle()


@pytest.mark.asyncio
async def test_every_statement_has_a_timeout_and_no_session_setting(mirror_conn):
    from ggwork_pick.mirror.versions import mark_failed

    recording = Recording(mirror_conn)
    version = await _build(recording)
    await mark_failed(recording, version.id, error="G8", clock=lambda: T0)
    assert recording.calls
    # The two transactions (the tables, the DROP) send BEGIN and COMMIT themselves, each with its timeout too.
    assert [statement for _, statement, _ in recording.calls if statement in ("BEGIN", "COMMIT", "ROLLBACK")] == ["BEGIN", "COMMIT"] * 2
    assert [statement for _, statement, timeout in recording.calls if timeout is None or timeout <= 0] == []
    assert [statement for _, statement, _ in recording.calls if SESSION_SETTING.search(statement)] == []
    assert {method for method, _, _ in recording.calls} <= {"execute", "fetchval", "fetchrow", "copy"}


@pytest.mark.asyncio
async def test_copy_rows_writes_only_its_own_tables(mirror_conn):
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import MirrorRecordError, copy_rows

    version = await create_version(mirror_conn, **version_args())
    recording = Recording(mirror_conn)
    with pytest.raises(ValueError):
        await copy_rows(recording, version.schema_name, "rs_series_day", parsed("rs_series_day", [synthetic_row("rs_series_day", 1)]))
    with pytest.raises(ValueError):
        await copy_rows(recording, version.schema_name, "meta", ())
    with pytest.raises(MirrorRecordError):
        await copy_rows(recording, version.schema_name, "rs_ids", parsed("catalog_rows", [synthetic_row("catalog_rows", 1)]))
    # An empty first page is a valid page: nothing to send.
    assert await copy_rows(recording, version.schema_name, "rs_ids", ()) == 0
    assert recording.calls == []
