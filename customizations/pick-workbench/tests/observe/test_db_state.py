"""TR-13: the Trends runtime state in the database (plan D17, D18, D23, D34; design 4.3, 4.4).

DbStateStore keeps TR-04's RuntimeState on the channel's runtime row, with the cookie jar sealed into its Text column,
and the budget on the (channel, target_date) budget row. A state that cannot be read, or a runtime row that is gone,
stops the day with exit 3 before any HTTP; the stand-in collector day in obs_db_helpers counts what it would send.
"""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import (
    T0,
    TARGET,
    UA,
    CountingTransport,
    as_json,
    budget_rows,
    clock_at,
    collector_day,
    collector_env,
    execute,
    is_postgres,
    migrated,
    new_cipher,
    open_db,
    runtime_row,
    stamp,
)

from ggwork_pick.observe.admin import cmd_reset_disable
from ggwork_pick.observe.errors import ExitCode, StateUnavailable, sqlstate
from ggwork_pick.observe.lease import DbStateStore, LeasedWriter
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends import breaker, budget, pacing
from ggwork_pick.observe.trends.cookies import Cookie, CookieJar

SECRET = "nid-value-that-must-stay-sealed"


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


def _jar() -> CookieJar:
    return CookieJar.fresh(UA).warmed([Cookie("NID", SECRET, ".google.com")], day=TARGET, now=T0)


def _paused_breaker(now: datetime) -> breaker.BreakerState:
    state, _ = breaker.observe(breaker.initial_state(TARGET), breaker.Signal.RATE_LIMITED, now=now, rng=breaker_rng())
    return state


def breaker_rng():
    from ggwork_pick.observe.clock import random_source

    return random_source(7)


def _full_state(now: datetime = T0, *, reserved: int = 3) -> RuntimeState:
    tripped = _paused_breaker(now)
    return RuntimeState(
        paused_until=tripped.day.paused_until,
        pacing=pacing.initial_state().to_dict(),
        breaker=tripped.to_dict(),
        budget=budget.BudgetDay(TARGET, reserved=reserved).to_dict(),
        cookie_jar=_jar(),
    )


async def _session(url, clock, owner="proc-a"):
    db = open_db(url)
    return db, await LeasedWriter.acquire(db, "trends", clock=clock, owner=owner)


async def _save(url, state, cipher, clock, *, limits=None):
    db, writer = await _session(url, clock)
    try:
        async with writer:
            await DbStateStore(writer, cipher, limits=limits).save(state)
    finally:
        await db.dispose()


async def _load(url, cipher, clock):
    db, writer = await _session(url, clock, owner="proc-load")
    try:
        async with writer:
            return await DbStateStore(writer, cipher).load()
    finally:
        await db.dispose()


# ---- round trip -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_runtime_row_loads_as_an_empty_state(obs_url):
    """D34: the seeded row is the first run's state; no init step, nothing guessed."""
    assert await _load(obs_url, new_cipher(), clock_at(T0)) == RuntimeState()


@pytest.mark.asyncio
async def test_restart_keeps_state_pause_and_sealed_jar(obs_url):
    cipher = new_cipher()
    state = _full_state()
    await _save(obs_url, state, cipher, clock_at(T0), limits=budget.mode_limits("canary1"))
    # A restart: another process, the same key.
    loaded = await _load(obs_url, cipher, clock_at(T0 + timedelta(minutes=5)))
    assert loaded == state
    assert loaded.is_paused(T0 + timedelta(minutes=5))
    assert loaded.cookie_jar.request_headers(T0)["User-Agent"] == UA
    row = await runtime_row(obs_url)
    assert (row["user_agent"], row["cookie_warmed_at"], row["breaker_level"]) == (UA, "2026-09-26", 1)
    assert row["paused_until"] == stamp(state.paused_until)
    assert SECRET not in json.dumps(row, default=str)  # the jar is ciphertext only (D18)
    assert set(as_json(row["state_json"])) == {"format", "pacing", "breaker"}
    [day] = await budget_rows(obs_url)
    assert (day["budget_day"], day["requests"], day["collect_mode"], day["cap"], day["http_429"]) == ("2026-09-26", 3, "canary1", 220, 1)


# ---- budget: reserved before sending, never given back, one row per target date (D23) -----------------------------


@pytest.mark.asyncio
async def test_budget_not_refunded(obs_url):
    cipher, clock = new_cipher(), clock_at(T0)
    limits = budget.mode_limits("canary1")
    db, writer = await _session(obs_url, clock)
    store = DbStateStore(writer, cipher, limits=limits)
    try:
        day = budget.BudgetDay(TARGET)
        for _ in range(2):  # reserve, then send; the second request times out, its answer never comes
            day = budget.reserve(day, limits)
            await store.save(RuntimeState(budget=day.to_dict()))
        assert (await budget_rows(obs_url))[0]["requests"] == 2
        # Nothing gives a reservation back: a lower count is refused and the row keeps its count.
        with pytest.raises(StateUnavailable):
            await store.save(RuntimeState(budget=budget.BudgetDay(TARGET, reserved=1).to_dict()))
        assert (await budget_rows(obs_url))[0]["requests"] == 2
        await writer.release()
    finally:
        await db.dispose()
    # After a crash and restart the reservations are still spent.
    loaded = await _load(obs_url, cipher, clock_at(T0 + timedelta(minutes=30)))
    assert loaded.section("budget", budget.BudgetDay.from_dict).reserved == 2


@pytest.mark.asyncio
async def test_cross_midnight_budget_same_target_date(obs_url):
    """22:30 and 00:10 UTC are one session and one target date (D23): the 00:10 run continues the same budget row and
    finds the day still put out; midnight neither refills the budget nor relights the day."""
    cipher = new_cipher()
    evening = datetime(2026, 9, 25, 22, 30, tzinfo=UTC)
    target = budget.target_date_of(evening)
    assert target == TARGET
    day = budget.BudgetDay(target, reserved=5)
    out, _ = breaker.observe(breaker.initial_state(target), breaker.Signal.WALL, now=evening, rng=breaker_rng())
    await _save(obs_url, RuntimeState(breaker=out.to_dict(), budget=day.to_dict()), cipher, clock_at(evening))

    after_midnight = datetime(2026, 9, 26, 0, 10, tzinfo=UTC)
    assert budget.target_date_of(after_midnight) == target
    loaded = await _load(obs_url, cipher, clock_at(after_midnight))
    carried = budget.for_target_date(loaded.section("budget", budget.BudgetDay.from_dict), budget.target_date_of(after_midnight))
    assert carried.reserved == 5
    kept = breaker.for_target_date(loaded.section("breaker", breaker.BreakerState.from_dict), target, now=after_midnight)
    assert breaker.halted(kept)
    await _save(
        obs_url, RuntimeState(breaker=kept.to_dict(), budget=budget.reserve(carried, budget.mode_limits("stable")).to_dict()), cipher, clock_at(after_midnight)
    )
    assert [(row["budget_day"], row["requests"], row["extinguish_reason"]) for row in await budget_rows(obs_url)] == [("2026-09-26", 6, "wall")]
    [row] = await budget_rows(obs_url)
    assert row["extinguished_at"] == stamp(evening)  # the first time it went out, kept


# ---- a state that cannot be read: exit 3, zero HTTP (design 4.3; D34) ---------------------------------------------


async def _garble_jar_key(url, cipher):
    return new_cipher()  # the jar was sealed with `cipher`; the run has another key


async def _garble_ua(url, cipher):
    await execute(url, "update ggwp_obs_runtime set user_agent = 'Other/1.0' where channel = 'trends'")
    return cipher


async def _garble_jar_without_ua(url, cipher):
    await execute(url, "update ggwp_obs_runtime set user_agent = null where channel = 'trends'")
    return cipher


async def _garble_format(url, cipher):
    document = json.dumps({"format": 99, "pacing": None, "breaker": None})
    await execute(url, "update ggwp_obs_runtime set state_json = :d where channel = 'trends'", d=document)
    return cipher


async def _garble_breaker(url, cipher):
    document = json.dumps({"format": 1, "pacing": None, "breaker": {"day": "not a day"}})
    await execute(url, "update ggwp_obs_runtime set state_json = :d where channel = 'trends'", d=document)
    return cipher


async def _garble_pause(url, cipher):
    await execute(url, "update ggwp_obs_runtime set paused_until = 'someday' where channel = 'trends'")
    return cipher


async def _garble_budget_row(url, cipher):
    await execute(url, "update ggwp_obs_budget set requests_before_first_limit = requests + 1, first_limited_at = :t", t=stamp(T0))
    return cipher


async def _garble_lease_until(url, cipher):
    await execute(url, "update ggwp_obs_runtime set lease_owner = 'proc-x', lease_until = 'soon' where channel = 'trends'")
    return cipher


async def _drop_budget_table(url, cipher):
    await execute(url, "drop table ggwp_obs_budget")
    return cipher


UNREADABLE = {
    "jar_sealed_with_another_key": _garble_jar_key,
    "ua_column_disagrees_with_jar": _garble_ua,
    "jar_without_its_ua": _garble_jar_without_ua,
    "unknown_state_format": _garble_format,
    "breaker_section_garbled": _garble_breaker,
    "pause_not_a_time": _garble_pause,
    "budget_row_inconsistent": _garble_budget_row,
    "lease_until_not_a_time": _garble_lease_until,
    "budget_table_unreadable": _drop_budget_table,
}


@pytest.mark.parametrize("garble", sorted(UNREADABLE))
@pytest.mark.asyncio
async def test_state_unreadable_no_run(obs_url, garble):
    cipher = new_cipher()
    await _save(obs_url, _full_state(), cipher, clock_at(T0))
    run_cipher = await UNREADABLE[garble](obs_url, cipher)
    transport = CountingTransport()
    status = await collector_day(collector_env(obs_url), run_cipher, clock_at(T0 + timedelta(hours=1)), transport)
    assert (status, transport.sent) == (ExitCode.STATE_UNAVAILABLE, [])


@pytest.mark.asyncio
async def test_readable_state_runs(obs_url):
    """The control for the test above: the same day on an intact state sends its first request."""
    cipher = new_cipher()
    await _save(obs_url, _full_state(), cipher, clock_at(T0))
    transport = CountingTransport()
    status = await collector_day(collector_env(obs_url), cipher, clock_at(T0 + timedelta(hours=1)), transport)
    assert (status, len(transport.sent)) == (ExitCode.OK, 1)


@pytest.mark.asyncio
async def test_unreadable_table_keeps_its_sqlstate(obs_url):
    """Exit 3 still tells a missing table or grant from an unreachable database (errors.py; runbook README)."""
    await _drop_budget_table(obs_url, None)
    db, writer = await _session(obs_url, clock_at(T0))
    try:
        with pytest.raises(StateUnavailable) as failed:
            await DbStateStore(writer, new_cipher()).load()
        assert sqlstate(failed.value) == ("42P01" if is_postgres(obs_url) else None)
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_missing_runtime_row_exit_3(obs_url):
    """D34: a deleted runtime row is never a fresh start; the day does not run and nothing is sent."""
    await execute(obs_url, "delete from ggwp_obs_runtime where channel = 'trends'")
    transport = CountingTransport()
    status = await collector_day(collector_env(obs_url), new_cipher(), clock_at(T0), transport)
    assert (status, transport.sent) == (ExitCode.STATE_UNAVAILABLE, [])
    assert await runtime_row(obs_url) is None  # and nothing put it back
    reset = await cmd_reset_disable.run(["--operator", "wzb"], environ=collector_env(obs_url), clock=clock_at(T0))
    assert reset == ExitCode.STATE_UNAVAILABLE


# ---- disabled_7d is cleared by reset-disable alone ------------------------------------------------------------------


def _disabled(target: date) -> breaker.BreakerState:
    days = tuple(target - timedelta(days=back) for back in (4, 2, 0))
    return breaker.BreakerState(breaker.BreakerDay(target, extinguished="trips"), extinguished_days=days, disabled_on=target)


@pytest.mark.asyncio
async def test_reset_disable(obs_url, capsys):
    cipher = new_cipher()
    env = collector_env(obs_url)
    await _save(obs_url, RuntimeState(breaker=_disabled(TARGET).to_dict()), cipher, clock_at(T0))
    assert (await runtime_row(obs_url))["disabled_at"] == stamp(T0)

    # A new target date keeps it (breaker.for_target_date), and the store refuses a state that dropped it.
    next_day = T0 + timedelta(days=1)
    loaded = await _load(obs_url, cipher, clock_at(next_day))
    carried = breaker.for_target_date(loaded.section("breaker", breaker.BreakerState.from_dict), TARGET + timedelta(days=1), now=next_day)
    assert carried.disabled_on == TARGET
    with pytest.raises(StateUnavailable):
        await _save(obs_url, RuntimeState(breaker=breaker.clear_disabled(carried).to_dict()), cipher, clock_at(next_day))
    assert (await runtime_row(obs_url))["disabled_at"] == stamp(T0)

    # Not while a collector holds the lease.
    db, writer = await _session(obs_url, clock_at(next_day), owner="proc-running")
    try:
        assert await cmd_reset_disable.run(["--operator", "wzb"], environ=env, clock=clock_at(next_day)) == ExitCode.FAILED
        assert (await runtime_row(obs_url))["reset_by"] is None
        await writer.release()
    finally:
        await db.dispose()

    assert await cmd_reset_disable.run(["--operator", "wzb"], environ=env, clock=clock_at(next_day)) == ExitCode.OK
    row = await runtime_row(obs_url)
    assert (row["disabled_at"], row["reset_by"], row["reset_at"]) == (None, "wzb", stamp(next_day))
    cleared = breaker.BreakerState.from_dict(as_json(row["state_json"])["breaker"])
    assert cleared.disabled_on is None and cleared.extinguished_days == _disabled(TARGET).extinguished_days  # history kept
    assert "wzb" in capsys.readouterr().out

    # Nothing left to clear: nothing written.
    later = next_day + timedelta(hours=1)
    assert await cmd_reset_disable.run(["--operator", "someone"], environ=env, clock=clock_at(later)) == ExitCode.OK
    assert (await runtime_row(obs_url))["reset_by"] == "wzb"


def test_reset_disable_needs_an_operator(capsys):
    with pytest.raises(SystemExit) as refused:
        cmd_reset_disable.main([], environ={})
    assert refused.value.code == ExitCode.REFUSED
    assert cmd_reset_disable.main(["--operator", "\x07bell"], environ={}) == ExitCode.REFUSED
    assert cmd_reset_disable.main(["--operator", "x" * 129], environ={}) == ExitCode.REFUSED
    assert cmd_reset_disable.main(["--operator", "wzb"], environ={}) == ExitCode.REFUSED  # no PICK_DATABASE_URL
    assert "x" * 129 not in capsys.readouterr().err


def test_reset_disable_is_discovered():
    from ggwork_pick.observe.admin.__main__ import discover

    assert discover()["reset-disable"] == "ggwork_pick.observe.admin.cmd_reset_disable"
