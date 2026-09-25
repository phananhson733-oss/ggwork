"""TR-13: the lease under contention (design 3.3; plan D1, D2; counterexample 10) and what a step may wait for.

- The generation, not the owner's name, tells two holders apart: an owner name fixed across runs (a replica id) must
  not let the old run write after the new one took the lease over.
- A takeover locks the row before it judges the lease: a renewal in flight wins, the takeover finds the new lease_until.
- A step waits at most so long for the row or a statement. A start-up that times out waiting is busy, not broken:
  exit 1 and the next trigger tries again; a heavy step may relax its own limits, the rest keep theirs.
- A status reader is not the collector in pg_stat_activity, and a cancellation is never lost releasing the lease.

Every test runs on SQLite and PostgreSQL unless it says otherwise (the PostgreSQL half skips without PICK_TEST_PG_URL).
"""

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import (
    T0,
    clock_at,
    collector_env,
    is_postgres,
    migrated,
    new_cipher,
    open_db,
    rows,
    runtime_row,
    snapshot,
    stamp,
    waiting_for_the_row,
    watch_begin,
)
from obs_schema import filler
from sqlalchemy import event, insert, text

from ggwork_pick.observe.db import APPLICATION_NAMES, ObsDatabase, StepLimits
from ggwork_pick.observe.errors import ExitCode, exit_code_for, sqlstate
from ggwork_pick.observe.lease import LEASE_SECONDS, RENEW_SECONDS, DbStateStore, LeasedWriter, LeaseHeld, LeaseLost, locked_runtime, status_reader

LEASE = timedelta(seconds=LEASE_SECONDS)
EXPIRED = LEASE + timedelta(minutes=1)
SHORT_WAIT = StepLimits(lock_ms=300)  # a start-up that would wait 15 s in production gives up here in 0.3 s


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


async def _acquire(url, clock, owner):
    db = open_db(url)
    return db, await LeasedWriter.acquire(db, "trends", clock=clock, owner=owner)


def _milestone():
    table, row = filler("ggwp_obs_milestones")
    return insert(table).values(**row)


# ---- the generation tells a fixed owner's runs apart ------------------------------------------------------------


async def _old_writes(old, clock):
    async with old.step() as step:
        await step.execute(_milestone())


async def _old_renews(old, clock):
    clock.advance(RENEW_SECONDS)
    await old.ensure_fresh()


async def _old_releases(old, clock):
    await old.release()


OLD_RUN = {"write": _old_writes, "renew": _old_renews, "release": _old_releases}


@pytest.mark.parametrize("action", sorted(OLD_RUN))
@pytest.mark.asyncio
async def test_same_owner_old_generation_is_refused(obs_url, action):
    """[counterexample 10] The same owner name takes the lease again one generation up (a fixed replica id, say). The
    old run, whose own clock still says its lease runs, can neither write, renew nor release: its generation is gone."""
    old_clock = clock_at(T0)
    old_db, old = await _acquire(obs_url, old_clock, "replica-1")
    new_db, new = await _acquire(obs_url, clock_at(T0 + EXPIRED), "replica-1")
    try:
        assert (new.token.owner, new.generation) == (old.token.owner, old.generation + 1)
        old_clock.advance(60)  # inside the old lease by the old run's clock
        tables = ("ggwp_obs_runtime", "ggwp_obs_milestones")
        before = await snapshot(obs_url, tables)
        if action == "release":
            await OLD_RUN[action](old, old_clock)  # a lease no longer this run's is left alone
        else:
            with pytest.raises(LeaseLost):
                await OLD_RUN[action](old, old_clock)
        assert await snapshot(obs_url, tables) == before
        held = await runtime_row(obs_url)
        assert (held["lease_owner"], held["lease_generation"], held["lease_until"]) == ("replica-1", 2, stamp(T0 + EXPIRED + LEASE))
    finally:
        await old_db.dispose()
        await new_db.dispose()


# ---- a takeover waits for a renewal in flight --------------------------------------------------------------------


def _pause_after_runtime_update(db: ObsDatabase, updated: asyncio.Event, resume: asyncio.Event) -> None:
    """Hold `db`'s transaction right after its UPDATE of the runtime row, before the commit."""
    from sqlalchemy.util import await_only

    def paused(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE GGWP_OBS_RUNTIME"):
            updated.set()
            await_only(resume.wait())

    event.listen(db._engine.sync_engine, "after_cursor_execute", paused)


@pytest.mark.asyncio
async def test_takeover_waits_for_renewal(obs_url):
    """A renews at T0+4 min (lease_until T0+5 min -> T0+9 min) and stops before its commit. B, at T0+6 min, would find
    the old lease_until run out; taking the lease locks the row first, so B waits, reads T0+9 min and does nothing."""
    a_clock = clock_at(T0)
    a_db, a = await _acquire(obs_url, a_clock, "proc-a")
    b_db = open_db(obs_url)
    updated, resume = asyncio.Event(), asyncio.Event()
    began = None if is_postgres(obs_url) else watch_begin(b_db)
    try:
        a_clock.advance(4 * 60)
        _pause_after_runtime_update(a_db, updated, resume)
        renewing = asyncio.create_task(a.renew())
        await asyncio.wait_for(updated.wait(), 5)
        takeover = asyncio.create_task(LeasedWriter.acquire(b_db, "trends", clock=clock_at(T0 + timedelta(minutes=6)), owner="proc-b"))
        await waiting_for_the_row(obs_url, b_db, began)
        await asyncio.sleep(0.2)
        assert not takeover.done()  # waiting on the row A holds
        resume.set()
        await asyncio.wait_for(renewing, 5)
        with pytest.raises(LeaseHeld):
            await asyncio.wait_for(takeover, 10)
        held = await runtime_row(obs_url)
        assert (held["lease_owner"], held["lease_generation"], held["lease_until"]) == ("proc-a", 1, stamp(T0 + timedelta(minutes=9)))
    finally:
        resume.set()
        await a_db.dispose()
        await b_db.dispose()


# ---- waiting at start-up is busy, not broken: exit 1 ------------------------------------------------------------


def _short_db(url: str, limits: StepLimits = SHORT_WAIT) -> ObsDatabase:
    return ObsDatabase(url, application_name=APPLICATION_NAMES["trends"], limits=limits)


async def _hold_row(url: str, held: asyncio.Event, done: asyncio.Event) -> None:
    """Another process in a step: the trends runtime row locked until `done`."""
    holder = ObsDatabase(url, application_name="ggwp-obs-test-holder")
    try:
        async with holder.transaction() as conn:
            await locked_runtime(conn, "trends")
            held.set()
            await done.wait()
    finally:
        await holder.dispose()


async def _while_row_held(url: str, attempt) -> BaseException:
    held, done = asyncio.Event(), asyncio.Event()
    holding = asyncio.create_task(_hold_row(url, held, done))
    try:
        await asyncio.wait_for(held.wait(), 5)
        with pytest.raises(Exception) as failed:
            await asyncio.wait_for(attempt(), 20)
        return failed.value
    finally:
        done.set()
        await holding


def _busy_state(url: str) -> str | None:
    return "55P03" if is_postgres(url) else None  # lock_not_available; SQLite's SQLITE_BUSY has no SQLSTATE


@pytest.mark.asyncio
async def test_acquire_that_times_out_waiting_exits_1(obs_url):
    db = _short_db(obs_url)
    try:
        before = await runtime_row(obs_url)
        failed = await _while_row_held(obs_url, lambda: LeasedWriter.acquire(db, "trends", clock=clock_at(T0), owner="proc-late"))
        assert exit_code_for(failed) == ExitCode.FAILED, failed
        assert sqlstate(failed) == _busy_state(obs_url)
        assert await runtime_row(obs_url) == before
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_state_load_that_times_out_waiting_exits_1(obs_url):
    db = _short_db(obs_url)
    try:
        writer = await LeasedWriter.acquire(db, "trends", clock=clock_at(T0), owner="proc-a")
        failed = await _while_row_held(obs_url, lambda: DbStateStore(writer, new_cipher()).load())
        assert exit_code_for(failed) == ExitCode.FAILED, failed
        assert sqlstate(failed) == _busy_state(obs_url)
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_statement_timeout_at_acquire_exits_1(pg_db_url):
    """PostgreSQL: a statement_timeout (57014) under the lock wait's limit is busy too."""
    db = _short_db(pg_db_url, StepLimits(lock_ms=5_000, statement_ms=300))
    try:
        failed = await _while_row_held(pg_db_url, lambda: LeasedWriter.acquire(db, "trends", clock=clock_at(T0), owner="proc-late"))
        assert (exit_code_for(failed), sqlstate(failed)) == (ExitCode.FAILED, "57014")
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_selfcheck_waiting_on_a_migration_exits_1(pg_db_url):
    """PostgreSQL: a migration holds the version table; the self-check gives up waiting and the next trigger retries."""
    from ggwork_pick.observe import selfcheck

    held, done = asyncio.Event(), asyncio.Event()

    async def migrating():
        holder = ObsDatabase(pg_db_url, application_name="ggwp-obs-test-holder")
        try:
            async with holder.transaction() as conn:
                await conn.execute(text("LOCK TABLE ggwp_alembic_version IN ACCESS EXCLUSIVE MODE"))
                held.set()
                await done.wait()
        finally:
            await holder.dispose()

    db = _short_db(pg_db_url)
    holding = asyncio.create_task(migrating())
    try:
        await asyncio.wait_for(held.wait(), 5)
        with pytest.raises(Exception) as failed:
            await selfcheck.run_selfcheck(db, selfcheck.expectations_from(collector_env(pg_db_url)))
        assert (exit_code_for(failed.value), sqlstate(failed.value)) == (ExitCode.FAILED, "55P03")
    finally:
        done.set()
        await holding
        await db.dispose()


# ---- a heavy step may relax its limits; every other step keeps them ------------------------------------------------


SHOW_LIMITS = text(
    "select current_setting('lock_timeout') as lock, current_setting('statement_timeout') as statement, "
    "current_setting('idle_in_transaction_session_timeout') as idle"
)


@pytest.mark.asyncio
async def test_a_heavy_step_relaxes_its_own_limits(obs_url):
    """TR-20 and TR-23b's big deletes and link materialization: step(limits=...) raises the server's limits and the
    driver's own (asyncpg's command_timeout, SQLite's busy timeout) for that step alone."""
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    connected: list[dict] = []
    event.listen(db._engine.sync_engine, "do_connect", lambda dialect, record, cargs, cparams: connected.append(dict(cparams)))
    heavy = StepLimits(lock_ms=20_000, statement_ms=180_000)
    try:
        shown = []
        for limits in (None, heavy, None):
            async with writer.step(limits=limits) as step:
                shown.append(tuple((await step.execute(SHOW_LIMITS)).one()) if is_postgres(obs_url) else None)
        if is_postgres(obs_url):
            assert shown == [("15s", "30s", "1min"), ("20s", "3min", "1min"), ("15s", "30s", "1min")]
            assert [cparams["command_timeout"] for cparams in connected] == [30, 180, 30]
        else:
            assert [cparams["timeout"] for cparams in connected] == [15, 20, 15]
    finally:
        await db.dispose()


@pytest.mark.parametrize("bad", [{"lock_ms": 0}, {"statement_ms": -1}, {"idle_ms": True}, {"statement_ms": 600_001}, {"lock_ms": "15s"}])
def test_step_limits_are_checked(bad):
    with pytest.raises(ValueError):
        StepLimits(**bad)


# ---- a status reader is not the collector; a cancellation is never lost --------------------------------------------


async def _observe_connections(url: str) -> dict[str, int]:
    found = await rows(
        url,
        "select application_name as name, count(*) as n from pg_stat_activity "
        "where datname = current_database() and application_name like 'ggwp-obs-%' group by 1",
    )
    return {row["name"]: row["n"] for row in found}


@pytest.mark.asyncio
async def test_status_reader_is_not_counted_as_the_collector(pg_db_url):
    """PostgreSQL: while the collector is in a step (its one connection), a status command shows as ggwp-obs-admin, so
    counting ggwp-obs-trends still counts the collector alone (runbook: connections)."""
    db, writer = await _acquire(pg_db_url, clock_at(T0), "proc-running")
    try:
        async with writer.step():
            async with status_reader("trends", environ=collector_env(pg_db_url)) as reader:
                await reader.execute(text("select 1"))
                seen = await _observe_connections(pg_db_url)
        assert seen == {"ggwp-obs-trends": 1, "ggwp-obs-admin": 1}
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_release_never_swallows_a_cancellation(obs_url, caplog):
    """Leaving the writer logs a failed release and carries on, unless the failure is a cancellation (or holds one)."""
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    try:

        async def cancelled():
            raise BaseExceptionGroup("release", [asyncio.CancelledError()])

        async def failed():
            raise ExceptionGroup("release", [OSError("connection reset")])

        writer.release = cancelled
        with pytest.raises(BaseExceptionGroup) as raised:
            async with writer:
                pass
        assert raised.group_contains(asyncio.CancelledError)
        writer.release = failed
        async with writer:
            pass
        assert "lease not released" in caplog.text
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_the_row_holder_helper_really_holds(obs_url):
    """The control for the timeout tests: without the holder the same short-limit acquire goes through."""
    db = _short_db(obs_url)
    try:
        writer = await LeasedWriter.acquire(db, "trends", clock=clock_at(T0), owner="proc-a")
        assert writer.generation == 1
        await writer.release()
    finally:
        await db.dispose()
