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
    found = {}
    for row in await observer.fetch(COLUMNS, version.schema_name):
        found.setdefault(row["table_name"], []).append((row["column_name"], row["ordinal_position"], row["is_nullable"], row["data_type"], row["udt_name"]))
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
async def test_drop_waits_at_most_its_lock_timeout(mirror_conn, observer, monkeypatch):
    from ggwork_pick.mirror import versions

    monkeypatch.setattr(versions, "DROP_LOCK_TIMEOUT_MS", 200)
    version = await versions.create_version(mirror_conn, **version_args())
    # A reader in the middle of a query holds a lock the DROP must queue behind (U24).
    reader = observer.transaction()
    await reader.start()
    await observer.execute(f"lock table {version.schema_name}.meta in access share mode")
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(versions.MirrorBuildError, match="55P03"):
            await versions.mark_failed(mirror_conn, version.id, error="G5", clock=lambda: T0)
        assert asyncio.get_running_loop().time() - started < 5
    finally:
        await reader.rollback()
    assert not mirror_conn.is_in_transaction()
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "G5", None)
    # The next run's cleanup finishes it; the first reason stays.
    assert await versions.mark_failed(mirror_conn, version.id, error="遗留清理", clock=lambda: T0)
    row = await observer.fetchrow(VERSION, version.id)
    assert (row["status"], row["error"], row["dropped_at"]) == ("failed", "G5", T0)
    assert not await observer.fetchval(SCHEMA_EXISTS, version.schema_name)
