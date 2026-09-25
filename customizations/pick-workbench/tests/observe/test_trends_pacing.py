"""TR-03: the Trends pacer (design 4.2; plan TR-03, counterexample 2).

Every HTTP request counts, warm-up, probe and retry included. The envelope: at most 12 requests in any minute (a
bucket of 8 refilled at 4 a minute), at most 200 in any 60 minutes, 40 active minutes then 10 idle, 1.5-3 s between
the requests of one query unit, 25 s plus 0-10 s of jitter between units, about 2.9 a minute on average. The state is
immutable and survives a restart as a plain dict; time and randomness come from outside.
"""

import itertools
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import trends_session_sim as sim

from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.trends import breaker, pacing

START = datetime(2026, 9, 25, 20, 30, tzinfo=UTC)
THREE_HOURS = timedelta(hours=3)
SOURCE = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
TARGET = date(2026, 9, 26)  # START belongs to the session that publishes on the 26th


class NoopPacer:
    """Counterexample 2: a pacer that never makes anyone wait."""

    def ready_at(self, state, *, now, first_in_unit, half_speed, unit_requests=1):
        return now

    def record(self, state, *, sent_at, done_at, rng, half_speed):
        return state


def _three_hours(pacer=None, seed=7, units=None):
    clock = ManualClock(START)
    return sim.run(
        clock=clock,
        rng=random_source(seed),
        responder=sim.always(breaker.Signal.SUCCESS),
        units=units if units is not None else itertools.repeat(2),
        pacer=pacer,
        until=START + THREE_HOURS,
    )


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_pacing_envelope_3h(seed):
    result = _three_hours(seed=seed)
    sim.assert_envelope(result.sent, span=THREE_HOURS)
    assert all(s.sent_at < START + THREE_HOURS for s in result.sent)


def test_noop_pacer_fails_envelope():
    """[counterexample 2] The same driver with the pacer swapped for a no-op must break the envelope."""
    result = _three_hours(pacer=NoopPacer())
    with pytest.raises(AssertionError):
        sim.assert_envelope(result.sent, span=THREE_HOURS)


def test_bucket_caps_a_burst_at_12_a_minute():
    """One long unit (retries, related searches) only has the 1.5-3 s gaps; the bucket holds it to 8 + 4 a minute."""
    clock = ManualClock(START)
    result = sim.run(clock=clock, rng=random_source(3), responder=sim.always(breaker.Signal.SUCCESS), units=[120])
    times = [s.sent_at for s in result.sent]
    assert len(times) == 120
    assert 11 <= sim._max_in_window(times, sim.MINUTE) <= 12
    # The full bucket goes first; after it every request waits for a refill (4 a minute, one per 15 s).
    assert sim._seconds(times[-1], times[0]) >= (120 - 8) * 15


def test_hour_cap_binds_when_the_bucket_alone_would_not():
    """Bursting units across two segments would reach 8 + 4 x 50 > 200 in an hour without the 60-minute cap."""
    clock = ManualClock(START)
    result = sim.run(clock=clock, rng=random_source(5), responder=sim.always(breaker.Signal.SUCCESS), units=itertools.repeat(40), until=START + THREE_HOURS)
    times = [s.sent_at for s in result.sent]
    assert sim._max_in_window(times, sim.HOUR) <= 200
    loose = pacing.EnvelopePacer(replace(pacing.DEFAULT_PARAMS, hour_cap=10_000))
    unbounded = sim.run(
        clock=ManualClock(START),
        rng=random_source(5),
        responder=sim.always(breaker.Signal.SUCCESS),
        units=itertools.repeat(40),
        pacer=loose,
        until=START + THREE_HOURS,
    )
    assert sim._max_in_window([s.sent_at for s in unbounded.sent], sim.HOUR) > 200


def test_unit_is_never_split_by_the_rest():
    """The rest falls between units: explore's widget token is used by the very next request."""
    result = _three_hours(units=itertools.repeat(3))
    inside = [sim._seconds(c.sent_at, p.done_at) for p, c in itertools.pairwise(result.sent) if c.unit == p.unit]
    between = [sim._seconds(c.sent_at, p.done_at) for p, c in itertools.pairwise(result.sent) if c.unit != p.unit]
    # Three requests a unit outrun the refill (about 5.5 a minute against 4): the wait for the bucket and the hour
    # happens before a unit starts, never inside it, and so does every rest.
    assert all(1.5 <= gap <= 3.0 for gap in inside)
    assert sum(1 for gap in between if gap >= sim.REST) >= 3
    assert max(between) > 35.0  # the bucket did hold units back


def test_unit_start_waits_for_room_for_the_whole_unit():
    pacer = pacing.EnvelopePacer()
    state = pacer.record(pacing.initial_state(), sent_at=START, done_at=START, rng=random_source(1), half_speed=False)
    low = replace(state, tokens=2.0, unit_gap=0.0)
    assert pacer.ready_at(low, now=START, first_in_unit=True, half_speed=False, unit_requests=2) == START
    assert sim._seconds(pacer.ready_at(low, now=START, first_in_unit=True, half_speed=False, unit_requests=3), START) == pytest.approx(15.0)
    # A size above the bucket waits for a full bucket, not forever.
    assert sim._seconds(pacer.ready_at(low, now=START, first_in_unit=True, half_speed=False, unit_requests=50), START) == pytest.approx(90.0)
    # The hour: 199 requests in the last 60 minutes leave room for one, not for a unit of two.
    recent = tuple(START - timedelta(minutes=59) + timedelta(seconds=i) for i in range(199))
    full = replace(pacing.initial_state(), recent=recent)
    assert pacer.ready_at(full, now=START, first_in_unit=True, half_speed=False) == START
    assert pacer.ready_at(full, now=START, first_in_unit=True, half_speed=False, unit_requests=2) == recent[0] + timedelta(hours=1)
    # Only a unit's first request asks for room; the ones after it take what is left.
    assert pacer.ready_at(full, now=START, first_in_unit=False, half_speed=False, unit_requests=2) == START
    with pytest.raises(ValueError):
        pacer.ready_at(full, now=START, first_in_unit=True, half_speed=False, unit_requests=0)


def test_rest_counts_from_the_last_response():
    state = pacing.initial_state()
    pacer = pacing.EnvelopePacer()
    rng = random_source(11)
    for second in range(0, 40 * 60, 40):  # busy for 40 minutes, one request every 40 s
        at = START + timedelta(seconds=second)
        state = pacer.record(state, sent_at=at, done_at=at + timedelta(seconds=1), rng=rng, half_speed=False)
    late = START + timedelta(minutes=39, seconds=59)
    state = pacer.record(state, sent_at=late, done_at=late + timedelta(seconds=20), rng=rng, half_speed=False)
    assert state.segment_started_at == START
    ready = pacer.ready_at(state, now=late + timedelta(seconds=50), first_in_unit=True, half_speed=False)
    assert ready == late + timedelta(seconds=20) + timedelta(minutes=10)
    # A request of the unit already under way is not held back.
    within = pacer.ready_at(state, now=late + timedelta(seconds=21), first_in_unit=False, half_speed=False)
    assert within <= late + timedelta(seconds=23)


def test_a_long_idle_starts_a_new_segment():
    """A breaker pause is idle time: the request after it opens a fresh 40 minutes."""
    pacer = pacing.EnvelopePacer()
    rng = random_source(2)
    state = pacer.record(pacing.initial_state(), sent_at=START, done_at=START + timedelta(seconds=1), rng=rng, half_speed=False)
    after_pause = START + timedelta(minutes=39)
    state = pacer.record(state, sent_at=after_pause - timedelta(minutes=30), done_at=after_pause - timedelta(minutes=30), rng=rng, half_speed=False)
    state = pacer.record(state, sent_at=after_pause, done_at=after_pause + timedelta(seconds=1), rng=rng, half_speed=False)
    assert state.segment_started_at == after_pause
    ready = pacer.ready_at(state, now=after_pause + timedelta(minutes=2), first_in_unit=True, half_speed=False)
    assert ready == after_pause + timedelta(minutes=2)


def test_half_speed_doubles_the_gaps_and_halves_the_refill():
    pacer = pacing.EnvelopePacer()
    state = pacer.record(pacing.initial_state(), sent_at=START, done_at=START, rng=random_source(4), half_speed=False)
    intra = pacer.ready_at(state, now=START, first_in_unit=False, half_speed=False)
    slow_intra = pacer.ready_at(state, now=START, first_in_unit=False, half_speed=True)
    unit = pacer.ready_at(state, now=START, first_in_unit=True, half_speed=False)
    slow_unit = pacer.ready_at(state, now=START, first_in_unit=True, half_speed=True)
    assert sim._seconds(slow_intra, START) == pytest.approx(2 * sim._seconds(intra, START))
    assert sim._seconds(slow_unit, START) == pytest.approx(2 * sim._seconds(unit, START))
    assert 3.0 <= sim._seconds(slow_intra, START) <= 6.0 and 50.0 <= sim._seconds(slow_unit, START) <= 70.0
    empty = replace(state, tokens=0.0)
    assert sim._seconds(pacer.ready_at(empty, now=START, first_in_unit=False, half_speed=False), START) == pytest.approx(15.0)
    assert sim._seconds(pacer.ready_at(empty, now=START, first_in_unit=False, half_speed=True), START) == pytest.approx(30.0)


def test_half_speed_run_rate_halves():
    clock = ManualClock(START)
    fresh = breaker.initial_state(TARGET)
    slow = replace(fresh, day=replace(fresh.day, half_speed=True))
    result = sim.run(
        clock=clock,
        rng=random_source(9),
        responder=sim.always(breaker.Signal.SUCCESS),
        units=itertools.repeat(2),
        breaker_state=slow,
        until=START + timedelta(minutes=40),
    )
    assert all(s.half_speed for s in result.sent)
    assert 1.5 <= len(result.sent) / 40 <= 2.0  # about 3.6 a minute at full speed while active


def test_state_is_immutable_and_record_returns_a_new_one():
    pacer = pacing.EnvelopePacer()
    before = pacing.initial_state()
    after = pacer.record(before, sent_at=START, done_at=START + timedelta(seconds=1), rng=random_source(1), half_speed=False)
    assert before == pacing.initial_state() and after is not before
    with pytest.raises(AttributeError):
        after.tokens = 3  # type: ignore[misc]


def test_state_roundtrips_through_json():
    pacer = pacing.EnvelopePacer()
    rng = random_source(8)
    state = pacing.initial_state()
    for second in range(0, 600, 31):
        at = START + timedelta(seconds=second)
        state = pacer.record(state, sent_at=at, done_at=at + timedelta(seconds=0.5), rng=rng, half_speed=False)
    restored = pacing.PacingState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored == state
    assert pacing.PacingState.from_dict(pacing.initial_state().to_dict()) == pacing.initial_state()


def test_record_forgets_requests_older_than_an_hour():
    pacer = pacing.EnvelopePacer()
    rng = random_source(8)
    state = pacer.record(pacing.initial_state(), sent_at=START, done_at=START, rng=rng, half_speed=False)
    later = START + timedelta(minutes=61)
    state = pacer.record(state, sent_at=later, done_at=later, rng=rng, half_speed=False)
    assert state.recent == (later,)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "extra": 1},
        lambda d: {k: v for k, v in d.items() if k != "tokens"},
        lambda d: {**d, "tokens": "8"},
        lambda d: {**d, "tokens": float("nan")},
        lambda d: {**d, "last_done_at": "2026-09-25T20:30:00"},
        lambda d: {**d, "recent": ["2026-09-25T20:31:00.000000+00:00", "2026-09-25T20:30:00.000000+00:00"]},
        lambda d: {**d, "unit_gap": -1.0},
        lambda d: {**d, "unit_gap": True},
        lambda d: {**d, "recent": "2026-09-25T20:30:00.000000+00:00"},
    ],
)
def test_state_from_dict_refuses_bad_input(mutate):
    pacer = pacing.EnvelopePacer()
    state = pacer.record(pacing.initial_state(), sent_at=START, done_at=START, rng=random_source(1), half_speed=False)
    with pytest.raises(ValueError):
        pacing.PacingState.from_dict(mutate(state.to_dict()))


def test_record_refuses_naive_or_backwards_times():
    pacer = pacing.EnvelopePacer()
    with pytest.raises(ValueError):
        pacer.record(pacing.initial_state(), sent_at=START.replace(tzinfo=None), done_at=START, rng=random_source(1), half_speed=False)
    with pytest.raises(ValueError):
        pacer.record(pacing.initial_state(), sent_at=START, done_at=START - timedelta(seconds=1), rng=random_source(1), half_speed=False)


def test_params_refuse_nonsense():
    with pytest.raises(ValueError):
        pacing.PacingParams(bucket_capacity=0)
    with pytest.raises(ValueError):
        pacing.PacingParams(intra_unit_seconds=(3.0, 1.5))
    with pytest.raises(ValueError):
        pacing.PacingParams(rest_minutes=0)
    with pytest.raises(ValueError):
        pacing.PacingParams(hour_cap=0)


def test_default_params_are_design_4_2():
    params = pacing.DEFAULT_PARAMS
    assert (params.bucket_capacity, params.refill_per_minute, params.hour_cap) == (8, 4, 200)
    assert (params.segment_minutes, params.rest_minutes) == (40, 10)
    assert params.intra_unit_seconds == (1.5, 3.0) and params.inter_unit_seconds == (25.0, 35.0)


@pytest.mark.parametrize("module", ["pacing", "breaker", "budget"])
def test_state_machines_import_pure(module):
    """Pure state machines: no database, HTTP or validation library comes with them into the cron process."""
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(EXTENSION_API)])}\n"
        f"importlib.import_module('ggwork_pick.observe.trends.{module}')\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    foreign = ("sqlalchemy", "httpx", "pydantic", "fastapi", "alembic", "langgraph", "deerflow")
    assert [name for name in loaded if name.split(".")[0] in foreign] == []
    assert [name for name in loaded if name.startswith("ggwork_pick.") and not name.startswith("ggwork_pick.observe")] == []
