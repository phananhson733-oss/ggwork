"""TR-13: the lease on a channel's runtime row and the leased write step (design 3.3; plan D1, D2; counterexample 10).

Two ObsDatabase objects on one database stand for two processes; each has its own manual clock, so "the old process's
lease ran out while it was frozen" is a clock that moved without a renewal. Every test runs on SQLite and PostgreSQL
(the PostgreSQL half skips without PICK_TEST_PG_URL); the connection count is PostgreSQL's alone.
"""

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import (
    T0,
    TARGET,
    clock_at,
    collector_env,
    count,
    execute,
    is_postgres,
    migrated,
    new_cipher,
    open_db,
    rows,
    runtime_row,
    snapshot,
    stamp,
)
from obs_schema import filler
from sqlalchemy import insert, select, update

from ggwork_pick.models import gsc_slices, obs_batches, obs_milestones, obs_requests, obs_runtime, obs_sets
from ggwork_pick.observe.clock import sleep_in_chunks
from ggwork_pick.observe.errors import ExitCode, exit_code_for
from ggwork_pick.observe.lease import LEASE_SECONDS, RENEW_SECONDS, DbStateStore, LeasedWriter, LeaseHeld, LeaseLost
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends.budget import BudgetDay

LEASE = timedelta(seconds=LEASE_SECONDS)
EXPIRED = LEASE + timedelta(minutes=1)  # past the lease: the holder froze without renewing


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


async def _acquire(url, clock, owner, channel="trends"):
    db = open_db(url, channel)
    return db, await LeasedWriter.acquire(db, channel, clock=clock, owner=owner)


def _row(name: str, **values) -> tuple:
    table, row = filler(name, **values)
    return table, row


# ---- acquiring, renewing, releasing -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_takeover_bumps_generation(obs_url):
    a_db, a = await _acquire(obs_url, clock_at(T0), "proc-a")
    b_db = open_db(obs_url)
    try:
        assert a.generation == 1
        held = await runtime_row(obs_url)
        assert (held["lease_owner"], held["lease_generation"], held["lease_until"]) == ("proc-a", 1, stamp(T0 + LEASE))
        # A live lease is never taken: the second process does nothing.
        with pytest.raises(LeaseHeld) as refused:
            await LeasedWriter.acquire(b_db, "trends", clock=clock_at(T0 + LEASE - timedelta(seconds=1)), owner="proc-b")
        assert exit_code_for(refused.value) == ExitCode.FAILED
        assert (await runtime_row(obs_url))["lease_generation"] == 1
        # Once it has run out, the next process takes it over, one generation up.
        b = await LeasedWriter.acquire(b_db, "trends", clock=clock_at(T0 + EXPIRED), owner="proc-b")
        assert b.generation == 2
        taken = await runtime_row(obs_url)
        assert (taken["lease_owner"], taken["lease_generation"], taken["lease_until"]) == ("proc-b", 2, stamp(T0 + EXPIRED + LEASE))
        # A released lease is free at once, and taking it is a new generation too.
        await b.release()
        c = await LeasedWriter.acquire(a_db, "trends", clock=clock_at(T0 + EXPIRED), owner="proc-c")
        assert c.generation == 3
        # The other channel's lease is its own.
        assert (await runtime_row(obs_url, "gsc"))["lease_generation"] == 0
    finally:
        await a_db.dispose()
        await b_db.dispose()


@pytest.mark.asyncio
async def test_ensure_fresh_renews_every_60_seconds(obs_url):
    clock = clock_at(T0)
    db, writer = await _acquire(obs_url, clock, "proc-a")
    try:
        clock.advance(RENEW_SECONDS - 1)
        await writer.ensure_fresh()  # renewed under a minute ago: no transaction at all
        assert (await runtime_row(obs_url))["lease_until"] == stamp(T0 + LEASE)
        clock.advance(1)
        await writer.ensure_fresh()
        assert (await runtime_row(obs_url))["lease_until"] == stamp(T0 + timedelta(seconds=RENEW_SECONDS) + LEASE)
        assert writer.token.until == T0 + timedelta(seconds=RENEW_SECONDS) + LEASE
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_renew_during_4h_pause(obs_url):
    """A four-hour breaker pause sleeps in 60-second chunks and renews on each wake (design 3.3, 4.3): the lease is
    still this process's afterwards, same generation, and nobody could take it in between."""
    clock = clock_at(T0)
    db, writer = await _acquire(obs_url, clock, "proc-a")
    other = open_db(obs_url)
    try:
        await sleep_in_chunks(clock, 4 * 3600, on_wake=writer.ensure_fresh)
        after = T0 + timedelta(hours=4)
        assert (await runtime_row(obs_url))["lease_until"] == stamp(after + LEASE)
        with pytest.raises(LeaseHeld):
            await LeasedWriter.acquire(other, "trends", clock=clock_at(after + timedelta(minutes=4)), owner="proc-b")
        table, row = _row("ggwp_obs_milestones")
        async with writer.step() as step:
            await step.execute(insert(table).values(**row))
        assert writer.generation == 1
        assert await count(obs_url, "ggwp_obs_milestones") == 1
    finally:
        await db.dispose()
        await other.dispose()


@pytest.mark.asyncio
async def test_pause_without_renewal_loses_the_lease(obs_url):
    """The control for the test above: the same pause with a wake that does not renew ends with the lease gone."""
    clock = clock_at(T0)
    db, writer = await _acquire(obs_url, clock, "proc-a")
    try:

        async def no_renewal():
            return None

        await sleep_in_chunks(clock, 4 * 3600, on_wake=no_renewal)
        with pytest.raises(LeaseLost):
            async with writer.step():
                pass
        assert writer.lost
    finally:
        await db.dispose()


# ---- counterexample 10: the stale owner -------------------------------------------------------------------------


async def _reserve_budget(writer):
    state = RuntimeState(budget=BudgetDay(TARGET, reserved=1).to_dict())
    await DbStateStore(writer, new_cipher()).save(state)


async def _write_response(writer):
    requests, request = _row("ggwp_obs_requests", channel="trends", budget_item="multiline", endpoint="multiline", sent_at=stamp(T0))
    raws, raw = _row("ggwp_obs_raw", batch_id="batch-1", geo="US", line_role="bare", time_range="now 7-d", fetch_status="ok", fetched_at=stamp(T0))
    async with writer.step() as step:
        await step.execute(insert(requests).values(**request))
        await step.execute(insert(raws).values(**raw))


async def _publish_set(writer):
    sets, row = _row("ggwp_obs_sets", id="set-1", channel="trends", mode="shadow", status="published", batch_id="batch-1")
    async with writer.step() as step:
        await step.execute(insert(sets).values(**row))
        await step.execute(update(obs_batches).where(obs_batches.c.id == "batch-1").values(published_set_id="set-1"))


async def _switch_slice(writer):
    async with writer.step() as step:
        await step.execute(update(gsc_slices).where(gsc_slices.c.round_id == "round-1").values(active=True, activated_at=stamp(T0)))


async def _alerts_and_milestones(writer):
    alerts, alert = _row("ggwp_obs_alerts", channel="trends", mode="shadow")
    milestones, milestone = _row("ggwp_obs_milestones")
    async with writer.step() as step:
        await step.execute(insert(alerts).values(**alert))
        await step.execute(insert(milestones).values(**milestone))


# Every write a collector makes, by the boundary it belongs to (design 3.3; plan TR-13).
STALE_WRITES = {
    "reserve_budget": (_reserve_budget, ("ggwp_obs_budget",)),
    "write_response": (_write_response, ("ggwp_obs_requests", "ggwp_obs_raw")),
    "publish_set": (_publish_set, ("ggwp_obs_sets", "ggwp_obs_batches")),
    "switch_slice_pointer": (_switch_slice, ("ggwp_gsc_slices",)),
    "alerts_and_milestones": (_alerts_and_milestones, ("ggwp_obs_alerts", "ggwp_obs_milestones")),
}
TOUCHED = ("ggwp_obs_runtime", *sorted({table for _, tables in STALE_WRITES.values() for table in tables}))


async def _seed_targets(url):
    """A batch to publish into and an inactive slice to switch on, so the stale writes have something to change."""
    db, seeder = await _acquire(url, clock_at(T0 - timedelta(hours=1)), "seeder")
    batches, batch = _row("ggwp_obs_batches", id="batch-1", channel="trends", mode="shadow", started_at=stamp(T0), outcome="running", status_codes_json=[])
    slices, slice_ = _row("ggwp_gsc_slices", round_id="round-1", active=False)
    try:
        async with seeder.step() as step:
            await step.execute(insert(batches).values(**batch))
            await step.execute(insert(slices).values(**slice_))
        await seeder.release()
    finally:
        await db.dispose()


@pytest.mark.parametrize("taken_over", [True, False], ids=["taken_over", "expired_only"])
@pytest.mark.parametrize("write", sorted(STALE_WRITES))
@pytest.mark.asyncio
async def test_stale_owner_cannot_write_or_publish(obs_url, write, taken_over):
    """[counterexample 10] The old process comes back after its lease ran out: reserving budget, writing a response,
    publishing a set, switching a slice pointer, writing alerts and milestones all raise LeaseLost and write nothing,
    whether another process took the lease over or nobody did yet."""
    await _seed_targets(obs_url)
    clock = clock_at(T0)
    db, stale = await _acquire(obs_url, clock, "proc-old")
    other = open_db(obs_url)
    try:
        clock.advance(EXPIRED.total_seconds())  # frozen past the lease, no renewal
        if taken_over:
            fresh = await LeasedWriter.acquire(other, "trends", clock=clock_at(T0 + EXPIRED), owner="proc-new")
            assert fresh.generation == stale.generation + 1
        before = await snapshot(obs_url, TOUCHED)
        perform, _ = STALE_WRITES[write]
        with pytest.raises(LeaseLost) as lost:
            await perform(stale)
        assert exit_code_for(lost.value) == ExitCode.FAILED
        assert await snapshot(obs_url, TOUCHED) == before
        # Lost for good: the next attempt fails without asking the database.
        with pytest.raises(LeaseLost):
            await stale.ensure_fresh()
    finally:
        await db.dispose()
        await other.dispose()


# ---- serialization -----------------------------------------------------------------------------------------------


async def _until(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for the condition")
        await asyncio.sleep(0.01)


def _watch_begin(db) -> asyncio.Event:
    """Set once `db` has sent its BEGIN IMMEDIATE (SQLite): from then on it waits for the other writer."""
    from sqlalchemy import event

    began = asyncio.Event()
    loop = asyncio.get_running_loop()

    def seen(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            loop.call_soon_threadsafe(began.set)

    event.listen(db._engine.sync_engine, "before_cursor_execute", seen)
    return began


async def _lock_waiters(url) -> int:
    found = await rows(url, "select count(*) as n from pg_stat_activity where datname = current_database() and wait_event_type = 'Lock'")
    return found[0]["n"]


@pytest.mark.asyncio
async def test_takeover_serialized_with_write(obs_url):
    """A write step and a takeover lock the same row: the takeover waits for the write to commit (or roll back), so the
    old process can never commit after the new one holds the lease (design 3.3)."""
    a_db, a = await _acquire(obs_url, clock_at(T0), "proc-a")
    b_db = open_db(obs_url)
    inside, release = asyncio.Event(), asyncio.Event()
    table, row = _row("ggwp_obs_milestones")
    began = None if is_postgres(obs_url) else _watch_begin(b_db)

    async def a_writes():
        async with a.step() as step:
            await step.execute(insert(table).values(**row))
            inside.set()
            await release.wait()

    try:
        writing = asyncio.create_task(a_writes())
        await asyncio.wait_for(inside.wait(), 5)
        takeover = asyncio.create_task(LeasedWriter.acquire(b_db, "trends", clock=clock_at(T0 + EXPIRED), owner="proc-b"))
        if began is None:
            await _until(lambda: _waits(obs_url))
        else:
            await asyncio.wait_for(began.wait(), 5)
        await asyncio.sleep(0.2)
        assert not takeover.done()  # waiting on the row A holds
        release.set()
        await asyncio.wait_for(writing, 5)
        b = await asyncio.wait_for(takeover, 10)
        assert b.generation == a.generation + 1
        assert await count(obs_url, "ggwp_obs_milestones") == 1  # A's write, committed before the takeover
        with pytest.raises(LeaseLost):
            async with a.step() as step:
                await step.execute(insert(table).values(**row))
        assert await count(obs_url, "ggwp_obs_milestones") == 1
    finally:
        release.set()
        await a_db.dispose()
        await b_db.dispose()


async def _waits(url) -> bool:
    return await _lock_waiters(url) >= 1


@pytest.mark.asyncio
async def test_nested_step_is_refused_not_deadlocked(obs_url):
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    try:
        async with writer.step():
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(writer.step().__aenter__(), 2)
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_step_is_unusable_after_it_ends(obs_url):
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    table, row = _row("ggwp_obs_milestones")
    try:
        async with writer.step() as step:
            kept = step
        with pytest.raises(RuntimeError):
            await kept.execute(insert(table).values(**row))
        assert await count(obs_url, "ggwp_obs_milestones") == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_step_carries_the_generation_for_the_rows_it_writes(obs_url):
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    try:
        async with writer.step() as step:
            assert (step.token.generation, step.token.owner, step.now) == (1, "proc-a", T0)
            await step.execute(
                insert(obs_requests).values(
                    channel="trends", lease_generation=step.token.generation, budget_item="warmup", endpoint="warmup", sent_at=stamp(step.now)
                )
            )
            found = (await step.execute(select(obs_requests.c.lease_generation))).scalar_one()
        assert found == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_reading_is_read_only(obs_url):
    db, writer = await _acquire(obs_url, clock_at(T0), "proc-a")
    table, row = _row("ggwp_obs_milestones")
    try:
        async with writer.reading() as reader:
            assert (await reader.execute(select(obs_sets.c.id))).all() == []
        with pytest.raises(Exception) as refused:
            async with writer.reading() as reader:
                await reader.execute(insert(table).values(**row))
        assert "read" in str(refused.value).lower()
        assert await count(obs_url, "ggwp_obs_milestones") == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_status_reader_needs_no_lease_and_writes_nothing(obs_url):
    """A status command reads while a collector holds the lease, and cannot write."""
    from ggwork_pick.observe.lease import status_reader

    db, writer = await _acquire(obs_url, clock_at(T0), "proc-running")
    env = collector_env(obs_url)
    table, row = _row("ggwp_obs_milestones")
    try:
        async with status_reader("trends", environ=env) as reader:
            found = (await reader.execute(select(obs_runtime.c.lease_owner).where(obs_runtime.c.channel == "trends"))).scalar_one()
        assert found == "proc-running"
        with pytest.raises(Exception, match="(?i)read"):
            async with status_reader("trends", environ=env) as reader:
                await reader.execute(insert(table).values(**row))
        assert await count(obs_url, "ggwp_obs_milestones") == 0
        assert writer.generation == 1
    finally:
        await db.dispose()


# ---- one connection (PostgreSQL) ---------------------------------------------------------------------------------


async def _trends_connections(url) -> int:
    found = await rows(url, "select count(*) as n from pg_stat_activity where datname = current_database() and application_name = 'ggwp-obs-trends'")
    return found[0]["n"]


@pytest.mark.asyncio
async def test_single_connection(pg_db_url):
    """D1: a channel holds at most one connection at any moment, and none between steps (design 3.3, 3.4; D4)."""
    url = pg_db_url
    clock = clock_at(T0)
    db, writer = await _acquire(url, clock, "proc-a")
    store = DbStateStore(writer, new_cipher())
    seen: list[int] = []
    stop = asyncio.Event()

    async def sample():
        while not stop.is_set():
            seen.append(await _trends_connections(url))
            await asyncio.sleep(0.002)

    async def one_step(index: int):
        table, row = _row("ggwp_obs_milestones", event=f"e{index}")
        async with writer.step() as step:
            await step.execute(insert(table).values(**row))
            await asyncio.sleep(0.01)

    async def renew_and_read():
        clock.advance(RENEW_SECONDS)
        await writer.ensure_fresh()
        async with writer.reading() as reader:
            await reader.execute(select(obs_milestones.c.id))
        await store.save(RuntimeState(budget=BudgetDay(TARGET, reserved=1).to_dict()))
        await store.load()

    try:
        sampler = asyncio.create_task(sample())
        await asyncio.gather(*(one_step(index) for index in range(8)), renew_and_read(), renew_and_read())
        await asyncio.sleep(0.05)
        stop.set()
        await sampler
        assert max(seen) == 1, seen
        assert await count(url, "ggwp_obs_milestones") == 8
        for index in range(3):  # between steps: none
            await one_step(100 + index)
            await _until(lambda: _none_open(url), timeout=2)
    finally:
        stop.set()
        await db.dispose()


async def _none_open(url) -> bool:
    return await _trends_connections(url) == 0


# ---- edges -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writer_edges(obs_url, caplog):
    db = open_db(obs_url)
    try:
        with pytest.raises(ValueError):
            await LeasedWriter.acquire(db, "youtube", clock=clock_at(T0))
        with pytest.raises(ValueError):
            await LeasedWriter.acquire(db, "trends", clock=clock_at(T0), owner="\x00bad")
        writer = await LeasedWriter.acquire(db, "trends", clock=clock_at(T0))  # the default owner: host, pid, random
        owner = writer.token.owner
        assert owner.count(":") >= 2 and len(owner) <= 128 and owner.isprintable()
        assert "trends" in repr(writer) and owner not in repr(DbStateStore(writer, new_cipher()))
        await writer.release()
        await writer.release()  # twice is once
        with pytest.raises(RuntimeError):
            async with writer.step():
                pass
        # A release that cannot reach the row is logged, never raised over the run's own outcome.
        other = await LeasedWriter.acquire(db, "gsc", clock=clock_at(T0))
        with pytest.raises(ValueError):
            DbStateStore(other, new_cipher())  # the Trends state lives on the trends row only
        async with other:
            await execute(obs_url, "delete from ggwp_obs_runtime where channel = 'gsc'")
        assert "lease not released" in caplog.text
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_unreadable_runtime_row_at_acquire_exit_3(obs_url):
    """A runtime table the lease cannot read is exit 3 (D34), with the database's SQLSTATE on PostgreSQL."""
    from ggwork_pick.observe.errors import StateUnavailable, sqlstate

    await execute(obs_url, "drop table ggwp_obs_runtime")
    db = open_db(obs_url)
    try:
        with pytest.raises(StateUnavailable) as failed:
            await LeasedWriter.acquire(db, "trends", clock=clock_at(T0))
        assert exit_code_for(failed.value) == ExitCode.STATE_UNAVAILABLE
        assert sqlstate(failed.value) == ("42P01" if is_postgres(obs_url) else None)
    finally:
        await db.dispose()
