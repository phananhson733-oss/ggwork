"""`python -m ggwork_pick.observe.trends status`: a read-only look at the Trends channel (plan TR-14, section 5).

One read-only transaction through lease.status_reader, as ggwp-obs-admin: no lease is taken, nothing is written, a
running collector is not disturbed. It prints JSON lines: the runtime row (lease, pause, breaker level, disabled), the
last budget rows and the last batches. Never the cookie jar, the user agent or anything from the environment.
"""

import json
from collections.abc import Mapping
from typing import Any, TextIO

from sqlalchemy import select

from ggwork_pick.models import obs_batches, obs_budget, obs_runtime
from ggwork_pick.observe.lease import ReadStep, status_reader

CHANNEL = "trends"
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
BATCH_COLUMNS = (
    *("id", "target_date", "mode", "collect_mode", "window_end", "started_at", "finished_at", "outcome"),
    *("requests", "planned_units", "fetched_units", "coverage", "breaker_events", "status_codes_json"),
)


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: json.loads(value) if key.endswith("_json") and isinstance(value, str) else value for key, value in row.items()}


async def status_lines(step: ReadStep) -> list[dict[str, Any]]:
    runtime_query = select(*(obs_runtime.c[name] for name in RUNTIME_COLUMNS)).where(obs_runtime.c.channel == CHANNEL)
    runtime = (await step.execute(runtime_query)).mappings().first()
    budgets = await step.execute(
        select(*(obs_budget.c[name] for name in BUDGET_COLUMNS))
        .where(obs_budget.c.channel == CHANNEL)
        .order_by(obs_budget.c.budget_day.desc())
        .limit(SHOWN_DAYS)
    )
    batches = await step.execute(
        select(*(obs_batches.c[name] for name in BATCH_COLUMNS))
        .where(obs_batches.c.channel == CHANNEL)
        .order_by(obs_batches.c.target_date.desc())
        .limit(SHOWN_DAYS)
    )
    return [
        {"runtime": _plain(runtime) if runtime is not None else None},
        *({"budget": _plain(row)} for row in budgets.mappings().all()),
        *({"batch": _plain(row)} for row in batches.mappings().all()),
    ]


async def print_status(environ: Mapping[str, str], out: TextIO) -> int:
    async with status_reader(CHANNEL, environ=environ) as step:
        lines = await status_lines(step)
    for line in lines:
        print(json.dumps(line, ensure_ascii=False, default=str), file=out)
    return 0
