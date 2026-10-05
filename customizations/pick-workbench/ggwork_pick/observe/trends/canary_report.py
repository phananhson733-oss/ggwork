"""Neutral stored canary facts (TR-30), not a gate or a collector.

Seven UTC calendar dates end at the injected current date. Missing dates remain unknown;
reservations, batch counters and recorded HTTP attempts are distinct. Accepted stored
admission plus an actual request to that batch establishes load_and_request_valid only:
late admission, incomplete sessions and failures remain visible, and no route is qualified.
"""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, TextIO

from sqlalchemy import case, func, select

from ggwork_pick.models import obs_batches, obs_budget, obs_requests, obs_runtime
from ggwork_pick.observe.contract import STATUS_CODES
from ggwork_pick.observe.cron_status import BUDGET_COLUMNS
from ggwork_pick.observe.lease import ReadStep, status_reader

BATCH_FIELDS = (
    "target_date",
    "collect_mode",
    "started_at",
    "finished_at",
    "outcome",
    "requests",
    "planned_units",
    "fetched_units",
    "coverage",
    "breaker_events",
)
RUNTIME_FIELDS = ("lease_until", "breaker_level", "paused_until", "disabled_at", "updated_at")
ADMISSION_COUNTS = (
    "plan",
    "min_requests",
    "planned_requests",
    "planned_units",
    "truncated_units",
    "catalog_dramas",
    "recent_dramas",
    "min_positive",
    "missing_controls",
)


def document(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def admission(row: Mapping) -> dict:
    notes = document(document(row["plan_json"]).get("notes"))
    planned = document(notes.get("admission"))
    refused = document(document(row["summary_json"]).get("admission"))
    figures = planned or refused
    reasons = figures.get("reasons")
    # Only the accepted plan establishes acceptance, never an empty refusal summary.
    accepted = bool(planned) and reasons == [] if isinstance(reasons, list) else None
    return {
        "accepted": accepted,
        "reason_count": len(reasons) if isinstance(reasons, list) else None,
        "figures": {key: value for key in ADMISSION_COUNTS if type(value := figures.get(key)) is int},
        "late_admission": notes.get("late_admission") if type(notes.get("late_admission")) is bool else None,
    }


def codes(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [code for code in STATUS_CODES if isinstance(value, list) and code in value]


async def read_report(step: ReadStep, now: datetime) -> dict:
    now = now.astimezone(UTC)
    anchor = now.date()
    start = anchor - timedelta(days=6)
    lower, upper = start.isoformat(), anchor.isoformat()
    runtime = (await step.execute(select(*(obs_runtime.c[name] for name in RUNTIME_FIELDS)).where(obs_runtime.c.channel == "trends"))).mappings().first()
    latest = []
    for table, column in ((obs_batches, obs_batches.c.target_date), (obs_budget, obs_budget.c.budget_day), (obs_requests, obs_requests.c.budget_day)):
        value = (await step.execute(select(func.max(column)).where(table.c.channel == "trends"))).scalar_one_or_none()
        if value:
            latest.append(value)
    budgets = (
        (
            await step.execute(
                select(*(obs_budget.c[name] for name in BUDGET_COLUMNS)).where(obs_budget.c.channel == "trends", obs_budget.c.budget_day.between(lower, upper))
            )
        )
        .mappings()
        .all()
    )
    batches = (
        (
            await step.execute(
                select(
                    obs_batches.c.id,
                    *(obs_batches.c[name] for name in BATCH_FIELDS),
                    obs_batches.c.plan_json,
                    obs_batches.c.summary_json,
                    obs_batches.c.status_codes_json,
                )
                .where(obs_batches.c.channel == "trends", obs_batches.c.target_date.between(lower, upper))
                .order_by(obs_batches.c.started_at, obs_batches.c.id)
            )
        )
        .mappings()
        .all()
    )
    requests = (
        (
            await step.execute(
                select(
                    obs_requests.c.budget_day,
                    obs_requests.c.batch_id,
                    func.count().label("count"),
                    func.sum(case((obs_requests.c.status_code == 429, 1), else_=0)).label("http_429"),
                    func.max(obs_requests.c.sent_at).label("last_sent_at"),
                )
                .where(obs_requests.c.channel == "trends", obs_requests.c.budget_day.between(lower, upper))
                .group_by(obs_requests.c.budget_day, obs_requests.c.batch_id)
            )
        )
        .mappings()
        .all()
    )
    days = []
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        budget = next((dict(row) for row in budgets if row["budget_day"] == day), None)
        attempts = [row for row in requests if row["budget_day"] == day]
        found = []
        validity = []
        for row in batches:
            if row["target_date"] != day:
                continue
            stored = admission(row)
            count = sum(item["count"] for item in attempts if item["batch_id"] == row["id"])
            valid = None if stored["accepted"] is None else stored["accepted"] and count > 0
            if stored["accepted"] is True and count == 0 and row["outcome"] == "running":
                valid = None
            validity.append(valid)
            found.append(
                {
                    **{name: row[name] for name in BATCH_FIELDS},
                    "stored_admission": stored,
                    "late_admission": stored["late_admission"],
                    "recorded_requests": count,
                    "load_and_request_valid": valid,
                    "status_codes": codes(row["status_codes_json"]),
                }
            )
        present = bool(budget or found or attempts)
        days.append(
            {
                "date": day,
                "evidence": "recorded" if present else "pending" if day == upper else "missing",
                "budget": budget,
                "batches": found,
                "recorded_requests": sum(item["count"] for item in attempts) if present else None,
                "recorded_http_429": sum(item["http_429"] for item in attempts) if present else None,
                "last_request_at": max((item["last_sent_at"] for item in attempts), default=None),
                "load_and_request_valid": True if True in validity else None if not validity or None in validity else False,
            }
        )
    latest_date = max(latest, default=None)
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="microseconds"),
        "anchor_date": upper,
        "latest_recorded_date": latest_date,
        "latest_recorded_age_days": (anchor - date.fromisoformat(latest_date)).days if latest_date else None,
        "window": {"start": lower, "end": upper},
        "qualification": "not_evaluated",
        "runtime": dict(runtime) if runtime else None,
        "days": days,
    }


async def print_report(environ: Mapping[str, str], out: TextIO, *, now: datetime) -> int:
    async with status_reader("trends", environ=environ) as step:
        report = await read_report(step, now)
    print(json.dumps(report, ensure_ascii=False, default=str), file=out)
    return 0
