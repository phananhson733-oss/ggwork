"""The mirror lock: a session-level advisory lock on the dedicated connection (U13, U14, plan 5.5; critique I.6).

The PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

T0 = datetime(2026, 9, 24, 3, 40, tzinfo=UTC)
SINCE = "select lock_holder_since from pick_mirror.control where id = 1"
ADVISORY = (
    "select pid, classid, objid, objsubid, granted from pg_locks"
    " where locktype = 'advisory' and database = (select oid from pg_database where datname = current_database())"
)
MIRROR_CONNECTIONS = "select count(*) from pg_stat_activity where datname = current_database() and application_name = 'ggwp-mirror'"


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def observer(dsn):
    """A plain connection that watches pg_locks and the control row; it never takes the lock."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    yield conn
    await conn.close()


async def _eventually(check, timeout: float = 5.0) -> None:
    """A closed client's backend exits a moment later; its locks go with it."""
    deadline = time.monotonic() + timeout
    while not await check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def _no_advisory_locks(observer) -> bool:
    return await observer.fetch(ADVISORY) == []


def test_the_key_is_sha256_of_its_own_namespace():
    from ggwork_pick.mirror.lock import MIRROR_LOCK_CLASSID, MIRROR_LOCK_KEY, MIRROR_LOCK_OBJID

    assert MIRROR_LOCK_KEY == int.from_bytes(hashlib.sha256(b"pickm:mirror-sync").digest()[:8], "big", signed=True)
    # Not under "ggwp:": repository._write keys each owner's writes as sha256("ggwp:" + owner) (U13).
    assert MIRROR_LOCK_KEY != int.from_bytes(hashlib.sha256(b"ggwp:mirror-sync").digest()[:8], "big", signed=True)
    assert (MIRROR_LOCK_CLASSID, MIRROR_LOCK_OBJID) == ((MIRROR_LOCK_KEY >> 32) & 0xFFFFFFFF, MIRROR_LOCK_KEY & 0xFFFFFFFF)
    assert MIRROR_LOCK_CLASSID << 32 | MIRROR_LOCK_OBJID == MIRROR_LOCK_KEY % 2**64


@pytest.mark.asyncio
async def test_pg_locks_shows_the_bigint_key_as_its_two_halves_and_objsubid_1(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import MIRROR_LOCK_CLASSID, MIRROR_LOCK_OBJID, try_mirror_lock

    conn = await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(conn, now=T0)
        pid = await conn.fetchval("select pg_backend_pid()")
        assert [tuple(row) for row in await observer.fetch(ADVISORY)] == [(pid, MIRROR_LOCK_CLASSID, MIRROR_LOCK_OBJID, 1, True)]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_second_connection_waits_its_turn_and_the_holder_timestamp_follows_the_lock(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import release_mirror_lock, try_mirror_lock

    first, second = await open_dedicated(dsn), await open_dedicated(dsn)
    try:
        assert await observer.fetchval(SINCE) is None
        assert await try_mirror_lock(first, now=T0)
        assert await observer.fetchval(SINCE) == T0
        assert not await try_mirror_lock(second, now=T0 + timedelta(minutes=1))
        # The loser leaves the holder's timestamp alone.
        assert await observer.fetchval(SINCE) == T0
        # Releasing a lock it does not hold changes nothing either.
        assert not await release_mirror_lock(second)
        assert await observer.fetchval(SINCE) == T0 and len(await observer.fetch(ADVISORY)) == 1
        assert await release_mirror_lock(first)
        assert await _no_advisory_locks(observer)
        assert await observer.fetchval(SINCE) is None
        assert await try_mirror_lock(second, now=T0 + timedelta(minutes=2))
        assert await observer.fetchval(SINCE) == T0 + timedelta(minutes=2)
        assert await release_mirror_lock(second)
        assert await _no_advisory_locks(observer)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_closing_without_unlocking_releases_it(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import lock_status, try_mirror_lock

    holder, next_one = await open_dedicated(dsn), await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(holder, now=T0)
        await holder.close()
        await _eventually(lambda: _no_advisory_locks(observer))
        # Session-level: gone with the session. The timestamp it left says nothing once nobody holds the lock.
        assert await observer.fetchval(SINCE) == T0
        assert await lock_status(observer, now=T0 + timedelta(hours=5)) == {"held": False, "holder_pid": None, "holder_since": None, "stuck": False}
        assert await try_mirror_lock(next_one, now=T0 + timedelta(hours=5))
        assert await observer.fetchval(SINCE) == T0 + timedelta(hours=5)
    finally:
        await next_one.close()


@pytest.mark.asyncio
async def test_stuck_is_judged_by_the_holders_own_timestamp(dsn, observer, pg_db_url):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import LOCK_STUCK_AFTER, lock_status, release_mirror_lock, try_mirror_lock

    assert LOCK_STUCK_AFTER == timedelta(minutes=60)
    holder = await open_dedicated(dsn)
    engine = host_engine(pg_db_url)
    try:
        assert await lock_status(observer, now=T0) == {"held": False, "holder_pid": None, "holder_since": None, "stuck": False}
        assert await try_mirror_lock(holder, now=T0)
        pid = await holder.fetchval("select pg_backend_pid()")
        held = {"held": True, "holder_pid": pid, "holder_since": T0}
        assert await lock_status(observer, now=T0 + timedelta(minutes=60)) == {**held, "stuck": False}
        assert await lock_status(observer, now=T0 + timedelta(minutes=61)) == {**held, "stuck": True}
        assert (await lock_status(observer, now=T0 + timedelta(minutes=11), stuck_after=timedelta(minutes=10)))["stuck"]
        # The /sync route asks through its ORM session.
        async with async_sessionmaker(engine)() as session:
            assert await lock_status(session, now=T0 + timedelta(minutes=61)) == {**held, "stuck": True}
        assert await release_mirror_lock(holder)
        assert await lock_status(observer, now=T0 + timedelta(minutes=61)) == {"held": False, "holder_pid": None, "holder_since": None, "stuck": False}
        # Supavisor hands the next client the same backend (supabase.md): a backend serving for hours that took the lock a
        # minute ago is not stuck. backend_start is not the time of the hold.
        started = await observer.fetchval("select backend_start from pg_stat_activity where pid = $1", pid)
        took = started + timedelta(hours=5)
        assert await try_mirror_lock(holder, now=took)
        assert not (await lock_status(observer, now=took + timedelta(minutes=1)))["stuck"]
    finally:
        await holder.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_try_needs_an_aware_now_and_takes_nothing_without_one(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import lock_status, try_mirror_lock

    conn = await open_dedicated(dsn)
    try:
        with pytest.raises(ValueError, match="时区"):
            await try_mirror_lock(conn, now=T0.replace(tzinfo=None))
        assert await _no_advisory_locks(observer)
        with pytest.raises(ValueError, match="时区"):
            await lock_status(observer, now=T0.replace(tzinfo=None))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_holder_that_cannot_record_its_timestamp_gives_the_lock_back(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    await observer.execute("delete from pick_mirror.control")
    conn = await open_dedicated(dsn)
    try:
        # Held without a timestamp, it could never be judged stuck.
        with pytest.raises(RuntimeError, match="pick_mirror.control"):
            await try_mirror_lock(conn, now=T0)
        assert await _no_advisory_locks(observer)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mirror_lock_yields_the_holder_or_none_and_always_releases_and_closes(dsn, observer):
    from ggwork_pick.mirror.lock import mirror_lock

    async with mirror_lock(dsn, clock=lambda: T0) as holder:
        assert holder is not None and await observer.fetchval(SINCE) == T0
        async with mirror_lock(dsn, clock=lambda: T0 + timedelta(minutes=1)) as loser:
            assert loser is None
        # The loser's exit closed its own connection and left the holder alone.
        assert len(await observer.fetch(ADVISORY)) == 1 and await observer.fetchval(SINCE) == T0
        assert await observer.fetchval(MIRROR_CONNECTIONS) == 1
    assert holder.is_closed()
    assert await _no_advisory_locks(observer) and await observer.fetchval(SINCE) is None
    with pytest.raises(RuntimeError, match="boom"):
        async with mirror_lock(dsn, clock=lambda: T0) as holder:
            raise RuntimeError("boom")
    assert holder.is_closed()
    assert await _no_advisory_locks(observer) and await observer.fetchval(SINCE) is None
    await _eventually(lambda: _count_is(observer, MIRROR_CONNECTIONS, 0))


async def _count_is(observer, query: str, expected: int) -> bool:
    return await observer.fetchval(query) == expected


async def _cancelled_holder(dsn, body, *, cancels: int = 1):
    """Run body(conn) under mirror_lock in a task, cancel it once it is inside, and return the connection it held."""
    from ggwork_pick.mirror.lock import mirror_lock

    inside, held = asyncio.Event(), {}

    async def hold():
        async with mirror_lock(dsn, clock=lambda: T0) as conn:
            held["conn"] = conn
            inside.set()
            await body(conn)

    task = asyncio.create_task(hold())
    await asyncio.wait_for(inside.wait(), 10)
    await asyncio.sleep(0.1)
    for _ in range(cancels):
        task.cancel()
        await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 15)
    return held["conn"]


@pytest.mark.parametrize(
    "body, cancels",
    [
        (lambda conn: asyncio.sleep(3600), 1),
        # Cancelled mid-statement: the connection is busy, releasing on it may fail, closing still ends the session.
        (lambda conn: conn.execute("select pg_sleep(30)"), 1),
        # Cancelled again while it cleans up: the shielded cleanup carries on to the end.
        (lambda conn: asyncio.sleep(3600), 2),
    ],
    ids=["idle", "mid-statement", "cancelled-twice"],
)
@pytest.mark.asyncio
async def test_a_cancelled_holder_still_releases_and_closes(dsn, observer, body, cancels):
    conn = await _cancelled_holder(dsn, body, cancels=cancels)
    await _eventually(lambda: _no_advisory_locks(observer), timeout=10)
    await _eventually(lambda: _count_is(observer, MIRROR_CONNECTIONS, 0), timeout=10)

    async def closed() -> bool:
        return conn.is_closed()

    await _eventually(closed)
