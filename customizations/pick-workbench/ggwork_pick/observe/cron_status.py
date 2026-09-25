"""`python -m ggwork_pick.observe.<channel> status`: a read-only look at a channel (plan TR-14, TR-21, section 5).

One read-only transaction through lease.status_reader, as ggwp-obs-admin: no lease is taken, nothing is written, a
running collector is not disturbed. It prints JSON lines: the runtime row (lease, pause, breaker level, disabled), the
last budget rows and the last batches, and for each Trends batch that has one, its task list in figures (the payload
gate's overview, trends/admission.py: planned requests against the plan, the controls matched per group, how many are
missing and the first few identities, and why a refused night was refused). Never the cookie jar, the user agent or
anything from the environment.

While it runs it holds one connection of its own, beside the collector's (design 3.4 counts two for the collectors;
this one is the operator's, for the few seconds the read takes, and gone when it returns: test_status_connections).
"""

import json
from collections.abc import Mapping
from typing import Any, TextIO

from sqlalchemy import select

from ggwork_pick.models import obs_batches, obs_budget, obs_runtime
from ggwork_pick.observe.lease import ReadStep, status_reader

SHOWN_DAYS = 7
RUNTIME_COLUMNS = ("lease_owner", "lease_until", "lease_generation", "breaker_level", "paused_until", "disabled_at", "reset_by", "reset_at", "updated_at")
BUDGET_COLUMNS = (
    "budget_day",
    "collect_mode",
    "cap",
    "requests",
    "requests_before_first_limit",
    "first_limited_at",
    "breaker_trips",
    "http_429",
    "extinguish_reason",
    "extinguished_at",
)
PLAN_NOTES = ("pace", "late_admission")  # what else TR-30 reads off a Trends batch's notes (trends/run.py)
BATCH_COLUMNS = (
    *("id", "target_date", "round_id", "mode", "collect_mode", "window_end", "started_at", "finished_at", "outcome"),
    *("requests", "planned_units", "fetched_units", "coverage", "breaker_events", "status_codes_json"),
)


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: json.loads(value) if key.endswith("_json") and isinstance(value, str) else value for key, value in row.items()}


def _figures(document: object, *path: str) -> Mapping[str, Any] | None:
    for key in path:
        document = json.loads(document) if isinstance(document, str) else document
        document = document.get(key) if isinstance(document, Mapping) else None
    return document if isinstance(document, Mapping) else None


def plan_line(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A batch's task list in figures: kept in plan_json's notes when the batch was created, with the pace it ran at
    and whether it was admitted late (trends/run.py), or in summary_json when the payload gate refused the night
    (trends/admission.py). None for a batch without either (GSC, older rows)."""
    notes = _figures(row["plan_json"], "notes") or {}
    figures = _figures(notes, "admission") or _figures(row["summary_json"], "admission")
    kept = {key: notes[key] for key in PLAN_NOTES if key in notes}
    return None if figures is None else {"plan": {"batch": row["id"], "target_date": row["target_date"], **figures, **kept}}


async def status_lines(step: ReadStep, channel: str) -> list[dict[str, Any]]:
    runtime_query = select(*(obs_runtime.c[name] for name in RUNTIME_COLUMNS)).where(obs_runtime.c.channel == channel)
    runtime = (await step.execute(runtime_query)).mappings().first()
    budgets = await step.execute(
        select(*(obs_budget.c[name] for name in BUDGET_COLUMNS))
        .where(obs_budget.c.channel == channel)
        .order_by(obs_budget.c.budget_day.desc())
        .limit(SHOWN_DAYS)
    )
    batches = await step.execute(
        select(*(obs_batches.c[name] for name in BATCH_COLUMNS), obs_batches.c.plan_json, obs_batches.c.summary_json)
        .where(obs_batches.c.channel == channel)
        .order_by(obs_batches.c.target_date.desc(), obs_batches.c.started_at.desc())
        .limit(SHOWN_DAYS)
    )
    found = batches.mappings().all()
    plans = (plan_line(row) for row in found)
    return [
        {"runtime": _plain(runtime) if runtime is not None else None},
        *({"budget": _plain(row)} for row in budgets.mappings().all()),
        *({"batch": _plain({name: row[name] for name in BATCH_COLUMNS})} for row in found),
        *(line for line in plans if line is not None),
    ]


async def print_status(channel: str, environ: Mapping[str, str], out: TextIO) -> int:
    async with status_reader(channel, environ=environ) as step:
        lines = await status_lines(step, channel)
    for line in lines:
        print(json.dumps(line, ensure_ascii=False, default=str), file=out)
    return 0
