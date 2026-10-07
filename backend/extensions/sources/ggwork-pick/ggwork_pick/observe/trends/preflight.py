"""`python -m ggwork_pick.observe.trends preflight`: tonight's task list in figures, without a request (plan section 9,
S6a between S6 and S7; G3 review seam 1). On Railway it runs as the second step of the self-check deploy, after
--selfcheck-only passed (deploy/pick-obs/trends/selfcheck/railway.toml; packaging.md section 3.2).

One read-only transaction as ggwp-obs-admin (lease.status_reader, like `status`): no lease, nothing written, no HTTP.
It reads what the night's first trigger will read (the current shared catalog batch through the canary's task
source, the breaker on the runtime row, the canary's extinguished days on the budget rows), builds the task list the
same way (run.build_plan) and prints one JSON line:

- the target date the next session feeds, the mode, the pace (preset, bucket and refill, as a batch keeps it in
  plan_json's notes), its start and deadline, and the window_end a batch created at the start would get;
- the payload gate's overview (admission.py): planned requests against the plan and the threshold, the units and the
  truncated ones, the recent dramas, the controls matched per group, the missing ones (count and the first few
  identities), and the reasons the night would be refused;
- the codes that would refuse the night before its task list (disabled_7d, canary_terminated);
- the capacity estimate of this very task list at the pace, for the three nights capacity.py prices.

Exit 0 when the night would run as a valid canary night, 2 when it would be refused (either way the line is printed).
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from ggwork_pick.models import obs_runtime
from ggwork_pick.observe.errors import ExitCode, StateUnavailable
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import ReadStep, status_reader, stored_breaker
from ggwork_pick.observe.trends import admission as gate
from ggwork_pick.observe.trends import breaker, budget, capacity, recovery
from ggwork_pick.observe.trends.run import TRENDS, Day, TaskSource, build_plan, payload_overview, refusal_codes, window_end_of
from ggwork_pick.observe.trends.settings import Settings
from ggwork_pick.observe.trends.units import SessionPlan


async def _breaker(step: ReadStep, target_date: date, now: datetime) -> breaker.BreakerState:
    row = (await step.execute(select(obs_runtime.c.state_json).where(obs_runtime.c.channel == TRENDS))).mappings().first()
    if row is None:
        raise StateUnavailable("ggwp_obs_runtime 没有 trends 这一行（D34）")
    try:
        stored = stored_breaker(row)
    except (ValueError, TypeError):
        raise StateUnavailable("运行时行里的熔断状态读不回来") from None
    return breaker.for_target_date(stored or breaker.initial_state(target_date), target_date, now=now)


def _estimates(plan: SessionPlan, day: Day) -> list[dict[str, Any]]:
    sizes = [unit.http for unit in plan.tasks.planned]
    params = day.settings.pace_params
    limits = recovery.limits_for(day.limits, plan.notes) if day.settings.recovery_since is not None else day.limits
    found = (capacity.estimate(sizes, limits=limits, target_date=day.target_date, params=params, scenario=scenario) for scenario in capacity.SCENARIOS)
    return [estimate.summary() for estimate in found]


async def tonight(settings: Settings, source: TaskSource, *, now: datetime, environ: Mapping[str, str], admission: gate.Admission) -> dict[str, Any]:
    """The overview of the session a trigger at or after `now` would run."""
    target = budget.target_date_of(now)
    async with status_reader(TRENDS, environ=environ) as step:
        broken = await _breaker(step, target, now)
        day = Day(settings, target, budget.day_limits(settings.limits, target, broken))
        refusals = await refusal_codes(step, day, broken)
        plan = build_plan(day, source, await source.units(step, target_date=target))
    start, deadline = day.limits.window(target)
    figures = payload_overview(day, plan, admission)
    reasons = figures["reasons"] if settings.canary or settings.recovery_since is not None else []
    return {
        "target_date": f"{target:%Y-%m-%d}",
        "mode": settings.mode,
        "pace": settings.pace_note,
        "start": stamp(start),
        "deadline": stamp(deadline),
        "window_end_if_started_on_time": stamp(window_end_of(start, settings.granularity)),
        **figures,
        "reasons": reasons,
        "refused_by": list(refusals),
        "estimates": _estimates(plan, day),
    }


def exit_code(line: Mapping[str, Any]) -> ExitCode:
    return ExitCode.REFUSED if line["reasons"] or line["refused_by"] else ExitCode.OK
