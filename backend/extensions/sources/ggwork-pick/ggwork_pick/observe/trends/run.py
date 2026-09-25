"""The nightly Trends session: the batch, its window_end, resuming, the hard deadline (plan TR-14; design 3.2, 4.5, 4.9,
4.10, 4.11, 6.3; D23, D34; counterexamples 1, 2, 10).

One trigger, in this order (plan TR-14 step 1-6):
1. the target date is D23's (the day whose 02:00 UTC publication the session feeds, so 20:30 to 01:45 is one day); a
   trigger before the mode's start, or at or after the 01:45 deadline, does nothing at all and exits 0;
2. the self-check, then the lease, then the state under the lease (lease.collector_session): a mismatch exits 2, a
   state that cannot be read or a missing runtime row 3, a lease another process holds 1, all before any HTTP;
3. a channel disabled by the breaker (disabled_7d), or a canary put out twice (canary_terminated), is refused (exit 2)
   after its code is written on the target date's batch row, so the data page's red banner holds while it lasts;
4. the batch of the target date: created by the first trigger, with window_end fixed then (the creation's whole hour
   less 3 hours, design 4.9) and the task list cut to the plan (truncated units kept in plan_json); a later trigger of
   the same date resumes it and never recomputes either (counterexample 1); a finished or published one exits 0;
5. the executor runs what is left of the task list (executor.py: every request paced, reserved and logged under the
   lease);
6. the summary and the codes; a canary session is withheld: it never publishes a set.

TR-20's seams, both unused here (a canary session never publishes, and a stable one has no task source yet, TR-18):
- `refetch` (executor.Refetch): after the task list, a hook that may run more units through the executor's gate, on
  the same client, for design 4.9 #6's consistency re-fetch of the first hits. The finishing step cannot send a request.
- `publish`: called in the finishing step with a Finishing (the batch, its plan and progress, the machines, the summary
  document, the codes the session came to, and the uncovered dramas in the contract's UncoveredUnit shape), returns a
  Published: the set's id or None, and the status codes it adds (not_published_low_coverage when design 4.10's 80%
  gate holds the set back). The batch is published when there is an id, withheld otherwise, with every code.
"""

import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import httpx

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.contract import STATUS_CODES
from ggwork_pick.observe.crypto import StateCipher
from ggwork_pick.observe.errors import ExitCode, Refused, StateUnavailable
from ggwork_pick.observe.instants import instant, stamp
from ggwork_pick.observe.lease import DbStateStore, LeasedStep, LeasedWriter, ReadStep, collector_session
from ggwork_pick.observe.trends import budget, pacing
from ggwork_pick.observe.trends import session_rows as rows
from ggwork_pick.observe.trends import session_summary as summary
from ggwork_pick.observe.trends.canary import SourceUnits
from ggwork_pick.observe.trends.contract_check import contract_check_due, contract_check_units, contract_check_verdict
from ggwork_pick.observe.trends.egress import EgressProbe
from ggwork_pick.observe.trends.executor import Executor, Machines, Refetch, UnitOutcome, restore_machines
from ggwork_pick.observe.trends.settings import Settings
from ggwork_pick.observe.trends.units import SessionPlan, cut_to_plan, ordered, plan_budget

logger = logging.getLogger(__name__)

TRENDS = "trends"
WINDOW_LAG = timedelta(hours=3)  # design 4.9: the latest hours Trends still revises are left out


def window_end_of(created_at: datetime) -> datetime:
    """The batch's window end: the whole hour it was created in, less three hours (design 4.9, 6.3)."""
    return instant(created_at).replace(minute=0, second=0, microsecond=0) - WINDOW_LAG


class TaskSource(Protocol):
    """Where a session's units come from: CanaryTaskSource now, TR-18's WatchTaskSource for the stable mode."""

    name: str

    async def units(self, step: ReadStep, *, target_date: date) -> SourceUnits: ...


@dataclass(frozen=True)
class Wiring:
    """What a session is wired to; tests pass a ManualClock, a MockTransport and, for the red test, a no-op pacer."""

    clock: Clock
    rng: random.Random
    transport: httpx.AsyncBaseTransport | None = None
    pacer: pacing.Pacer | None = None
    egress: EgressProbe | None = None


@dataclass(frozen=True)
class Day:
    settings: Settings
    target_date: date
    limits: budget.ModeLimits


def build_plan(day: Day, source: TaskSource, found: SourceUnits) -> SessionPlan:
    """The day's task list: the contract check first on its Monday, then the source's units, in truncation order, cut
    to the mode's plan."""
    checks = contract_check_units() if contract_check_due(day.target_date, enabled=day.settings.contract_check) else ()
    tasks = cut_to_plan(ordered((*checks, *found.units), day.target_date), plan_budget(day.limits))
    notes = {**found.notes, "contract_check": bool(checks), "plan": day.limits.plan, "cap": day.limits.cap}
    return SessionPlan(source.name, day.settings.granularity, day.settings.related, tasks, found.catalog_batch_id, notes)


async def carried_codes(step: ReadStep, target_date: date) -> frozenset[str]:
    before = await rows.previous_batch(step, target_date)
    return frozenset(before.status_codes) & summary.CARRIED_CODES if before is not None else frozenset()


def _new_batch(day: Day, **values: Any) -> rows.NewBatch:
    batch_id = f"trends-{day.target_date:%Y-%m-%d}-{uuid4().hex[:8]}"
    return rows.NewBatch(id=batch_id, mode=day.settings.batch_mode, collect_mode=day.limits.name, target_date=day.target_date, **values)


# ---- refusals that must stay visible (status_rules: a lasting code is written by every run row) ----------------------


async def refusal_codes(step: ReadStep, day: Day, machines: Machines) -> tuple[str, ...]:
    codes = {"disabled_7d"} if machines.breaker.disabled_on is not None else set()
    if day.settings.canary:
        days = await rows.extinguished_canary_days(step, since=day.settings.canary_since)
        codes |= {"canary_terminated"} if len(days) >= summary.CANARY_TERMINATE_AFTER else set()
    return summary.ordered_codes(codes)


async def mark_refused(step: LeasedStep, day: Day, codes: tuple[str, ...]) -> None:
    """The target date's row carries the codes: added to an existing row, or a refusal row (no plan, no window). A
    session that died and left its row (or an earlier night's) running is closed as failed: a refused day does not run
    again, so nothing would ever close it (the run_status view would show it running for good)."""
    closed = await rows.abandon_unfinished(step, day.target_date)
    if closed:
        logger.warning("[pick-obs] trends closed %d earlier batch(es) left running", closed)
    batch = await rows.find_batch(step, day.target_date)
    if batch is not None:
        merged = list(summary.ordered_codes({*batch.status_codes, *codes}))
        closing = {"outcome": "failed", "finished_at": stamp(step.now)} if batch.outcome == "running" else {}
        await rows.update_batch(step, batch.id, status_codes_json=merged, **closing)
        return
    carried = await carried_codes(step, day.target_date)
    values = {"window_end": None, "plan": None, "planned_units": None, "outcome": "failed", "finished_at": step.now}
    await rows.insert_batch(step, _new_batch(day, status_codes=summary.ordered_codes({*codes, *carried}), **values))


async def refuse_if_stopped(writer: LeasedWriter, day: Day, machines: Machines) -> None:
    async with writer.step() as step:
        codes = await refusal_codes(step, day, machines)
        if codes:
            await mark_refused(step, day, codes)
    if codes:
        raise Refused(f"trends 不跑：{', '.join(codes)}（disabled_7d 由 reset-disable 解除；canary_terminated 见手册）")


# ---- the batch --------------------------------------------------------------------------------------------------------


async def open_batch(writer: LeasedWriter, day: Day, source: TaskSource) -> rows.BatchRow | None:
    """The batch to run: the target date's, resumed as it is, or created now with its window and task list. None when
    the date is done (finished or published): nothing to do."""
    async with writer.step() as step:
        batch = await rows.find_batch(step, day.target_date)
        if batch is not None and batch.plan is not None:
            return None if batch.finished or batch.outcome == "published" else batch
        closed = await rows.abandon_unfinished(step, day.target_date)
        plan = build_plan(day, source, await source.units(step, target_date=day.target_date))
        codes = summary.ordered_codes(await carried_codes(step, day.target_date))  # a refusal's own codes are not carried
        values = {"window_end": window_end_of(step.now), "plan": plan.to_dict(), "planned_units": len(plan.tasks.planned), "status_codes": codes}
        if batch is None:
            await rows.insert_batch(step, _new_batch(day, **values))
        else:  # a refusal row of the same date, now that the channel runs again
            await _adopt(step, batch.id, values)
        if closed:
            logger.warning("[pick-obs] trends closed %d earlier batch(es) left running", closed)
        return await rows.find_batch(step, day.target_date)


async def _adopt(step: LeasedStep, batch_id: str, values: Mapping[str, Any]) -> None:
    await rows.update_batch(
        step,
        batch_id,
        window_end=stamp(values["window_end"]),
        plan_json=values["plan"],
        planned_units=values["planned_units"],
        fetched_units=0,
        outcome="running",
        finished_at=None,
        started_at=stamp(step.now),
        summary_json={"units": {}},
        status_codes_json=list(values["status_codes"]),
    )


def plan_of(batch: rows.BatchRow) -> SessionPlan:
    """The batch's task list as it was written when the batch was created; StateUnavailable (exit 3) when it cannot be
    read back, never a list made afresh in its place (counterexample 1: the list, like window_end, is the batch's)."""
    try:
        return SessionPlan.from_dict(batch.plan)
    except (ValueError, TypeError, KeyError):
        raise StateUnavailable(f"批次 {batch.id} 的 plan_json 读不回来：不另起任务清单，先查这一行（手册 trends-session.md）") from None


class Progress:
    """The units done so far, as summary_json keeps them; replaced, never changed in place."""

    def __init__(self, batch: rows.BatchRow, plan: SessionPlan):
        self._batch, self._plan = batch, plan
        self._units: Mapping[str, Mapping[str, Any]] = dict((batch.summary or {}).get("units", {}))

    @property
    def units(self) -> Mapping[str, Mapping[str, Any]]:
        return self._units

    def pending(self):
        return tuple(unit for unit in self._plan.tasks.planned if unit.key not in self._units)

    async def on_unit(self, step: LeasedStep, outcome: UnitOutcome) -> None:
        """The unit's raw rows and its progress entry, in the executor's step."""
        if outcome.result is not None:
            await rows.insert_raw(step, rows.raw_rows(self._batch.id, outcome.unit, outcome.result, outcome.sent, step.now))
        units = {**self._units, outcome.unit.key: summary.progress_entry(outcome)}
        done = sum(summary.fetched(entry) for entry in units.values())
        await rows.update_batch(step, self._batch.id, summary_json={"units": units}, fetched_units=done)
        self._units = units


# ---- the session ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ran:
    """What a trigger's executor left behind: the batch, its plan, the units' progress, the machines, the warm-up."""

    batch: rows.BatchRow
    plan: SessionPlan
    units: Mapping[str, Mapping[str, Any]]
    machines: Machines
    warmed: bool


@dataclass(frozen=True)
class Finishing:
    """What the finishing step hands TR-20's publisher."""

    ran: Ran
    document: Mapping[str, Any]
    codes: tuple[str, ...]

    @property
    def batch(self) -> rows.BatchRow:
        return self.ran.batch

    @property
    def uncovered_dramas(self) -> tuple[dict[str, str], ...]:
        """The uncovered units that name a drama, as the set summary's UncoveredUnit (identity, geo, reason): market
        series and the contract check name none."""
        listed = self.document["uncovered_units"]
        return tuple({"identity": unit["identity"], "geo": unit["geo"], "reason": unit["reason"]} for unit in listed if unit["identity"] is not None)


@dataclass(frozen=True)
class Published:
    """The publisher's answer: the set's id (None: held back) and the status codes it adds (contract STATUS_CODES)."""

    set_id: str | None
    codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not set(self.codes) <= set(STATUS_CODES):
            raise ValueError("a publisher adds only contract STATUS_CODES")


Publish = Callable[[LeasedStep, Finishing], Awaitable[Published]]


async def _judge(step: LeasedStep, day: Day, ran: Ran) -> summary.Judged:
    """What the session's codes rest on besides the breaker: the canary's days, the carried codes, the checks."""
    before = await rows.previous_batch(step, day.target_date)
    previous = (before.summary or {}) if before is not None else {}
    canary_days = await rows.extinguished_canary_days(step, since=day.settings.canary_since) if day.settings.canary else ()
    answered = {key: entry["status"] for key, entry in ran.units.items() if entry["reason"] is None}
    return summary.Judged(
        canary=day.settings.canary,
        extinguished_canary_days=len(canary_days),
        carried=frozenset(ran.batch.status_codes) & summary.CARRIED_CODES,
        contract_verdict=contract_check_verdict(ran.plan.tasks.planned, answered),
        zero_jump=summary.all_zero_jumped(summary.all_zero_rate(ran.plan.tasks.planned, ran.units), previous.get("all_zero_rate")),
        usertype_changed=summary.usertype_changed(summary.user_types(ran.units), previous.get("user_types")),
    )


def _document(ran: Ran, judged: summary.Judged) -> dict[str, Any]:
    """summary_json of a finished batch (the data page and TR-20 read it)."""
    planned, units, breaker = ran.plan.tasks.planned, ran.units, ran.machines.breaker
    fetched = sum(summary.fetched(entry) for entry in units.values())
    zero = summary.all_zero_rate(planned, units)
    return {
        "units": dict(units),
        "warmup": ran.warmed,
        "planned_units": len(planned),
        "fetched_units": fetched,
        "coverage": fetched / len(planned) if planned else None,
        "uncovered_units": summary.uncovered_units(planned, ran.plan.tasks.truncated, units),
        "failed_units": summary.failed_units(planned, units),
        "breaker_events": breaker.day.trips,
        "all_zero_rate": zero.rate,
        "judged_series": zero.judged,
        "user_types": list(summary.user_types(units)),
        "contract_check": judged.contract_verdict,
        "extinguished": breaker.day.extinguished,
        "requests_reserved": ran.machines.budget.reserved,
    }


async def _finish(writer: LeasedWriter, day: Day, ran: Ran, publish: Publish | None) -> None:
    """The finishing step: codes, summary, and (TR-20's seam) the set; a canary never publishes."""
    async with writer.step() as step:
        judged = await _judge(step, day, ran)
        codes = summary.session_codes(ran.machines.breaker, judged)
        document = _document(ran, judged)
        published = Published(None) if day.settings.canary or publish is None else await publish(step, Finishing(ran, document, codes))
        codes = summary.ordered_codes({*codes, *published.codes})
        outcome = "published" if published.set_id is not None else "withheld"
        await rows.update_batch(
            step,
            ran.batch.id,
            finished_at=stamp(step.now),
            outcome=outcome,
            fetched_units=document["fetched_units"],
            coverage=document["coverage"],
            breaker_events=document["breaker_events"],
            summary_json=document,
            status_codes_json=list(codes),
            published_set_id=published.set_id,
        )
    logger.info(
        "[pick-obs] trends %s finished: %s, %d/%d units, codes %s",
        day.target_date,
        outcome,
        document["fetched_units"],
        document["planned_units"],
        list(codes) or "-",
    )


async def run_session(
    day: Day,
    source: TaskSource,
    *,
    writer: LeasedWriter,
    cipher: StateCipher,
    wiring: Wiring,
    publish: Publish | None = None,
    refetch: Refetch | None = None,
) -> ExitCode:
    """Steps 3-6 under a lease already held: refuse, open the batch, run what is left, finish."""
    loaded = await DbStateStore(writer, cipher).load()
    machines = restore_machines(loaded, target_date=day.target_date, now=wiring.clock.now())
    day = replace(day, limits=budget.day_limits(day.settings.limits, day.target_date, machines.breaker))
    await refuse_if_stopped(writer, day, machines)
    batch = await open_batch(writer, day, source)
    if batch is None:
        logger.info("[pick-obs] trends %s is done already: nothing to do", day.target_date)
        return ExitCode.OK
    plan = plan_of(batch)
    progress = Progress(batch, plan)
    store = DbStateStore(writer, cipher, limits=day.limits)
    executor = Executor(
        writer=writer,
        store=store,
        batch_id=batch.id,
        target_date=day.target_date,
        limits=day.limits,
        machines=machines,
        clock=wiring.clock,
        rng=wiring.rng,
        pacer=wiring.pacer or pacing.EnvelopePacer(),
        egress=wiring.egress,
        transport=wiring.transport,
        on_unit=progress.on_unit,
    )
    warmed = await executor.run(progress.pending(), then=refetch)
    await _finish(writer, day, Ran(batch, plan, progress.units, executor.machines, warmed), publish)
    return ExitCode.OK


def day_of(settings: Settings, now: datetime) -> tuple[Day, str | None]:
    """The day a trigger at `now` belongs to, and why it has nothing to do (before the start, at or after the
    deadline), or None."""
    target = budget.target_date_of(now)
    day = Day(settings, target, settings.limits)
    start, deadline = settings.limits.window(target)
    if now < start:
        return day, f"还没到 {settings.mode} 的起跑时刻 {start:%H:%M} UTC"
    if now >= deadline:
        return day, f"已过 {deadline:%H:%M} UTC 硬截止"
    return day, None


async def run_trends(
    settings: Settings,
    source: TaskSource,
    *,
    cipher: StateCipher,
    environ: Mapping[str, str],
    wiring: Wiring,
    publish: Publish | None = None,
    refetch: Refetch | None = None,
) -> ExitCode:
    """One trigger of the cron: nothing before the start or after the deadline; otherwise the session under the lease."""
    day, idle = day_of(settings, wiring.clock.now())
    if idle is not None:
        logger.info("[pick-obs] trends %s: %s，什么都不做", day.target_date, idle)
        return ExitCode.OK
    async with collector_session(TRENDS, clock=wiring.clock, environ=environ) as session:
        return await run_session(day, source, writer=session.writer, cipher=cipher, wiring=wiring, publish=publish, refetch=refetch)
