"""The Trends executor: every HTTP request through the pacer, the breaker and the budget, under the lease (plan TR-14,
critique C-17; design 3.3, 4.2, 4.3, 4.4; counterexamples 2 and 10).

The pacing envelope is only as good as its wiring, so it is wired here, at the one place every request passes: the
client's gate, awaited before each HTTP request (warm-up, explore, multiline, relatedsearches; probes and retries are
such requests too). For each one, in this order:

1. wait for the pacer and the breaker (clock.sleep_in_chunks, renewing the lease on every wake), and stop instead when
   the breaker put the day out, the deadline has come, or the day's cap cannot hold the request;
2. renew the lease if 60 seconds have passed (ensure_fresh);
3. reserve the request in the budget and write the state, in one leased step: committed before the request leaves, and
   never given back (design 3.3). A lost lease raises here, and the request is never sent;
4. the client sends it; on_request then records the pacing, turns the outcome into the breaker's signal, and writes the
   request row with the state in one leased step. A lease taken over while the request was out raises there: the old
   owner commits neither the row, nor the budget's later counts, nor the unit's raw rows (counterexample 10).

A unit runs whole or stops: it starts only when the day can hold all its requests; a first 5xx or timeout reruns it
once, 30-60 seconds later (TR-03's RETRY); a pause abandons the rest of it; once one unit cannot start, every later unit
is left with the same reason (truncated, skipped_breaker or deadline). When a unit ends, on_unit writes its raw rows
and progress with the state in one leased step. The pacer is injectable: the wiring test swaps in a no-op and expects
the transport-level envelope to fail (test_transport_level_noop_pacer_red).
"""

import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

import httpx

from ggwork_pick.observe.clock import Clock, sleep_in_chunks
from ggwork_pick.observe.lease import DbStateStore, LeasedStep, LeasedWriter
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends import breaker, budget, pacing
from ggwork_pick.observe.trends.client import TrendsClient
from ggwork_pick.observe.trends.cookies import CookieJar
from ggwork_pick.observe.trends.egress import EgressProbe
from ggwork_pick.observe.trends.parse import WALLS
from ggwork_pick.observe.trends.session_rows import RequestContext, SentRequest, count_request, insert_request
from ggwork_pick.observe.trends.source import DEFAULT_USER_AGENT, FetchResult, Phase, RequestRecord, RequestStep
from ggwork_pick.observe.trends.units import QueryUnit

logger = logging.getLogger(__name__)

WARMUP_ITEM = "warmup"
LIMIT_ACTIONS = (breaker.Action.PAUSE, breaker.Action.EXTINGUISH)


@dataclass(frozen=True)
class Machines:
    """The TR-03 machines and the jar, as one immutable value the executor replaces after each step."""

    pacing: pacing.PacingState
    breaker: breaker.BreakerState
    budget: budget.BudgetDay
    jar: CookieJar


def restore_machines(state: RuntimeState, *, target_date: date, now: datetime) -> Machines:
    """The persisted machines rolled over to `target_date`: a new date starts its counters (keeping a pause still
    running and the history), the same one goes on, midnight included (D23)."""
    paced = state.section("pacing", pacing.PacingState.from_dict) or pacing.initial_state()
    broken = state.section("breaker", breaker.BreakerState.from_dict) or breaker.initial_state(target_date)
    spent = state.section("budget", budget.BudgetDay.from_dict) or budget.BudgetDay(target_date)
    jar = state.cookie_jar or CookieJar.fresh(DEFAULT_USER_AGENT)
    return Machines(paced, breaker.for_target_date(broken, target_date, now=now), budget.for_target_date(spent, target_date), jar)


def state_of(machines: Machines) -> RuntimeState:
    return RuntimeState(
        paused_until=machines.breaker.day.paused_until,
        pacing=machines.pacing.to_dict(),
        breaker=machines.breaker.to_dict(),
        budget=machines.budget.to_dict(),
        cookie_jar=machines.jar,
    )


class Stop(Exception):
    """A request, or a unit, cannot go out: truncated, skipped_breaker or deadline. Nothing was sent for it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class UnitOutcome:
    """How a unit ended: a result (a failed one included), or the reason it did not run to the end."""

    unit: QueryUnit
    result: FetchResult | None
    reason: str | None
    attempts: int
    sent: tuple[SentRequest, ...]


OnUnit = Callable[[LeasedStep, UnitOutcome], Awaitable[None]]


@dataclass(frozen=True)
class _Current:
    """What the next request belongs to: its unit (None for the warm-up), its attempt and its size."""

    unit: QueryUnit | None
    attempt: int
    http: int


class Executor:
    """One night's requests on one client. Holds only what the gate and on_request share; the machines are replaced
    only after the step that persists them has committed."""

    def __init__(
        self,
        *,
        writer: LeasedWriter,
        store: DbStateStore,
        batch_id: str,
        target_date: date,
        limits: budget.ModeLimits,
        machines: Machines,
        clock: Clock,
        rng: random.Random,
        pacer: pacing.Pacer,
        egress: EgressProbe | None,
        transport: httpx.AsyncBaseTransport | None,
        on_unit: OnUnit,
    ):
        self._writer, self._store, self._batch_id, self._target = writer, store, batch_id, target_date
        self._limits, self._machines, self._clock, self._rng, self._pacer = limits, machines, clock, rng, pacer
        self._egress, self._transport, self._on_unit = egress, transport, on_unit
        self._deadline = limits.window(target_date)[1]
        self._client: TrendsClient | None = None
        self._current = _Current(None, 1, 1)
        self._item, self._half = WARMUP_ITEM, False
        self._decision: breaker.Decision | None = None
        self._sent: tuple[SentRequest, ...] = ()

    @property
    def machines(self) -> Machines:
        return self._machines

    async def run(self, units: Sequence[QueryUnit]) -> str | None:
        """The warm-up, then `units` in order, on one client (one connection pool, design 4.1). Returns the warm-up's
        status (None when the jar was already warmed for the target date)."""
        async with TrendsClient(
            jar=self._machines.jar, clock=self._clock, gate=self._gate, on_request=self._on_request, transport=self._transport, egress=self._egress
        ) as client:
            self._client = client
            try:
                warmed = await self._warm(client) if units else None
                await self._run_units(client, units)
            finally:
                self._machines, self._client = replace(self._machines, jar=client.jar), None
        return warmed

    # ---- the warm-up and the units ---------------------------------------------------------------------------------

    async def _warm(self, client: TrendsClient) -> str | None:
        """At most one warm-up per target date; a 5xx or timeout gets its one retry (design 4.2)."""
        status = None
        for attempt in (1, 2):
            self._begin(None, attempt, 1)
            try:
                warmed = await client.warm(day=self._target)
            except Stop as stop:
                return f"skipped:{stop.reason}"
            status = warmed.status.value if warmed.status is not None else None
            if not (attempt == 1 and self._retry_due()):
                break
        await self._save()  # the jar the warm-up filled
        return status

    async def _run_units(self, client: TrendsClient, units: Sequence[QueryUnit]) -> None:
        for index, unit in enumerate(units):
            outcome = await self._run_unit(client, unit)
            await self._settle(outcome)
            if outcome.reason is not None:
                await self._settle_rest(units[index + 1 :], outcome.reason)
                return

    async def _run_unit(self, client: TrendsClient, unit: QueryUnit) -> UnitOutcome:
        self._sent = ()
        for attempt in (1, 2):
            reason = self._stop_reason(need=unit.http)
            if reason is not None:
                return UnitOutcome(unit, None, reason, attempt - 1, self._sent)
            self._begin(unit, attempt, unit.http)
            try:
                result = await client.fetch(unit.query(), timeline=unit.timeline, related=unit.related, label=unit.key)
            except Stop as stop:
                return UnitOutcome(unit, None, stop.reason, attempt, self._sent)
            if not (attempt == 1 and self._retry_due()):
                return UnitOutcome(unit, result, None, attempt, self._sent)
        raise AssertionError("unreachable: the second attempt always returns")

    def _begin(self, unit: QueryUnit | None, attempt: int, http: int) -> None:
        self._current, self._decision = _Current(unit, attempt, http), None

    def _retry_due(self) -> bool:
        return self._decision is not None and self._decision.action is breaker.Action.RETRY

    async def _settle(self, outcome: UnitOutcome) -> None:
        async with self._writer.step() as step:
            await self._store.write(step, self._state())
            await self._on_unit(step, outcome)

    async def _settle_rest(self, units: Sequence[QueryUnit], reason: str) -> None:
        """Every unit after the one that could not run, left with its reason, in one step."""
        if not units:
            return
        async with self._writer.step() as step:
            for unit in units:
                await self._on_unit(step, UnitOutcome(unit, None, reason, 0, ()))

    # ---- the gate: before every request ----------------------------------------------------------------------------

    def _stop_reason(self, *, need: int) -> str | None:
        machines = self._machines
        return budget.stop_reason(breaker_state=machines.breaker, day=machines.budget, limits=self._limits, now=self._clock.now(), need=need)

    async def _wait_until_ready(self, first: bool) -> None:
        """Sleep until the pacer and the breaker both let the request go, never past the deadline; Stop when it cannot."""
        while True:
            reason = self._stop_reason(need=1)
            if reason is not None:
                raise Stop(reason)
            machines, now = self._machines, self._clock.now()
            paced = self._pacer.ready_at(
                machines.pacing, now=now, first_in_unit=first, half_speed=machines.breaker.day.half_speed, unit_requests=self._current.http
            )
            ready = max(paced, breaker.ready_at(machines.breaker, now=now))
            if ready <= now:
                return
            await sleep_in_chunks(self._clock, (min(ready, self._deadline) - now).total_seconds(), on_wake=self._writer.ensure_fresh)

    async def _gate(self, step: RequestStep) -> None:
        first = step.phase in (Phase.WARMUP, Phase.EXPLORE)
        await self._wait_until_ready(first)
        await self._writer.ensure_fresh()
        try:
            reserved = budget.reserve(self._machines.budget, self._limits)
        except budget.BudgetExhausted:
            raise Stop(budget.TRUNCATED) from None
        machines = replace(self._machines, budget=reserved)
        async with self._writer.step() as leased:
            await self._store.write(leased, self._state(machines))
        self._machines = machines
        self._half = machines.breaker.day.half_speed
        self._item = self._budget_item(step.phase, probe=machines.breaker.day.probe_due)

    def _budget_item(self, phase: Phase, *, probe: bool) -> str:
        """design 4.5's rows: warm-up, probe and retry first, then the related queries, then the unit's own item."""
        unit = self._current.unit
        if phase is Phase.WARMUP or unit is None:
            return WARMUP_ITEM
        if probe:
            return "probe"
        if self._current.attempt > 1:
            return "retry"
        return "related" if phase is Phase.RELATED else unit.item

    # ---- on_request: after every request ---------------------------------------------------------------------------

    async def _on_request(self, record: RequestRecord) -> None:
        now, machines = self._clock.now(), self._machines
        paced = self._pacer.record(machines.pacing, sent_at=record.started_at, done_at=now, rng=self._rng, half_speed=self._half)
        signal = breaker.signal_of(record.fetch_status.value, captcha_or_consent=record.redirect_kind in WALLS)
        broken, decision = breaker.observe(machines.breaker, signal, now=now, rng=self._rng)
        spent = machines.budget
        if decision.action in LIMIT_ACTIONS:
            spent = budget.note_limit(spent, ordinal=spent.reserved, at=record.started_at)
        updated = replace(machines, pacing=paced, breaker=broken, budget=spent)
        unit = self._current.unit
        context = RequestContext(self._batch_id, self._target, self._item, unit.identity if unit else None, unit.geo if unit else None)
        async with self._writer.step() as step:
            request_id = await insert_request(step, context, record)
            await count_request(step, self._batch_id)
            await self._store.write(step, self._state(updated))
        self._machines, self._decision = updated, decision
        self._sent = (*self._sent, SentRequest(record, request_id))
        if decision.action in LIMIT_ACTIONS and self._egress is not None:
            self._egress.invalidate()  # design 4.4, D20: measure the egress again after a trip
        if decision.action is not breaker.Action.CONTINUE:
            logger.info("[pick-obs] trends %s after %s: %s", decision.action.value, record.phase.value, record.fetch_status.value)

    # ---- state -----------------------------------------------------------------------------------------------------

    def _state(self, machines: Machines | None = None) -> RuntimeState:
        chosen = machines or self._machines
        jar = self._client.jar if self._client is not None else chosen.jar
        return state_of(replace(chosen, jar=jar))

    async def _save(self) -> None:
        async with self._writer.step() as step:
            await self._store.write(step, self._state())
