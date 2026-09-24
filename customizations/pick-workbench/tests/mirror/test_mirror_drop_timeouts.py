"""One DROP SCHEMA for a mirror version, whoever drops it: mark_failed (P2-3), retention and the leftover cleanup (P2-6).

Each DROP is its own transaction, BEGIN to COMMIT, every statement sent with a timeout: SET LOCAL lock_timeout and
SET LOCAL statement_timeout to the same budget (lock_timeout alone counts each lock wait on its own and the DROP takes
one lock per table; statement_timeout bounds the DROP as a whole, U24), the DROP, then the versions row that records
it, stamped from the injected clock. 57014, 55P03 and 40P01 mean a reader was in the way: nothing is dropped and the
next try goes again. SET LOCAL goes with the transaction, so the connection keeps its own settings.

PostgreSQL only; skipped when PICK_TEST_PG_URL is unset.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from mirror_rows import T0, Recording, version_args

FINGERPRINT = "0123456789abcdef" * 4
SCHEMA_EXISTS = "select exists (select 1 from pg_namespace where nspname = $1)"
VERSION = "select status, error, dropped_at from pick_mirror.versions where id = $1"
# Session values of the connection's own, set by the test: a SET LOCAL that stayed, or a RESET, would show.
SESSION_LOCK_TIMEOUT, SESSION_STATEMENT_TIMEOUT = "7s", "9s"
BLOCKED = {"QueryCanceledError": "57014", "LockNotAvailableError": "55P03", "DeadlockDetectedError": "40P01"}


def _heads(schema: str, budget: int) -> list[str]:
    return ["BEGIN", f"SET LOCAL lock_timeout = {budget}", f"SET LOCAL statement_timeout = {budget}", f"DROP SCHEMA IF EXISTS {schema} CASCADE"]


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def mirror_conn(dsn):
    """The dedicated connection, with session settings of its own that no DROP may change."""
    from ggwork_pick.mirror.connection import open_dedicated

    conn = await open_dedicated(dsn)
    await conn.execute(f"set lock_timeout = '{SESSION_LOCK_TIMEOUT}'")
    await conn.execute(f"set statement_timeout = '{SESSION_STATEMENT_TIMEOUT}'")
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def observer(dsn):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    yield conn
    await conn.close()


async def _settings(conn) -> tuple[str, str]:
    return await conn.fetchval("show lock_timeout"), await conn.fetchval("show statement_timeout")


class _Watched:
    """The dedicated connection with every execute recorded as (statement, timeout); anything else goes straight through."""

    def __init__(self, conn, *, fail_drop: Exception | None = None):
        self._conn = conn
        self._fail_drop = fail_drop
        self.executed: list[tuple[str, object]] = []

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, query, *args, **kwargs):
        self.executed = [*self.executed, (query, kwargs.get("timeout"))]
        if self._fail_drop is not None and query.startswith("DROP SCHEMA"):
            raise self._fail_drop
        return await self._conn.execute(query, *args, **kwargs)

    def statements(self) -> list[str]:
        return [query for query, _ in self.executed]


# ---- versions.py: mark_failed and drop_version_schema ----


@pytest.mark.asyncio
async def test_mark_failed_bounds_the_lock_waits_and_the_whole_drop_and_stamps_it_inside(mirror_conn, observer):
    from ggwork_pick.mirror import versions

    version = await versions.create_version(mirror_conn, **version_args())
    recording = Recording(mirror_conn)
    assert await versions.mark_failed(recording, version.id, error="G5", clock=lambda: T0)
    drop = [statement for method, statement, _ in recording.calls if method == "execute"]
    assert drop[:4] == _heads(version.schema_name, versions.DROP_STATEMENT_TIMEOUT_MS) and versions.DROP_STATEMENT_TIMEOUT_MS == 5000
    # dropped_at commits with the DROP, not after it: one transaction, like retention's.
    assert drop[4].startswith("UPDATE pick_mirror.versions SET dropped_at") and drop[5:] == ["COMMIT"]
    assert all(timeout for *_, timeout in recording.calls)
    assert await _settings(mirror_conn) == (SESSION_LOCK_TIMEOUT, SESSION_STATEMENT_TIMEOUT)
    assert tuple(await observer.fetchrow(VERSION, version.id)) == ("failed", "G5", T0)
    assert not await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


@pytest.mark.asyncio
async def test_a_drop_given_up_behind_a_reader_leaves_the_connections_own_settings(mirror_conn, observer, monkeypatch):
    from ggwork_pick.mirror import versions

    monkeypatch.setattr(versions, "DROP_STATEMENT_TIMEOUT_MS", 200)
    version = await versions.create_version(mirror_conn, **version_args())
    reader = observer.transaction()
    await reader.start()
    await observer.execute(f"lock table {version.schema_name}.meta in access share mode")
    try:
        # Both limits are 200 ms; whichever PostgreSQL reports first, it is a reader in the way.
        with pytest.raises(versions.MirrorDropBlocked, match="57014|55P03"):
            await versions.drop_version_schema(mirror_conn, version.schema_name)
    finally:
        await reader.rollback()
    assert not mirror_conn.is_in_transaction()
    assert await _settings(mirror_conn) == (SESSION_LOCK_TIMEOUT, SESSION_STATEMENT_TIMEOUT)
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


@pytest.mark.parametrize("error", BLOCKED)
@pytest.mark.asyncio
async def test_every_way_a_reader_ends_the_drop_is_mirror_drop_blocked(mirror_conn, observer, error):
    import asyncpg

    from ggwork_pick.mirror import versions

    version = await versions.create_version(mirror_conn, **version_args())
    watched = _Watched(mirror_conn, fail_drop=getattr(asyncpg.exceptions, error)("synthetic"))
    with pytest.raises(versions.MirrorDropBlocked, match=BLOCKED[error]):
        await versions.mark_failed(watched, version.id, error="G5", clock=lambda: T0)
    assert watched.statements()[-1] == "ROLLBACK" and not mirror_conn.is_in_transaction()
    assert tuple(await observer.fetchrow(VERSION, version.id)) == ("failed", "G5", None)
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


@pytest.mark.asyncio
async def test_any_other_drop_failure_is_a_build_error_not_a_blocked_drop(mirror_conn, observer):
    import asyncpg

    from ggwork_pick.mirror import versions

    version = await versions.create_version(mirror_conn, **version_args())
    watched = _Watched(mirror_conn, fail_drop=asyncpg.exceptions.InsufficientPrivilegeError("synthetic"))
    with pytest.raises(versions.MirrorBuildError, match="42501") as failure:
        await versions.drop_version_schema(watched, version.schema_name)
    assert not isinstance(failure.value, versions.MirrorDropBlocked)
    assert await observer.fetchval(SCHEMA_EXISTS, version.schema_name)


def test_drop_statements_take_a_checked_name_and_a_whole_positive_budget():
    from ggwork_pick.mirror.versions import drop_statements

    assert list(drop_statements("pickm_v000042", budget_ms=750)) == _heads("pickm_v000042", 750)[1:]
    for budget in (0, -1, True, 1.5, "5000"):
        with pytest.raises(ValueError, match="毫秒"):
            drop_statements("pickm_v000042", budget_ms=budget)
    with pytest.raises(ValueError, match="pickm_v"):
        drop_statements("pickm_v000042; select 1", budget_ms=750)


@pytest.mark.asyncio
async def test_mark_failed_refuses_a_naive_clock_before_any_statement():
    from ggwork_pick.mirror.versions import mark_failed

    class _Refuses:
        def is_closed(self):
            return False

        def __getattr__(self, name):
            raise AssertionError(f"no statement may go out: {name}")

    with pytest.raises(ValueError, match="时区"):
        await mark_failed(_Refuses(), 1, error="G5", clock=lambda: T0.replace(tzinfo=None))


# ---- retention.py: prune_versions and clean_leftover_versions ----


async def _published_chain(observer, count: int) -> None:
    """Versions 1..count published an hour apart before T0 - 2 h, each superseded by the next; the last one is current."""
    for n in range(1, count + 1):
        published = T0 - timedelta(hours=2 + count - n)
        superseded = published + timedelta(hours=1) if n < count else None
        await observer.execute(
            "insert into pick_mirror.versions (id, schema_name, status, as_of, fingerprint, created_at, published_at, superseded_at)"
            " values ($1, $2, 'published', $3, $4, $3, $5, $6)",
            n,
            f"pickm_v{n:06d}",
            (published - timedelta(minutes=5)).replace(second=0, microsecond=0),  # as_of is on the minute (0006)
            FINGERPRINT,
            published,
            superseded,
        )
        await observer.execute(f"create schema pickm_v{n:06d}; create table pickm_v{n:06d}.meta (key text primary key, value jsonb not null)")


@pytest_asyncio.fixture
async def sync_conn(mirror_conn):
    from ggwork_pick.mirror.lock import try_mirror_lock

    assert await try_mirror_lock(mirror_conn, now=T0, holder="sync")
    return mirror_conn


def _drops(executed: list[tuple[str, object]]) -> list[list[tuple[str, object]]]:
    """Each DROP's transaction, from its BEGIN to its COMMIT or ROLLBACK."""
    begins = [index for index, (query, _) in enumerate(executed) if query == "BEGIN"]
    ends = [index for index, (query, _) in enumerate(executed) if query in ("COMMIT", "ROLLBACK")]
    return [executed[begin : end + 1] for begin, end in zip(begins, ends, strict=True)]


@pytest.mark.asyncio
async def test_retention_drops_the_same_way_mark_failed_does(sync_conn, observer):
    from ggwork_pick.mirror.retention import prune_versions

    await _published_chain(observer, 5)
    watched = _Watched(sync_conn)
    assert (await prune_versions(watched, clock=lambda: T0)).dropped == (1, 2)
    for number, transaction in zip((1, 2), _drops(watched.executed), strict=True):
        statements = [query for query, _ in transaction]
        assert statements[:4] == _heads(f"pickm_v{number:06d}", 5000)
        assert statements[4].startswith("UPDATE pick_mirror.versions SET status = 'dropped'") and statements[5:] == ["COMMIT"]
        assert all(timeout for _, timeout in transaction)
    assert await _settings(sync_conn) == (SESSION_LOCK_TIMEOUT, SESSION_STATEMENT_TIMEOUT)


@pytest.mark.asyncio
async def test_retention_skips_a_drop_a_reader_ended_and_keeps_the_connections_settings(sync_conn, observer, dsn):
    import asyncpg

    from ggwork_pick.mirror.retention import prune_versions

    await _published_chain(observer, 5)
    reader = await asyncpg.connect(dsn)
    try:
        async with reader.transaction():
            await reader.fetchval("select count(*) from pickm_v000001.meta")
            report = await prune_versions(sync_conn, clock=lambda: T0, lock_timeout=timedelta(milliseconds=200))
    finally:
        await reader.close()
    assert (report.dropped, report.lock_busy) == ((2,), (1,))
    assert await _settings(sync_conn) == (SESSION_LOCK_TIMEOUT, SESSION_STATEMENT_TIMEOUT) and not sync_conn.is_in_transaction()


@pytest.mark.asyncio
async def test_dropped_at_is_the_injected_clock_at_each_drop(sync_conn, observer):
    from ggwork_pick.mirror.retention import clean_leftover_versions, prune_versions

    await _published_chain(observer, 5)
    ticks = iter(T0 + timedelta(seconds=n) for n in range(10))
    assert (await prune_versions(sync_conn, clock=lambda: next(ticks))).dropped == (1, 2)
    assert [row["dropped_at"] for row in await observer.fetch("select dropped_at from pick_mirror.versions where id in (1, 2) order by id")] == [
        T0 + timedelta(seconds=1),
        T0 + timedelta(seconds=2),
    ]
    await observer.execute("update pick_mirror.versions set status = 'failed', dropped_at = null where id = 3")
    ticks = iter(T0 + timedelta(minutes=n) for n in range(10))
    assert (await clean_leftover_versions(sync_conn, clock=lambda: next(ticks))).dropped_schemas == ("pickm_v000003",)
    assert await observer.fetchval("select dropped_at from pick_mirror.versions where id = 3") == T0 + timedelta(minutes=1)


@pytest.mark.parametrize("step", ["prune", "clean"])
@pytest.mark.asyncio
async def test_a_naive_clock_at_drop_time_is_refused_before_that_drop(sync_conn, observer, step):
    from ggwork_pick.mirror.retention import clean_leftover_versions, prune_versions

    await _published_chain(observer, 5)
    await observer.execute("update pick_mirror.versions set status = 'failed' where id = 3")
    ticks = iter([T0, datetime(2026, 9, 24, 3, 41)])  # aware for the lock check, naive at the first drop
    watched = _Watched(sync_conn)
    with pytest.raises(ValueError, match="时区"):
        await (prune_versions if step == "prune" else clean_leftover_versions)(watched, clock=lambda: next(ticks))
    assert not [query for query in watched.statements() if query.startswith("DROP SCHEMA")]
    assert await observer.fetchval("select count(*) from pg_namespace where nspname like 'pickm_v%'") == 5
