"""TR-14: the executor's wiring, seen from outside the process (plan TR-14; critique C-17; counterexamples 2 and 10).

- The pacing envelope is asserted on the transport's own log (FakeGoogle records when each request left and came back
  on the ManualClock), for a whole session with a 5xx retry, a 429, its pause and the probe after it; and it must fail
  when the executor's pacer is swapped for a no-op (counterexample 2): the envelope rests on the wiring, not on TR-03.
- Requests, budget reservations and request rows agree one for one.
- A lease taken over while a request is out: the old owner commits no request row, budget or raw row (counterexample 10).
- The start-up error matrix TR-13 ran on its stand-in entry, rerun through the real __main__: zero HTTP in every case.
- The acceptance run: one simulated canary2 night end to end on the local database, the clock accelerated.
"""

import asyncio
import time
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from obs_db_helpers import execute, is_postgres, migrated, open_db, rows, runtime_row
from test_db_state import UNREADABLE, _full_state, _save
from test_selfcheck import MISMATCHES
from trends_fake_google import PHASES, FakeGoogle
from trends_session_helpers import (
    EVE,
    TARGET,
    at,
    batches,
    budget_requests,
    raw_rows,
    recent_catalog,
    request_rows,
    seed_catalog,
    trends_env,
    trigger,
    write_controls,
)
from trends_session_sim import Sent, assert_envelope_bounds

from ggwork_pick.observe import lease
from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.crypto import load_cipher
from ggwork_pick.observe.db import APPLICATION_NAMES, ObsDatabase, StepLimits, database_url
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import LEASE_SECONDS, LeasedWriter, locked_runtime
from ggwork_pick.observe.trends import breaker

TRANSIENT = frozenset({"503", "timeout"})
UNIT_HEADS = ("warmup", "explore")
FAULTS = {8: "503", 21: "timeout", 40: "429"}  # a retried multiline, a retried explore, a pause and its probe


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


async def _night(url, tmp_path, *, dramas: int, controls: int = 2):
    catalog = recent_catalog(dramas)
    await seed_catalog(url, catalog)
    return write_controls(tmp_path, catalog[:controls])


def _unit_numbers(google: FakeGoogle) -> Iterator[int]:
    """Which unit each request belonged to, read off the transport log: a warm-up or an explore starts a unit, except
    the one that repeats its unit's first request after a 5xx or a timeout (TR-03's RETRY reruns the unit)."""
    unit, head, failed = 0, None, False
    for seen in google.seen:
        key = (seen.phase, seen.terms, seen.geo)
        if seen.phase in UNIT_HEADS and not (failed and key == head):
            unit, head, failed = unit + 1, key, False
        failed = (failed and seen.phase not in UNIT_HEADS) or seen.answer in TRANSIENT
        yield unit


def transport_log(google: FakeGoogle) -> tuple[Sent, ...]:
    """FakeGoogle's log in trends_session_sim's shape (only the unit and the two instants are asserted on)."""
    return tuple(
        Sent(seen.ordinal, unit, 0, seen.sent_at, seen.done_at, breaker.Signal.SUCCESS, False, False)
        for seen, unit in zip(google.seen, _unit_numbers(google), strict=True)
    )


class NoopPacer:
    """[counterexample 2] Lets every request go at once; the breaker and the budget stay wired as they are."""

    def ready_at(self, state, *, now, first_in_unit, half_speed, unit_requests=1):
        return now

    def record(self, state, *, sent_at, done_at, rng, half_speed):
        return state


async def _faulty_night(url, tmp_path, pacer=None) -> FakeGoogle:
    controls = await _night(url, tmp_path, dramas=60)
    clock = ManualClock(at(EVE, 22, 0))
    google = FakeGoogle(clock, script=FAULTS)
    assert await trigger(trends_env(url), clock, google, controls=controls, pacer=pacer) == ExitCode.OK
    return google


async def _assert_one_for_one(url, google: FakeGoogle) -> list[dict]:
    """Every request that left: one budget reservation and one request row, nothing more (design 3.3)."""
    sent = await request_rows(url)
    (batch,) = await batches(url)
    assert len(google.seen) == len(sent) == await budget_requests(url) == batch["requests"]
    assert [row["sent_at"] for row in sent] == [stamp(seen.sent_at) for seen in google.seen]
    return sent


# ---- the envelope on the transport log (counterexample 2) ---------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_level_envelope(obs_url, tmp_path):
    google = await _faulty_night(obs_url, tmp_path)
    sent = await _assert_one_for_one(obs_url, google)
    items = [row["budget_item"] for row in sent]
    assert {"warmup", "retry", "probe"} <= set(items) and items[0] == "warmup"
    limited = next(seen for seen in google.seen if seen.answer == "429")
    after = google.seen[limited.ordinal]  # the next request: the probe, after the 30-minute pause
    assert after.sent_at - limited.done_at >= timedelta(minutes=30) and sent[limited.ordinal]["budget_item"] == "probe"
    assert_envelope_bounds(transport_log(google))
    (batch,) = await batches(obs_url)
    assert batch["breaker_events"] >= 1 and batch["coverage"] > 0.9


@pytest.mark.asyncio
async def test_transport_level_noop_pacer_red(obs_url, tmp_path):
    """[counterexample 2] The same night with the pacer a no-op: the budget and the rows still agree, the envelope
    does not hold."""
    google = await _faulty_night(obs_url, tmp_path, pacer=NoopPacer())
    await _assert_one_for_one(obs_url, google)
    with pytest.raises(AssertionError):
        assert_envelope_bounds(transport_log(google))


# ---- a takeover while a request is out (counterexample 10) --------------------------------------------------------


async def _written(url) -> dict:
    (batch,) = await batches(url)
    return {
        "requests": len(await request_rows(url)),
        "budget": await budget_requests(url),
        "raw": len(await raw_rows(url)),
        "batch": (batch["requests"], batch["fetched_units"], sorted(batch["summary_json"]["units"])),
    }


class TakeoverTransport(httpx.AsyncBaseTransport):
    """FakeGoogle behind a transport that, while the first multiline after `after` requests is out, lets the lease run
    out and has another process take it over; `before` is what the database held at that moment."""

    def __init__(self, google: FakeGoogle, url: str, clock: ManualClock, *, after: int):
        self.google, self.url, self.clock, self.after = google, url, clock, after
        self.before: dict | None = None
        self.generation: int | None = None

    def transport(self) -> httpx.AsyncBaseTransport:
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.before is None and PHASES[request.url.path] == "multiline" and len(self.google.seen) >= self.after:
            self.clock.advance(LEASE_SECONDS + 60)
            db = open_db(self.url)
            try:
                self.generation = (await LeasedWriter.acquire(db, "trends", clock=self.clock, owner="proc-new")).generation
            finally:
                await db.dispose()  # the lease stays with proc-new: never released
            self.before = await _written(self.url)
        return self.google.handler(request)


@pytest.mark.asyncio
async def test_takeover_mid_http_trends(obs_url, tmp_path):
    controls = await _night(obs_url, tmp_path, dramas=12)
    clock = ManualClock(at(EVE, 22, 0))
    google = FakeGoogle(clock)
    transport = TakeoverTransport(google, obs_url, clock, after=6)
    status = await trigger(trends_env(obs_url), clock, transport, controls=controls)
    assert status == ExitCode.FAILED and transport.before is not None
    assert await _written(obs_url) == transport.before  # no request row, budget count or raw row from the old owner
    assert transport.before["requests"] == len(google.seen) - 1  # the one that was out when the lease was lost
    row = await runtime_row(obs_url)
    assert (row["lease_owner"], row["lease_generation"]) == ("proc-new", transport.generation)
    assert google.seen[-1].phase == "multiline"  # and nothing went out after it


# ---- the start-up error matrix through the real __main__ ----------------------------------------------------------


SHORT_WAITS = {"row_lock_wait": StepLimits(lock_ms=300), "statement_timeout": StepLimits(lock_ms=5_000, statement_ms=300)}
STARTUP = {
    **{f"selfcheck:{name}": ExitCode.REFUSED for name in MISMATCHES},
    **{f"state:{name}": ExitCode.STATE_UNAVAILABLE for name in UNREADABLE},
    "runtime_row_missing": ExitCode.STATE_UNAVAILABLE,
    "lease_held": ExitCode.FAILED,
    "row_lock_wait": ExitCode.FAILED,
    "statement_timeout": ExitCode.FAILED,
    "control": ExitCode.OK,
}


async def _garbled(url, name: str) -> dict:
    """A saved state, then one of TR-13's ways it cannot be read back; the environment the run starts with."""
    env = trends_env(url)
    cipher = load_cipher(env)
    await _save(url, _full_state(), cipher, ManualClock(at(EVE, 22, 10)))
    run_cipher = await UNREADABLE[name](url, cipher)
    return env if run_cipher is cipher else trends_env(url)  # another key: the jar was sealed with the first


async def _startup_env(url, case: str) -> dict:
    kind, _, name = case.partition(":")
    if kind == "selfcheck":
        overrides, change, postgres_only = MISMATCHES[name]
        if postgres_only and not is_postgres(url):
            pytest.skip("SQLite has no roles")
        if change is not None:
            await change(url)
        return trends_env(url, **overrides)
    if kind == "state":
        return await _garbled(url, name)
    if case == "runtime_row_missing":
        await execute(url, "delete from ggwp_obs_runtime where channel = 'trends'")
    if case == "lease_held":
        db = open_db(url)
        try:
            await LeasedWriter.acquire(db, "trends", clock=ManualClock(at(EVE, 22, 7)), owner="proc-other")
        finally:
            await db.dispose()
    if case == "statement_timeout" and not is_postgres(url):
        pytest.skip("SQLite has no statement timeout")
    return trends_env(url)


async def _hold_row(url: str, held: asyncio.Event, done: asyncio.Event) -> None:
    """Another process mid-step: the trends runtime row locked until `done`."""
    holder = ObsDatabase(url, application_name="ggwp-obs-test-holder")
    try:
        async with holder.transaction() as conn:
            await locked_runtime(conn, "trends")
            held.set()
            await done.wait()
    finally:
        await holder.dispose()


def _short_database(limits: StepLimits):
    def open_database(channel: str, environ=None) -> ObsDatabase:
        return ObsDatabase(database_url(environ), application_name=APPLICATION_NAMES[channel], limits=limits)

    return open_database


async def _trigger_while_row_held(env, clock, google, controls) -> int:
    held, done = asyncio.Event(), asyncio.Event()
    holding = asyncio.create_task(_hold_row(env["PICK_DATABASE_URL"], held, done))
    try:
        await asyncio.wait_for(held.wait(), 5)
        return await asyncio.wait_for(trigger(env, clock, google, controls=controls), 30)
    finally:
        done.set()
        await holding


@pytest.mark.parametrize("case", sorted(STARTUP))
@pytest.mark.asyncio
async def test_startup_error_matrix(obs_url, tmp_path, case, monkeypatch):
    """Self-check mismatch 2; a state that cannot be read or a missing runtime row 3; a lease held elsewhere, a lock
    wait or a statement that times out at start 1. Zero HTTP in every case; the control runs."""
    controls = await _night(obs_url, tmp_path, dramas=3)
    env = await _startup_env(obs_url, case)
    clock = ManualClock(at(EVE, 22, 10))
    google = FakeGoogle(clock)
    if case in SHORT_WAITS:  # production waits 15 s for the row; the test gives up in 0.3 s
        monkeypatch.setattr(lease, "open_database", _short_database(SHORT_WAITS[case]))
        status = await _trigger_while_row_held(env, clock, google, controls)
    else:
        status = await trigger(env, clock, google, controls=controls)
    assert status == STARTUP[case]
    assert (google.seen == []) == (case != "control")
    assert [row["target_date"] for row in await batches(obs_url)] == (["2026-09-26"] if case == "control" else [])


# ---- acceptance: one simulated night, end to end ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulated_canary2_night(obs_url, tmp_path):
    """canary2 from 22:00: a 503 and a timeout retried, the whole plan fetched before 01:45, withheld; the envelope,
    the budget, the rows and the transport agree. The night takes seconds of the wall clock."""
    catalog = recent_catalog(260)
    await seed_catalog(obs_url, catalog)
    controls = write_controls(tmp_path, catalog[:6])
    clock = ManualClock(at(EVE, 22, 0))
    google = FakeGoogle(clock, script={50: "503", 120: "timeout"})
    started = time.perf_counter()
    assert await trigger(trends_env(obs_url, mode="canary2"), clock, google, controls=controls) == ExitCode.OK
    elapsed = time.perf_counter() - started
    sent = await _assert_one_for_one(obs_url, google)
    (batch,) = await batches(obs_url)
    assert (batch["collect_mode"], batch["outcome"], batch["window_end"]) == ("canary2", "withheld", stamp(at(EVE, 19)))
    assert batch["coverage"] == 1.0 and batch["summary_json"]["uncovered_units"] and batch["status_codes_json"] == []
    assert 380 <= len(sent) <= 430 and google.seen[-1].done_at < at(TARGET, 1, 45)
    assert_envelope_bounds(transport_log(google))
    assert {row["budget_item"] for row in sent} >= {"warmup", "market", "control", "title", "related", "retry"}
    budget = await rows(obs_url, "select budget_day, collect_mode, cap, requests from ggwp_obs_budget where channel = 'trends'")
    assert [(row["budget_day"], row["collect_mode"], row["cap"]) for row in budget] == [("2026-09-26", "canary2", 600)]
    span = google.seen[-1].done_at - google.seen[0].sent_at
    print(f"simulated canary2 night: {len(sent)} requests over {span} simulated, {elapsed:.1f} s wall")
