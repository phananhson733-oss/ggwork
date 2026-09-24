"""A mirror version's row in pick_mirror.versions and its pickm_vNNNNNN schema: creation and failure (P2-3; U1, U4-U6, U24).

PostgreSQL only; skipped when PICK_TEST_PG_URL is unset.
"""

import asyncio
import json
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from mirror_rows import AS_OF, MANIFEST, T0, TABLES, version_args

from ggwork_pick.mirror.contracts import FORBIDDEN_NAME, RESOURCE_COLUMNS

# information_schema.columns' data_type for each contract type (U3), and udt_name for the array.
DATA_TYPES = {
    "text": ("text", "text"),
    "day": ("text", "text"),
    "ts": ("timestamp with time zone", "timestamptz"),
    "bool": ("boolean", "bool"),
    "int": ("integer", "int4"),
    "float": ("double precision", "float8"),
    "text[]": ("ARRAY", "_text"),
    "json": ("jsonb", "jsonb"),
}
COLUMNS = (
    "select table_name, column_name, ordinal_position, is_nullable, data_type, udt_name from information_schema.columns"
    " where table_schema = $1 order by table_name, ordinal_position"
)
SCHEMA_EXISTS = "select exists (select 1 from pg_namespace where nspname = $1)"
VERSION = "select * from pick_mirror.versions where id = $1"


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
    """Another session: it sees only what the dedicated connection committed."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    yield conn
    await conn.close()


async def _next_id(conn) -> int:
    last, called = await conn.fetchrow("select last_value, is_called from pick_mirror.versions_id_seq")
    return last + 1 if called else last


@pytest.mark.asyncio
async def test_ddl_matches_contract(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version

    version = await create_version(mirror_conn, **version_args())
    rows = await observer.fetch(COLUMNS, version.schema_name)
    fields = ("column_name", "ordinal_position", "is_nullable", "data_type", "udt_name")
    tables = dict.fromkeys(row["table_name"] for row in rows)
    found = {table: [tuple(row[field] for field in fields) for row in rows if row["table_name"] == table] for table in tables}
    expected = {
        table: [
            (column.name, position, "YES" if column.nullable else "NO", *DATA_TYPES[column.type])
            for position, column in enumerate(RESOURCE_COLUMNS[table], start=1)
        ]
        for table in TABLES
    }
    meta = [("key", 1, "NO", "text", "text"), ("value", 2, "NO", "jsonb", "jsonb")]
    assert found == {**expected, "meta": meta}


@pytest.mark.asyncio
async def test_no_forbidden_columns(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version

    version = await create_version(mirror_conn, **version_args())
    names = [row["column_name"] for row in await observer.fetch(COLUMNS, version.schema_name)]
    assert [name for name in names if FORBIDDEN_NAME.search(name)] == []
    for forbidden in ("pan_url", "pan_pw", "creator", "group_key", "revenue_usd", "promotion_value"):
        assert forbidden not in names
    assert [name for name in names if name.startswith("bill_usd")] == []


@pytest.mark.asyncio
async def test_create_version_writes_a_building_row_and_an_empty_schema(mirror_conn, observer):
    from ggwork_pick.mirror.versions import MirrorVersion, create_version, schema_for

    expected_id = await _next_id(observer)
    version = await create_version(mirror_conn, **version_args())
    assert version == MirrorVersion(id=expected_id, schema_name=schema_for(expected_id))
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["schema_name"], row["status"], row["as_of"], row["fingerprint"]) == (version.schema_name, "building", AS_OF, MANIFEST["fingerprint"])
    assert json.loads(row["counts"]) == MANIFEST["counts"]
    assert json.loads(row["freshness"]) == MANIFEST["meta"]["freshness"]
    assert json.loads(row["warnings"]) == MANIFEST["meta"]["warnings"]
    assert row["latest_snapshot"] == date(2026, 9, 23)
    assert (row["sync_run_id"], row["created_at"]) == ("run-1", T0)
    for column in ("published_at", "superseded_at", "dropped_at", "error", "agent_catalog_batch_id", "agent_knowledge_batch_id"):
        assert row[column] is None
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)
    for table in (*TABLES, "meta"):
        assert await observer.fetchval(f"select count(*) from {version.schema_name}.{table}") == 0


@pytest.mark.asyncio
async def test_create_version_takes_nullable_manifest_values(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version

    first = await create_version(mirror_conn, **version_args(latest_snapshot=None, warnings=[], sync_run_id=None))
    second = await create_version(mirror_conn, **version_args(latest_snapshot=date(2026, 9, 22)))
    assert second.id == first.id + 1
    row = await observer.fetchrow(VERSION, first.id)
    assert (row["latest_snapshot"], json.loads(row["warnings"]), row["sync_run_id"]) == (None, [], None)
    assert (await observer.fetchrow(VERSION, second.id))["latest_snapshot"] == date(2026, 9, 22)


@pytest.mark.asyncio
async def test_mark_failed_drops_schema(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version, mark_failed

    version = await create_version(mirror_conn, **version_args())
    dropped_at = datetime(2026, 9, 24, 3, 55, tzinfo=UTC)
    assert await mark_failed(mirror_conn, version.id, error="镜像闸门 G3 未通过：catalog_rows 行数不符", clock=lambda: dropped_at)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "镜像闸门 G3 未通过：catalog_rows 行数不符", dropped_at)
    assert not await observer.fetchval(SCHEMA_EXISTS, version.schema_name)
    # Done once: a second call finds nothing to fail and changes nothing.
    assert not await mark_failed(mirror_conn, version.id, error="again", clock=lambda: T0)
    again = await observer.fetchrow(VERSION, version.id)
    assert (again["error"], again["dropped_at"]) == (row["error"], dropped_at)


@pytest.mark.asyncio
async def test_mark_failed_leaves_published_and_unknown_versions_alone(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version, mark_failed

    version = await create_version(mirror_conn, **version_args())
    await observer.execute("update pick_mirror.versions set status = 'published', published_at = $2 where id = $1", version.id, T0)
    assert not await mark_failed(mirror_conn, version.id, error="late", clock=lambda: T0)
    assert not await mark_failed(mirror_conn, version.id + 100, error="unknown", clock=lambda: T0)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("published", None, None)
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


@pytest.mark.asyncio
async def test_mark_failed_stores_only_storable_bounded_text(mirror_conn, observer):
    from ggwork_pick.mirror.versions import ERROR_MAX_LENGTH, create_version, mark_failed

    version = await create_version(mirror_conn, **version_args())
    assert await mark_failed(mirror_conn, version.id, error="坏\x00字\ud800" + "长" * 2000, clock=lambda: T0)
    stored = (await observer.fetchrow(VERSION, version.id))["error"]
    assert stored.startswith("坏\ufffd字\ufffd") and len(stored) == ERROR_MAX_LENGTH


@pytest.mark.asyncio
async def test_mark_failed_on_a_closed_connection_leaves_cleanup_to_the_next_run(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.versions import create_version, mark_failed

    conn = await open_dedicated(dsn)
    version = await create_version(conn, **version_args())
    await conn.close()
    assert not await mark_failed(conn, version.id, error="cancelled", clock=lambda: T0)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["dropped_at"]) == ("building", None)


@pytest.mark.asyncio
async def test_a_schema_that_cannot_be_created_fails_its_version(mirror_conn, observer):
    from ggwork_pick.mirror.versions import MirrorBuildError, create_version, schema_for

    # A leftover of the same name (say, versions rebuilt after a downgrade): no versions row names it.
    taken = schema_for(await _next_id(observer))
    await observer.execute(f"create schema {taken}")
    await observer.execute(f"create table {taken}.leftover (secret_value text)")
    await observer.execute(f"insert into {taken}.leftover values ('pw-s3cret')")
    with pytest.raises(MirrorBuildError) as failure:
        await create_version(mirror_conn, **version_args())
    assert "42P06" in str(failure.value) and "pw-s3cret" not in str(failure.value)
    row = await observer.fetchrow("select * from pick_mirror.versions where schema_name = $1", taken)
    assert row["status"] == "failed" and "42P06" in row["error"] and row["dropped_at"] == T0
    assert not mirror_conn.is_in_transaction()


@pytest.mark.asyncio
async def test_drop_waits_at_most_its_budget(mirror_conn, observer, monkeypatch):
    from ggwork_pick.mirror import versions

    monkeypatch.setattr(versions, "DROP_STATEMENT_TIMEOUT_MS", 200)
    version = await versions.create_version(mirror_conn, **version_args())
    session_timeout = await mirror_conn.fetchval("show statement_timeout")
    # A reader in the middle of a query holds a lock the DROP must queue behind (U24).
    reader = observer.transaction()
    await reader.start()
    await observer.execute(f"lock table {version.schema_name}.meta in access share mode")
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(versions.MirrorDropBlocked, match="57014"):
            await versions.mark_failed(mirror_conn, version.id, error="G5", clock=lambda: T0)
        assert asyncio.get_running_loop().time() - started < 5
    finally:
        await reader.rollback()
    assert not mirror_conn.is_in_transaction()
    # SET LOCAL went with the transaction.
    assert await mirror_conn.fetchval("show statement_timeout") == session_timeout
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "G5", None)
    # The next run's cleanup finishes it; the first reason stays.
    assert await versions.mark_failed(mirror_conn, version.id, error="遗留清理", clock=lambda: T0)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "G5", T0)
    assert not await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


@pytest.mark.asyncio
async def test_mark_failed_finishes_a_version_whose_schema_never_came(mirror_conn, observer):
    from ggwork_pick.mirror.versions import create_version, mark_failed

    # A run stopped between the building row and its tables: the row names a schema that is not there.
    version = await create_version(mirror_conn, **version_args())
    await observer.execute(f"drop schema {version.schema_name} cascade")
    assert await mark_failed(mirror_conn, version.id, error="遗留清理", clock=lambda: T0)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "遗留清理", T0)


@pytest.mark.asyncio
async def test_a_failing_table_script_leaves_no_schema_behind(mirror_conn, observer, monkeypatch):
    from ggwork_pick.mirror import versions

    # A valid CREATE TABLE, then one that fails: CREATE SCHEMA and both go back together (P2-3 step 3).
    monkeypatch.setattr(versions, "ddl_template", lambda: "CREATE TABLE __SCHEMA__.a (x text);\nCREATE TABLE __SCHEMA__.a (x text);")
    mark_failed = versions.mark_failed
    schema_at_failure = None

    async def watching(conn, version_id, **kwargs):
        nonlocal schema_at_failure
        schema_at_failure = await observer.fetchval(SCHEMA_EXISTS, versions.schema_for(version_id))
        return await mark_failed(conn, version_id, **kwargs)

    monkeypatch.setattr(versions, "mark_failed", watching)
    with pytest.raises(versions.MirrorBuildError, match="42P07"):
        await versions.create_version(mirror_conn, **version_args())
    assert schema_at_failure is False
    assert not mirror_conn.is_in_transaction()
    row = await observer.fetchrow("select * from pick_mirror.versions order by id desc limit 1")
    assert (row["status"], row["dropped_at"]) == ("failed", T0) and "42P07" in row["error"]
    assert not await observer.fetchval(SCHEMA_EXISTS, row["schema_name"])


@pytest.mark.asyncio
async def test_a_version_that_cannot_be_registered_is_a_mirror_build_error(mirror_conn, observer):
    from ggwork_pick.mirror.versions import MAX_VERSION_ID, MirrorBuildError, create_version, schema_for

    # Another row already holds the schema name the next id makes: the INSERT (step 2) fails on UNIQUE.
    taken = schema_for(await _next_id(observer))
    await observer.execute(
        "insert into pick_mirror.versions (id, schema_name, status, as_of, fingerprint, created_at) values ($1, $2, 'dropped', $3, $4, $5)",
        MAX_VERSION_ID,
        taken,
        AS_OF,
        MANIFEST["fingerprint"],
        T0,
    )
    with pytest.raises(MirrorBuildError) as failure:
        await create_version(mirror_conn, **version_args())
    assert "23505" in str(failure.value) and taken not in str(failure.value)
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert await observer.fetchval("select count(*) from pick_mirror.versions") == 1
    assert not await observer.fetchval(SCHEMA_EXISTS, taken)
    assert not mirror_conn.is_in_transaction()


@pytest.mark.asyncio
async def test_version_ddl_refuses_a_callers_open_transaction(mirror_conn, observer):
    from ggwork_pick.mirror.versions import MirrorBuildError, create_version, drop_version_schema

    version = await create_version(mirror_conn, **version_args())
    # BEGIN inside it would only warn, and the COMMIT that follows would commit the caller's work.
    outer = mirror_conn.transaction()
    await outer.start()
    try:
        with pytest.raises(MirrorBuildError):
            await drop_version_schema(mirror_conn, version.schema_name)
    finally:
        await outer.rollback()
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


WAITING_ON = "select c.relname from pg_locks l join pg_class c on c.oid = l.relation where l.pid = $1 and not l.granted limit 1"


async def _let_through_one_by_one(observer, pid: int, readers: dict, hold: float, drop: asyncio.Task) -> tuple[str, ...]:
    """Each time the DROP queues behind a reader, let that reader finish `hold` seconds later."""
    released: tuple[str, ...] = ()
    while not drop.done():
        waiting = await observer.fetchval(WAITING_ON, pid)
        if waiting in readers and waiting not in released:
            await asyncio.sleep(hold)
            await readers[waiting].rollback()
            released = (*released, waiting)
        else:
            await asyncio.sleep(0.01)
    return released


@pytest.mark.asyncio
async def test_drop_is_bounded_as_a_whole_behind_staggered_readers(mirror_conn, observer, dsn, monkeypatch):
    import asyncpg

    from ggwork_pick.mirror import versions

    budget_ms, hold = 400, 0.25
    monkeypatch.setattr(versions, "DROP_STATEMENT_TIMEOUT_MS", budget_ms)
    version = await versions.create_version(mirror_conn, **version_args())
    # Three readers on three tables: each alone would clear a per-lock limit, together they outlast the budget (U24).
    tables = ("catalog_rows", "rs_rows", "meta")
    connections = [await asyncpg.connect(dsn) for _ in tables]
    readers = {}
    try:
        for table, conn in zip(tables, connections, strict=True):
            reader = conn.transaction()
            await reader.start()
            await conn.execute(f"lock table {version.schema_name}.{table} in access share mode")
            readers = {**readers, table: reader}
        started = asyncio.get_running_loop().time()
        drop = asyncio.create_task(versions.drop_version_schema(mirror_conn, version.schema_name))
        released = await _let_through_one_by_one(observer, mirror_conn.get_server_pid(), readers, hold, drop)
        elapsed = asyncio.get_running_loop().time() - started
        (outcome,) = await asyncio.gather(drop, return_exceptions=True)
        assert elapsed < budget_ms / 1000 + hold, (elapsed, released, outcome)
        assert isinstance(outcome, versions.MirrorDropBlocked) and len(released) < len(tables)
    finally:
        for conn in connections:
            await conn.close()
    assert not mirror_conn.is_in_transaction()
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)
