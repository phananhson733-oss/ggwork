"""How long a Trends night takes at a pace, and whether a mode's plan fits its window (plan section 9; G3 review seam 2
and P2-1).

The check this replaces (plan / 2.9 + 40 minutes) priced one breaker pause but not the half speed after it, which lasts
the rest of the target date (breaker.py), and it knew one pace only. This one replays a night request by request
through the real pacer (pacing.EnvelopePacer with the preset's parameters) and the real breaker and budget, on a clock
of its own: every drawn gap at the long end of its range, every answer taking LATENCY_SECONDS. Three nights, each
deterministic:

- CLEAR: no limit signal at all;
- ONE_LIMIT: the 56th request of the night (the warm-up is the first) is a 429, as stage 0's first limit was: its unit
  is lost, nothing goes out for 30 minutes, the probe comes back fine, and the rest of the target date runs at half
  speed;
- RESUMED: ONE_LIMIT, and the process dies with the first request sent at or after midnight UTC out. The first cron
  trigger after its lease has run out resumes the night on the same target date and window_end (D23), with the budget,
  the breaker (still at half speed) and the pacing as they were committed; the unit that was out is sent again from
  its first request. A night that is over before midnight never dies: RESUMED is ONE_LIMIT then.

Every estimate then adds MARGIN to each unit's finish, so a unit only counts as covered when it would still be done
MARGIN before the deadline. A mode is accepted at a pace (settings.py refuses it otherwise, exit 2, before anything
is read or sent) only if CLEAR covers every unit of the largest plan the mode allows and ONE_LIMIT at least
MIN_LIMITED_COVERAGE of them.

Where the estimate stands against a night (tests/observe/test_trends_capacity.py runs the three nights through the real
executor, on a ManualClock against a MockTransport, and checks that the estimate never finishes sooner and never
covers more):
- MARGIN is what makes it the later one, and the only thing. At the user pace the bucket's refill sets the speed:
  LATENCY_SECONDS and the long-end gaps hardly count (both at their other extreme, 0 s and the short end, move the
  canary modes' estimates by under a minute).
- The replay of a night's own task list, without MARGIN, finishes within a minute of the executor's run. The mode
  check's canonical plan (CANONICAL_SHAPE) is not the night's list: its 429 falls on another unit, and without MARGIN
  its limited nights finish a minute or two before the executor's (canary1: 219.8 against 220.5 minutes with one 429,
  240.2 against 242.1 with the crash on top).
- The unit a 429 hits is priced as lost, and nothing the executor does with it afterwards is replayed: the executor
  keeps what the unit fetched before the 429 (a series that came back before its related queries were refused counts
  as fetched). The estimated coverage is the lower one.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta

from ggwork_pick.observe.trends import breaker, budget, pacing

LIMIT_AT = 56  # stage 0, day 1: the first 429 answered the 56th request of the session
LATENCY_SECONDS = 2.0  # per answer; the fake Google of the tests takes 0.4
MARGIN = timedelta(minutes=10)  # one rest: a unit starting a little later in the night than here can rest earlier
MIN_LIMITED_COVERAGE = 0.95  # ONE_LIMIT, like the canary's freshness bar (design 4.11)
TRIGGER_INTERVAL = timedelta(minutes=30)  # deploy/pick-obs/trends/railway.toml fires at :00 and :30
LEASE = timedelta(seconds=300)  # lease.LEASE_SECONDS: a dead process's lease runs out within this
# The canary's unit shape: every RELATED_EVERY-th (4th) drama unit also asks for related queries (canary.with_related).
CANONICAL_SHAPE = (3, 2, 2, 2)
WARMUP_SIZE = 1


@dataclass(frozen=True)
class Scenario:
    name: str
    limit_at: int | None = None  # the request (1-based over the night, the warm-up first) answered with a 429
    crash_after: time | None = None  # the process dies with the first request sent at or after this UTC time


CLEAR = Scenario("clear")
ONE_LIMIT = Scenario("one_limit", limit_at=LIMIT_AT)
RESUMED = Scenario("resumed", limit_at=LIMIT_AT, crash_after=time(0, 0))
SCENARIOS = (CLEAR, ONE_LIMIT, RESUMED)


@dataclass(frozen=True)
class Estimate:
    """One replayed night: when each covered unit's last answer came back (MARGIN added), in the order they ran."""

    scenario: str
    units: int
    started: datetime
    completed: tuple[datetime, ...]
    last_done: datetime

    @property
    def covered(self) -> int:
        return len(self.completed)

    @property
    def coverage(self) -> float:
        return self.covered / self.units if self.units else 1.0

    @property
    def elapsed(self) -> timedelta:
        return self.last_done - self.started

    def summary(self) -> dict:
        minutes = round(self.elapsed.total_seconds() / 60)
        return {"scenario": self.scenario, "units": self.units, "covered": self.covered, "coverage": round(self.coverage, 4), "minutes": minutes}


class _Longest:
    """The drawn gaps at the long end of their range: the replay never waits less than a night could."""

    def uniform(self, low: float, high: float) -> float:
        return high


_LONGEST = _Longest()


@dataclass(frozen=True)
class _Rules:
    pacer: pacing.EnvelopePacer
    limits: budget.ModeLimits
    deadline: datetime
    scenario: Scenario
    crash_at: datetime | None


@dataclass(frozen=True)
class _Night:
    now: datetime
    pacing: pacing.PacingState
    breaker: breaker.BreakerState
    budget: budget.BudgetDay
    sent: int = 0
    crashed: bool = False
    last_done: datetime | None = None


SENT, LOST, STOPPED, CRASHED = "sent", "lost", "stopped", "crashed"


def canonical_sizes(requests: int) -> tuple[int, ...]:
    """Units of CANONICAL_SHAPE, as many as fit in `requests` (a plan is cut the same way, units.cut_to_plan)."""
    sizes, used = [], 0
    while used + CANONICAL_SHAPE[len(sizes) % len(CANONICAL_SHAPE)] <= requests:
        size = CANONICAL_SHAPE[len(sizes) % len(CANONICAL_SHAPE)]
        sizes, used = [*sizes, size], used + size
    return tuple(sizes)


def crash_moment(scenario: Scenario, target_date: date) -> datetime | None:
    """When the scenario's process dies on `target_date`'s night: at its first request sent from then on."""
    if scenario.crash_after is None:
        return None
    day = target_date if scenario.crash_after < budget.PUBLISH_CUTOFF else target_date - timedelta(days=1)
    return datetime.combine(day, scenario.crash_after, UTC)


def next_trigger(moment: datetime) -> datetime:
    """The first cron trigger at or after `moment` (every TRIGGER_INTERVAL from the hour)."""
    slot = moment.replace(minute=0, second=0, microsecond=0)
    while slot < moment:
        slot += TRIGGER_INTERVAL
    return slot


def estimate(sizes: Sequence[int], *, limits: budget.ModeLimits, target_date: date, params: pacing.PacingParams, scenario: Scenario) -> Estimate:
    """The night of `target_date` for units of `sizes` requests (in the order they run), under `limits` at `params`."""
    start, deadline = limits.window(target_date)
    rules = _Rules(pacing.EnvelopePacer(params), limits, deadline, scenario, crash_moment(scenario, target_date))
    night = _Night(start, pacing.initial_state(params), breaker.initial_state(target_date), budget.BudgetDay(target_date))
    night, _ = _unit(rules, night, WARMUP_SIZE)
    completed: tuple[datetime, ...] = ()
    for size in sizes:
        night, finished = _unit(rules, night, size)
        if finished == STOPPED:
            break
        if isinstance(finished, datetime) and finished + MARGIN <= deadline:
            completed = (*completed, finished + MARGIN)
    last_done = (night.last_done or start) + MARGIN
    return Estimate(scenario.name, len(sizes), start, completed, last_done)


def _unit(rules: _Rules, night: _Night, size: int) -> tuple[_Night, datetime | str]:
    """One unit: its finish, LOST (a limit signal abandoned it) or STOPPED (it and every later unit are uncovered).
    A crash sends the unit again from its first request once the night resumes."""
    while True:
        if budget.stop_reason(breaker_state=night.breaker, day=night.budget, limits=rules.limits, now=night.now, need=size) is not None:
            return night, STOPPED
        night, outcome = _requests(rules, night, size)
        if outcome != CRASHED:
            return night, (night.now if outcome == SENT else outcome)


def _requests(rules: _Rules, night: _Night, size: int) -> tuple[_Night, str]:
    for index in range(size):
        night, outcome = _send(rules, night, first=index == 0, size=size)
        if outcome != SENT:
            return night, outcome
    return night, SENT


def _send(rules: _Rules, night: _Night, *, first: bool, size: int) -> tuple[_Night, str]:
    """One request through the executor's gate: stop, wait for the pacer and the breaker, reserve, send, observe."""
    if budget.stop_reason(breaker_state=night.breaker, day=night.budget, limits=rules.limits, now=night.now) is not None:
        return night, STOPPED
    half = night.breaker.day.half_speed
    paced_at = rules.pacer.ready_at(night.pacing, now=night.now, first_in_unit=first, half_speed=half, unit_requests=size)
    sent_at = max(paced_at, breaker.ready_at(night.breaker, now=night.now))
    if sent_at >= rules.deadline:
        return replace(night, now=rules.deadline), STOPPED
    try:
        reserved = replace(night, budget=budget.reserve(night.budget, rules.limits), sent=night.sent + 1)
    except budget.BudgetExhausted:
        return night, STOPPED
    if rules.crash_at is not None and not night.crashed and sent_at >= rules.crash_at:
        return replace(reserved, now=next_trigger(sent_at + LEASE), crashed=True), CRASHED
    done = sent_at + timedelta(seconds=LATENCY_SECONDS)
    signal = breaker.Signal.RATE_LIMITED if reserved.sent == rules.scenario.limit_at else breaker.Signal.SUCCESS
    paced = rules.pacer.record(night.pacing, sent_at=sent_at, done_at=done, rng=_LONGEST, half_speed=half)
    broken, decision = breaker.observe(night.breaker, signal, now=done, rng=_LONGEST)
    after = replace(reserved, now=done, pacing=paced, breaker=broken, last_done=done)
    return after, (LOST if decision.abandon_unit else SENT)


# ---- the mode check ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    """A mode at a pace: the CLEAR, ONE_LIMIT and RESUMED estimates of its largest plan, and whether it is accepted."""

    mode: str
    clear: Estimate
    one_limit: Estimate
    resumed: Estimate

    @property
    def fits(self) -> bool:
        return self.clear.coverage >= 1.0 and self.one_limit.coverage >= MIN_LIMITED_COVERAGE

    def problem(self) -> str | None:
        if self.fits:
            return None
        return (
            f"{self.mode} 的计划在这个节奏下放不进窗口：无熔断覆盖 {self.clear.coverage:.0%}（须 100%），"
            f"第 {LIMIT_AT} 个请求一次 429 后覆盖 {self.one_limit.coverage:.0%}（须 ≥{MIN_LIMITED_COVERAGE:.0%}）；提前起跑或减量"
        )


def mode_fit(limits: budget.ModeLimits, params: pacing.PacingParams, *, target_date: date = date(2026, 9, 26)) -> Fit:
    """The largest plan the mode allows (its plan less the warm-up, probe and retry allowance), at `params`. Any target
    date gives the same answer: the window is the same length every night."""
    sizes = canonical_sizes(budget.plan_budget(limits))
    found = {scenario.name: estimate(sizes, limits=limits, target_date=target_date, params=params, scenario=scenario) for scenario in SCENARIOS}
    return Fit(limits.name, found[CLEAR.name], found[ONE_LIMIT.name], found[RESUMED.name])
