"""The mirror lock: a session-level advisory lock on the dedicated connection (U13, U14, plan 5.5; critique I.6).

The PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import pg
import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

T0 = datetime(2026, 9, 24, 3, 40, tzinfo=UTC)
SINCE = "select lock_holder_since from pick_mirror.control where id = 1"
HOLDER = "select lock_holder, lock_holder_since from pick_mirror.control where id = 1"
ADVISORY = (
    "select pid, classid, objid, objsubid, granted from pg_locks"
    " where locktype = 'advisory' and database = (select oid from pg_database where datname = current_database())"
)
MIRROR_CONNECTIONS = "select count(*) from pg_stat_activity where datname = current_database() and application_name = 'ggwp-mirror'"
NOT_HELD = {"held": False, "holder_pid": None, "holder": None, "holder_since": None, "stuck": False}


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
        assert await try_mirror_lock(conn, now=T0, holder="sync")
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
        assert tuple(await observer.fetchrow(HOLDER)) == (None, None)
        assert await try_mirror_lock(first, now=T0, holder="sync")
        assert tuple(await observer.fetchrow(HOLDER)) == ("sync", T0)
        assert not await try_mirror_lock(second, now=T0 + timedelta(minutes=1), holder="backfill")
        # The loser leaves the holder's name and timestamp alone.
        assert tuple(await observer.fetchrow(HOLDER)) == ("sync", T0)
        # Releasing a lock it does not hold changes nothing either.
        assert not await release_mirror_lock(second)
        assert tuple(await observer.fetchrow(HOLDER)) == ("sync", T0) and len(await observer.fetch(ADVISORY)) == 1
        assert await release_mirror_lock(first)
        assert await _no_advisory_locks(observer)
        assert tuple(await observer.fetchrow(HOLDER)) == (None, None)
        assert await try_mirror_lock(second, now=T0 + timedelta(minutes=2), holder="cleanup")
        assert tuple(await observer.fetchrow(HOLDER)) == ("cleanup", T0 + timedelta(minutes=2))
        assert await release_mirror_lock(second)
        assert await _no_advisory_locks(observer)
        assert tuple(await observer.fetchrow(HOLDER)) == (None, None)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_closing_without_unlocking_releases_it(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import lock_status, try_mirror_lock

    holder, next_one = await open_dedicated(dsn), await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(holder, now=T0, holder="sync")
        await holder.close()
        await _eventually(lambda: _no_advisory_locks(observer))
        # Session-level: gone with the session. The timestamp it left says nothing once nobody holds the lock.
        assert await observer.fetchval(SINCE) == T0
        assert await lock_status(observer, now=T0 + timedelta(hours=5)) == NOT_HELD
        # Another holder takes over the row the dead one left: both columns are its own, never the dead holder's name.
        assert await try_mirror_lock(next_one, now=T0 + timedelta(hours=5), holder="backfill")
        assert tuple(await observer.fetchrow(HOLDER)) == ("backfill", T0 + timedelta(hours=5))
        status = await lock_status(observer, now=T0 + timedelta(hours=5))
        assert (status["holder"], status["holder_since"], status["stuck"]) == ("backfill", T0 + timedelta(hours=5), False)
    finally:
        await next_one.close()


@pytest.mark.asyncio
async def test_stuck_is_judged_by_the_holders_own_timestamp(dsn, observer, pg_db_url):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import LOCK_STUCK_AFTER, lock_status, release_mirror_lock, try_mirror_lock

    # F7: 80 minutes, above the longest legitimate run (about 71.5: busy waits count per attempt).
    assert LOCK_STUCK_AFTER == timedelta(minutes=80)
    holder = await open_dedicated(dsn)
    engine = host_engine(pg_db_url)
    try:
        assert await lock_status(observer, now=T0) == NOT_HELD
        assert await try_mirror_lock(holder, now=T0, holder="backfill")
        pid = await holder.fetchval("select pg_backend_pid()")
        held = {"held": True, "holder_pid": pid, "holder": "backfill", "holder_since": T0}
        assert await lock_status(observer, now=T0 + timedelta(minutes=80)) == {**held, "stuck": False}
        assert await lock_status(observer, now=T0 + timedelta(minutes=81)) == {**held, "stuck": True}
        assert (await lock_status(observer, now=T0 + timedelta(minutes=11), stuck_after=timedelta(minutes=10)))["stuck"]
        # The /sync route asks through its ORM session.
        async with async_sessionmaker(engine)() as session:
            assert await lock_status(session, now=T0 + timedelta(minutes=81)) == {**held, "stuck": True}
        assert await release_mirror_lock(holder)
        assert await lock_status(observer, now=T0 + timedelta(minutes=81)) == NOT_HELD
        # Supavisor hands the next client the same backend (supabase.md): a backend serving for hours that took the lock a
        # minute ago is not stuck. backend_start is not the time of the hold.
        started = await observer.fetchval("select backend_start from pg_stat_activity where pid = $1", pid)
        took = started + timedelta(hours=5)
        assert await try_mirror_lock(holder, now=took, holder="sync")
        assert not (await lock_status(observer, now=took + timedelta(minutes=1)))["stuck"]
    finally:
        await holder.close()
        await engine.dispose()


class _Recording:
    """Stands in for the dedicated connection and records every statement sent to it."""

    def __init__(self):
        self.sent = []

    async def fetchval(self, query, *args):
        self.sent.append(query)
        return True

    async def execute(self, query, *args):
        self.sent.append(query)
        return "UPDATE 1"


@pytest.mark.parametrize("holder", ["admin", "", "Sync", "sync ", None, 1])
@pytest.mark.asyncio
async def test_a_holder_outside_the_three_is_refused_before_any_sql(holder, monkeypatch):
    from ggwork_pick.mirror import lock

    conn, opened = _Recording(), []

    async def open_recorded(target):
        opened.append(target)
        return _Recording()

    monkeypatch.setattr(lock, "open_dedicated", open_recorded)
    with pytest.raises(ValueError, match="sync、backfill、cleanup"):
        await lock.try_mirror_lock(conn, now=T0, holder=holder)
    assert conn.sent == []
    # The context manager checks it before it even opens the dedicated connection.
    with pytest.raises(ValueError, match="sync、backfill、cleanup"):
        async with lock.mirror_lock("postgresql://unused", holder=holder, clock=lambda: T0):
            pass
    assert opened == []


@pytest.mark.asyncio
async def test_every_take_names_its_holder():
    from ggwork_pick.mirror import lock

    with pytest.raises(TypeError):
        await lock.try_mirror_lock(_Recording(), now=T0)
    with pytest.raises(TypeError):
        async with lock.mirror_lock("postgresql://unused", clock=lambda: T0):
            pass


@pytest.mark.parametrize("holder", ["sync", "backfill", "cleanup"])
@pytest.mark.asyncio
async def test_holder_and_since_are_written_while_held_and_cleared_on_release(dsn, observer, holder):
    # P2-0 test 7 (U14): both control columns name the current hold and go back to NULL with it.
    from ggwork_pick.mirror.lock import lock_status, mirror_lock

    async with mirror_lock(dsn, holder=holder, clock=lambda: T0) as conn:
        assert conn is not None
        assert tuple(await observer.fetchrow(HOLDER)) == (holder, T0)
        status = await lock_status(observer, now=T0 + timedelta(minutes=5))
        assert (status["held"], status["holder"], status["holder_since"], status["stuck"]) == (True, holder, T0, False)
    assert tuple(await observer.fetchrow(HOLDER)) == (None, None)
    assert await lock_status(observer, now=T0) == NOT_HELD


@pytest.mark.asyncio
async def test_try_needs_an_aware_now_and_takes_nothing_without_one(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import lock_status, try_mirror_lock

    conn = await open_dedicated(dsn)
    try:
        with pytest.raises(ValueError, match="时区"):
            await try_mirror_lock(conn, now=T0.replace(tzinfo=None), holder="sync")
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
            await try_mirror_lock(conn, now=T0, holder="sync")
        assert await _no_advisory_locks(observer)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mirror_lock_yields_the_holder_or_none_and_always_releases_and_closes(dsn, observer):
    from ggwork_pick.mirror.lock import mirror_lock

    async with mirror_lock(dsn, holder="sync", clock=lambda: T0) as holder:
        assert holder is not None and tuple(await observer.fetchrow(HOLDER)) == ("sync", T0)
        async with mirror_lock(dsn, holder="backfill", clock=lambda: T0 + timedelta(minutes=1)) as loser:
            assert loser is None
        # The loser's exit closed its own connection and left the holder alone.
        assert len(await observer.fetch(ADVISORY)) == 1 and tuple(await observer.fetchrow(HOLDER)) == ("sync", T0)
        assert await observer.fetchval(MIRROR_CONNECTIONS) == 1
    assert holder.is_closed()
    assert await _no_advisory_locks(observer) and await observer.fetchval(SINCE) is None
    with pytest.raises(RuntimeError, match="boom"):
        async with mirror_lock(dsn, holder="sync", clock=lambda: T0) as holder:
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
        async with mirror_lock(dsn, holder="sync", clock=lambda: T0) as conn:
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
        # Cancelled mid-statement: asyncpg has the server cancel the statement, then the cleanup releases and closes as usual.
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


def _warned(caplog, text: str) -> bool:
    return any(record.name == "ggwork_pick.mirror.lock" and text in record.getMessage() for record in caplog.records)


class _Wrapped:
    """The dedicated connection with one call made to fail; everything else passes through."""

    def __init__(self, conn):
        self.conn = conn
        self.terminated = False

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def terminate(self) -> None:
        self.terminated = True
        self.conn.terminate()


class _CloseTimesOut(_Wrapped):
    async def close(self, *, timeout=None):
        raise TimeoutError


class _UnlockBreaks(_Wrapped):
    async def fetchval(self, query, *args):
        if "pg_advisory_unlock" in query:
            raise ConnectionResetError("connection lost")
        return await self.conn.fetchval(query, *args)


@pytest.mark.asyncio
async def test_a_release_that_fails_still_closes_and_the_session_takes_the_lock_along(dsn, observer, caplog):
    from ggwork_pick.mirror.lock import mirror_lock

    with caplog.at_level(logging.WARNING, logger="ggwork_pick.mirror.lock"):
        async with mirror_lock(dsn, holder="sync", clock=lambda: T0) as holder:
            # The release clears the timestamp in control first: with the table gone that statement fails.
            await observer.execute("drop table pick_mirror.control")
    assert holder.is_closed()
    await _eventually(lambda: _no_advisory_locks(observer))
    assert _warned(caplog, "releasing the mirror lock failed")


@pytest.mark.asyncio
async def test_a_connection_that_will_not_close_is_terminated(dsn, observer, monkeypatch, caplog):
    from ggwork_pick.mirror import lock
    from ggwork_pick.mirror.connection import open_dedicated

    opened = []

    async def open_stuck(target):
        opened.append(_CloseTimesOut(await open_dedicated(target)))
        return opened[-1]

    monkeypatch.setattr(lock, "open_dedicated", open_stuck)
    try:
        with caplog.at_level(logging.WARNING, logger="ggwork_pick.mirror.lock"):
            async with lock.mirror_lock(dsn, holder="sync", clock=lambda: T0) as holder:
                assert holder is opened[0]
        assert holder.terminated and holder.conn.is_closed()
        await _eventually(lambda: _count_is(observer, MIRROR_CONNECTIONS, 0))
        assert _warned(caplog, "did not close cleanly")
    finally:
        for wrapped in opened:
            wrapped.conn.terminate()


@pytest.mark.asyncio
async def test_a_holder_that_cannot_give_the_lock_back_still_raises_its_own_error(dsn, observer, caplog):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    await observer.execute("delete from pick_mirror.control")
    conn = await open_dedicated(dsn)
    try:
        with caplog.at_level(logging.WARNING, logger="ggwork_pick.mirror.lock"), pytest.raises(RuntimeError, match="pick_mirror.control"):
            await try_mirror_lock(_UnlockBreaks(conn), now=T0, holder="sync")
        # The give-back failed, so the session still holds it; the warning says closing is what frees it.
        assert len(await observer.fetch(ADVISORY)) == 1
        assert _warned(caplog, "giving the mirror lock back failed")
    finally:
        await conn.close()
    await _eventually(lambda: _no_advisory_locks(observer))


def _int4(half: int) -> int:
    return half - 2**32 if half >= 2**31 else half


@pytest.mark.asyncio
async def test_a_two_key_lock_on_the_same_halves_is_not_the_mirror_lock(dsn, observer):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import MIRROR_LOCK_CLASSID, MIRROR_LOCK_OBJID, lock_status, release_mirror_lock, try_mirror_lock

    # pg_advisory_lock(int4, int4) shows the very same classid and objid, with objsubid 2.
    await observer.execute("select pg_advisory_lock($1::int4, $2::int4)", _int4(MIRROR_LOCK_CLASSID), _int4(MIRROR_LOCK_OBJID))
    other = await observer.fetchval("select pg_backend_pid()")
    assert [tuple(row) for row in await observer.fetch(ADVISORY)] == [(other, MIRROR_LOCK_CLASSID, MIRROR_LOCK_OBJID, 2, True)]
    assert await lock_status(observer, now=T0) == NOT_HELD
    holder = await open_dedicated(dsn)
    try:
        assert await try_mirror_lock(holder, now=T0, holder="sync")
        pid = await holder.fetchval("select pg_backend_pid()")
        held = {"held": True, "holder_pid": pid, "holder": "sync", "holder_since": T0, "stuck": True}
        assert await lock_status(observer, now=T0 + timedelta(minutes=81)) == held
        # The other lock's owner "releasing the mirror lock" leaves the real holder and its timestamp alone.
        assert not await release_mirror_lock(observer)
        assert await observer.fetchval(SINCE) == T0
    finally:
        await holder.close()


@pytest.mark.asyncio
async def test_the_key_held_in_another_database_is_not_this_databases_mirror_lock(dsn, observer, pg_cluster):
    import asyncpg

    from ggwork_pick.mirror.connection import dsn_from_url, open_dedicated
    from ggwork_pick.mirror.lock import MIRROR_LOCK_KEY, lock_status, try_mirror_lock

    name = pg.unique_name("other")
    pg_cluster.create_database(name)
    try:
        elsewhere = await asyncpg.connect(dsn_from_url(pg_cluster.async_url(name)))
        try:
            # Advisory locks are per database while pg_locks shows the whole cluster.
            assert await elsewhere.fetchval("select pg_try_advisory_lock($1)", MIRROR_LOCK_KEY)
            assert await observer.fetchval("select count(*) from pg_locks where locktype = 'advisory' and pid = $1", elsewhere.get_server_pid()) == 1
            assert await lock_status(observer, now=T0) == NOT_HELD
            here = await open_dedicated(dsn)
            try:
                assert await try_mirror_lock(here, now=T0, holder="sync")
                assert (await lock_status(observer, now=T0))["holder_pid"] == here.get_server_pid()
            finally:
                await here.close()
        finally:
            await elsewhere.close()
    finally:
        pg_cluster.drop_database(name)
