"""A reference driver for the TR-03 state machines, and the pacing envelope it must stay inside (plan TR-03, design 4.2).

The driver runs query units through pacing, the breaker and the budget on a ManualClock, in the order TR-14's executor
must follow for every HTTP request (warm-up, probe and retry included): roll the day over, ask stop_reason before a unit
starts, wait for both the pacer and the breaker, reserve the budget, send, record the pacing, observe the outcome. A
unit can fit when it starts and still run the budget out half-way (a retry or a probe is one request more): that unit
and every one after it are truncated. A fake responder stands in for Google. This is test code; the executor-level
wiring test is TR-14's (test_transport_level_envelope).

Two envelopes (design 4.2), for two kinds of run:
- assert_envelope_bounds: the upper bounds only, which hold for any run: 429s and pauses, probes, 5xx retries, half
  speed, units of two or three requests. This is the one TR-14's transport-level test reuses on its transport log.
- assert_envelope: the bounds plus the exact figures (average rate, the upper ends of the gaps, the rest count), which
  hold only for full-speed, all-success units (test_pacing_envelope_3h).
"""

import bisect
import itertools
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.trends import breaker, budget, pacing

Responder = Callable[[int, datetime, budget.BudgetDay | None], breaker.Signal]


@dataclass(frozen=True)
class Sent:
    ordinal: int  # 1-based, over the whole run
    unit: int
    index: int  # the request's place in its unit; a retry keeps the index it retries
    sent_at: datetime
    done_at: datetime
    signal: breaker.Signal
    probe: bool
    half_speed: bool


@dataclass(frozen=True)
class SimResult:
    sent: tuple[Sent, ...]
    covered: tuple[int, ...]
    uncovered: tuple[tuple[int, str], ...]
    decisions: tuple[breaker.Decision, ...]
    pacing: pacing.PacingState
    breaker: breaker.BreakerState
    budget: budget.BudgetDay | None


@dataclass(frozen=True)
class _Run:
    pacing: pacing.PacingState
    breaker: breaker.BreakerState
    budget: budget.BudgetDay | None
    sent: tuple[Sent, ...] = ()
    decisions: tuple[breaker.Decision, ...] = ()


def always(signal: breaker.Signal) -> Responder:
    return lambda ordinal, sent_at, day: signal


def script(signals: dict[int, breaker.Signal], default: breaker.Signal = breaker.Signal.SUCCESS) -> Responder:
    """Answer with signals[ordinal] where given, `default` otherwise."""
    return lambda ordinal, sent_at, day: signals.get(ordinal, default)


def run(
    *,
    clock: ManualClock,
    rng: random.Random,
    responder: Responder,
    units: Iterable[int],
    pacer: pacing.Pacer | None = None,
    breaker_state: breaker.BreakerState | None = None,
    budget_day: budget.BudgetDay | None = None,
    limits: budget.ModeLimits | None = None,
    until: datetime | None = None,
    latency: tuple[float, float] = (0.2, 1.0),
) -> SimResult:
    """Run `units` (each an int: its HTTP requests) until they run out, the breaker halts the day, the deadline of
    `limits` passes or `until` is reached. Without `limits` there is no budget and no deadline."""
    pacer = pacer or pacing.EnvelopePacer()
    target = budget.target_date_of(clock.now())
    state = _Run(
        pacing=pacing.initial_state(),
        breaker=breaker.for_target_date(breaker_state or breaker.initial_state(target), target, now=clock.now()),
        budget=budget.for_target_date(budget_day or budget.BudgetDay(target), target) if limits else None,
    )
    covered, uncovered = (), ()
    for unit, size in enumerate(units):
        reason = _stop(state, limits, clock.now(), size)
        if reason is not None or (until is not None and clock.now() >= until):
            uncovered = (*uncovered, *_rest(unit, units, reason)) if reason else uncovered
            break
        state, outcome = _unit(state, unit, size, clock=clock, rng=rng, pacer=pacer, responder=responder, limits=limits, until=until, latency=latency)
        if outcome == "not_started":
            break
        if outcome == budget.TRUNCATED:  # the budget ran out half-way: nothing after this unit can start either
            uncovered = (*uncovered, *_rest(unit, units, outcome))
            break
        if outcome == "covered":
            covered = (*covered, unit)
        else:
            uncovered = (*uncovered, (unit, outcome))
    return SimResult(state.sent, covered, uncovered, state.decisions, state.pacing, state.breaker, state.budget)


def _stop(state: _Run, limits: budget.ModeLimits | None, now: datetime, need: int) -> str | None:
    if limits is None:
        return budget.SKIPPED_BREAKER if breaker.halted(state.breaker) else None
    return budget.stop_reason(breaker_state=state.breaker, day=state.budget, limits=limits, now=now, need=need)


def _rest(unit: int, units: Iterable[int], reason: str) -> tuple[tuple[int, str], ...]:
    """The unit that could not start and, when `units` is a finite list, every one after it."""
    later = range(unit + 1, len(units)) if isinstance(units, list | tuple) else ()
    return tuple((u, reason) for u in itertools.chain((unit,), later))


def _unit(state: _Run, unit: int, size: int, *, clock, rng, pacer, responder, limits, until, latency) -> tuple[_Run, str]:
    index = 0
    while index < size:
        wait = max(
            pacer.ready_at(state.pacing, now=clock.now(), first_in_unit=index == 0, half_speed=state.breaker.day.half_speed, unit_requests=size),
            breaker.ready_at(state.breaker, now=clock.now()),
        )
        clock.advance((wait - clock.now()).total_seconds())
        if index == 0 and until is not None and clock.now() >= until:
            return state, "not_started"
        if limits is not None and clock.now() >= limits.window(state.budget.target_date)[1]:
            return state, budget.DEADLINE_REASON
        try:
            state, decision = _request(state, unit, index, clock=clock, rng=rng, pacer=pacer, responder=responder, limits=limits, latency=latency)
        except budget.BudgetExhausted:  # nothing was sent: the reservation comes first
            return state, budget.TRUNCATED
        if decision.action is breaker.Action.RETRY:
            continue
        if decision.abandon_unit:
            return state, budget.SKIPPED_BREAKER
        index += 1
    return state, "covered"


def _request(state: _Run, unit: int, index: int, *, clock, rng, pacer, responder, limits, latency) -> tuple[_Run, breaker.Decision]:
    day = budget.reserve(state.budget, limits) if limits is not None else None  # reserved before the request leaves
    ordinal = len(state.sent) + 1
    sent_at, probe, half = clock.now(), state.breaker.day.probe_due, state.breaker.day.half_speed
    signal = responder(ordinal, sent_at, day)
    clock.advance(rng.uniform(*latency))
    paced = pacer.record(state.pacing, sent_at=sent_at, done_at=clock.now(), rng=rng, half_speed=half)
    tripped, decision = breaker.observe(state.breaker, signal, now=clock.now(), rng=rng)
    if day is not None and decision.action in (breaker.Action.PAUSE, breaker.Action.EXTINGUISH):
        day = budget.note_limit(day, ordinal=day.reserved, at=sent_at)
    record = Sent(ordinal, unit, index, sent_at, clock.now(), signal, probe, half)
    updated = replace(state, pacing=paced, breaker=tripped, budget=day, sent=(*state.sent, record), decisions=(*state.decisions, decision))
    return updated, decision


# ---- the envelope (design 4.2) --------------------------------------------------------------------------------------

MINUTE, HOUR = 60.0, 3600.0
SEGMENT, REST = 40 * MINUTE, 10 * MINUTE
UNIT_TAIL = 10.0  # a unit that started inside its segment finishes within this at full speed (3 s gap + 1 s latency)
# ... and within this in any run: three requests at half speed (6 s gaps), each retried once 30-60 s later, latency.
MAX_UNIT_TAIL = 240.0


def _seconds(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds()


def _max_in_window(times: list[datetime], width: float) -> int:
    """The most requests any window [t, t + width) holds, t running over the (ascending) send times."""
    step = timedelta(seconds=width)
    return max((bisect.bisect_left(times, start + step, lo=i) - i for i, start in enumerate(times)), default=0)


def _segments(sent: tuple[Sent, ...]) -> list[list[Sent]]:
    """Consecutive requests split wherever the line was idle for the rest time or longer."""
    segments: list[list[Sent]] = []
    for previous, current in itertools.pairwise((None, *sent)):
        if previous is None or _seconds(current.sent_at, previous.done_at) >= REST:
            segments = [*segments, [current]]
        else:
            segments = [*segments[:-1], [*segments[-1], current]]
    return segments


def assert_envelope_bounds(sent: tuple[Sent, ...], *, tail: float = MAX_UNIT_TAIL) -> None:
    """Design 4.2's upper bounds, for any run (retries, pauses, probes, half speed, units of any size): at most 12
    requests in any minute and 200 in any 60 minutes; at least 1.5 s between two requests of one unit and 25 s between
    units; every unit starts inside its segment's first 40 minutes and a segment ends within `tail` of them, so a rest
    of 10 minutes or more follows every 40 active minutes. Raises AssertionError when one is broken."""
    times = [s.sent_at for s in sent]
    assert _max_in_window(times, MINUTE) <= 12, f"a minute held {_max_in_window(times, MINUTE)} requests"
    assert _max_in_window(times, HOUR) <= 200, f"an hour held {_max_in_window(times, HOUR)} requests"
    first_of_unit = {s.unit: s for s in reversed(sent)}  # a retry of a unit's first request does not start it again
    for segment in _segments(sent):
        starts = [s for s in segment if first_of_unit[s.unit] is s]
        assert all(_seconds(s.sent_at, segment[0].sent_at) < SEGMENT for s in starts), "a unit started after 40 active minutes"
        assert _seconds(segment[-1].done_at, segment[0].sent_at) <= SEGMENT + tail, "a segment ran past 40 minutes"
    for previous, current in itertools.pairwise(sent):
        gap = _seconds(current.sent_at, previous.done_at)
        least = 1.5 if current.unit == previous.unit else 25.0
        assert gap >= least, f"{gap:.2f} s between two requests" + (" of one unit" if least == 1.5 else " of two units")


def assert_envelope(sent: tuple[Sent, ...], *, span: timedelta, rate: tuple[float, float] = (2.6, 3.2)) -> None:
    """Design 4.2 exactly, on a run of full-speed, all-success units: the bounds, plus an average of `rate` requests a
    minute, gaps of at most 3 s inside a unit and 35 s between units (or a rest), and a rest every 50 minutes of `span`.
    Retries, pauses and half speed break these figures by design; such a run takes assert_envelope_bounds."""
    assert_envelope_bounds(sent, tail=UNIT_TAIL)
    segments = _segments(sent)
    assert len(segments) >= int(span.total_seconds() // (SEGMENT + REST)) + 1, "no 10-minute rest after 40 active minutes"
    per_minute = len(sent) / (span.total_seconds() / MINUTE)
    assert rate[0] <= per_minute <= rate[1], f"{per_minute:.2f} requests a minute"
    for previous, current in itertools.pairwise(sent):
        gap = _seconds(current.sent_at, previous.done_at)
        if current.unit == previous.unit:
            assert 1.5 <= gap <= 3.0, f"{gap:.2f} s between two requests of one unit"
        else:
            assert 25.0 <= gap <= 35.0 or gap >= REST, f"{gap:.2f} s between two units"
