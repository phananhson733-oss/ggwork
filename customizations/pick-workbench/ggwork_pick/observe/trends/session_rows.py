"""The Trends session's rows: the batch, its request log and raw series (plan TR-14; design 3.2, 3.5, 4.10).

Every function takes the step it runs in, first: a LeasedStep to write (the lease was checked under the runtime row's
lock, and everything written commits with the step or not at all), a ReadStep to read. Nothing here opens a
transaction of its own (test_write_paths).

- ggwp_obs_batches: one row per target date (UNIQUE), created by the first trigger with window_end fixed then (design
  3.2); plan_json holds the task list, summary_json the progress and, at the end, the summary; status_codes_json the
  codes the data page's banners read (status_rules).
- ggwp_obs_requests: one row per HTTP request, warm-up, probe and retry included, with its budget item (design 3.5).
- ggwp_obs_raw: one row per series line as the answer had it, or per requested line with data_json NULL when the unit
  failed: a failure never becomes zeros (design 4.10, counterexample 1). Kept 35 days, for audit only.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import insert, select, update

from ggwork_pick.models import obs_batches, obs_budget, obs_raw, obs_requests
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import LeasedStep, ReadStep
from ggwork_pick.observe.trends import state_codec as codec
from ggwork_pick.observe.trends.source import FetchResult, Phase, RequestRecord
from ggwork_pick.observe.trends.units import QueryUnit
from ggwork_pick.observe.versions import COLLECTOR_VERSION

CHANNEL = "trends"
MAX_ERROR = 200  # the error column
EXTINGUISHED_CANARY_MODES = ("canary1", "canary2")


@dataclass(frozen=True)
class BatchRow:
    id: str
    target_date: date
    window_end: datetime | None  # None only on a refusal row: a run that never started has no window
    outcome: str
    finished: bool
    plan: Mapping[str, Any] | None
    summary: Mapping[str, Any] | None
    status_codes: tuple[str, ...]
    requests: int


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _batch(row: Mapping[str, Any]) -> BatchRow:
    return BatchRow(
        id=row["id"],
        target_date=codec.decode_day(row["target_date"], "target_date"),
        window_end=codec.decode_instant(row["window_end"], "window_end"),
        outcome=row["outcome"],
        finished=row["finished_at"] is not None,
        plan=_json(row["plan_json"]),
        summary=_json(row["summary_json"]),
        status_codes=tuple(_json(row["status_codes_json"]) or ()),
        requests=row["requests"],
    )


async def find_batch(step: ReadStep, target_date: date) -> BatchRow | None:
    statement = select(obs_batches).where(obs_batches.c.channel == CHANNEL, obs_batches.c.target_date == codec.encode_day(target_date))
    row = (await step.execute(statement)).mappings().first()
    return None if row is None else _batch(row)


async def previous_batch(step: ReadStep, target_date: date) -> BatchRow | None:
    """The latest Trends batch before `target_date`: the comparison day for the all-zero rate and the userType, and
    where lasting codes (parse_error) are carried from."""
    statement = (
        select(obs_batches)
        .where(obs_batches.c.channel == CHANNEL, obs_batches.c.target_date < codec.encode_day(target_date))
        .order_by(obs_batches.c.target_date.desc())
        .limit(1)
    )
    row = (await step.execute(statement)).mappings().first()
    return None if row is None else _batch(row)


@dataclass(frozen=True)
class NewBatch:
    id: str
    mode: str
    collect_mode: str
    target_date: date
    window_end: datetime | None
    plan: Mapping[str, Any] | None
    planned_units: int | None
    status_codes: tuple[str, ...]
    outcome: str = "running"
    finished_at: datetime | None = None
    summary: Mapping[str, Any] | None = None  # summary_json; None: no unit done yet


async def insert_batch(step: LeasedStep, batch: NewBatch) -> None:
    values = {
        "id": batch.id,
        "channel": CHANNEL,
        "mode": batch.mode,
        "collect_mode": batch.collect_mode,
        "target_date": codec.encode_day(batch.target_date),
        "window_end": stamp(batch.window_end) if batch.window_end is not None else None,
        "collector_version": COLLECTOR_VERSION,
        "lease_generation": step.token.generation,
        "started_at": stamp(step.now),
        "finished_at": stamp(batch.finished_at) if batch.finished_at is not None else None,
        "outcome": batch.outcome,
        "planned_units": batch.planned_units,
        "fetched_units": 0 if batch.planned_units is not None else None,
        "plan_json": dict(batch.plan) if batch.plan is not None else None,
        "summary_json": dict(batch.summary) if batch.summary is not None else {"units": {}},
        "status_codes_json": list(batch.status_codes),
    }
    await step.execute(insert(obs_batches).values(**values))


async def update_batch(step: LeasedStep, batch_id: str, **values: Any) -> None:
    """Changes one batch row, recording the generation that wrote it last."""
    await step.execute(update(obs_batches).where(obs_batches.c.id == batch_id).values(lease_generation=step.token.generation, **values))


async def count_request(step: LeasedStep, batch_id: str) -> None:
    await step.execute(update(obs_batches).where(obs_batches.c.id == batch_id).values(requests=obs_batches.c.requests + 1))


async def abandon_unfinished(step: LeasedStep, before: date) -> int:
    """Earlier Trends batches that never finished (their process died after the last trigger of their night) are
    closed as failed, so the run_status view never shows a night as still running. Returns how many."""
    where = (obs_batches.c.channel == CHANNEL) & (obs_batches.c.target_date < codec.encode_day(before)) & (obs_batches.c.finished_at.is_(None))
    done = await step.execute(update(obs_batches).where(where).values(outcome="failed", finished_at=stamp(step.now)))
    return done.rowcount or 0


@dataclass(frozen=True)
class RequestContext:
    """What the request row says beyond the record: its batch, target date, budget item and unit."""

    batch_id: str
    target_date: date
    budget_item: str
    identity: str | None
    geo: str | None


def _error(record: RequestRecord) -> str | None:
    return None if record.error_class is None else record.error_class[:MAX_ERROR]


async def insert_request(step: LeasedStep, context: RequestContext, record: RequestRecord) -> int:
    """One HTTP request's row (never a cookie, never a value of the series); returns its id for the raw rows."""
    values = {
        "channel": CHANNEL,
        "batch_id": context.batch_id,
        "lease_generation": step.token.generation,
        "budget_day": codec.encode_day(context.target_date),
        "budget_item": context.budget_item,
        "identity": context.identity,
        "geo": context.geo,
        "endpoint": record.phase.value,
        "sent_at": stamp(record.started_at),
        "status_code": record.http_status,
        "latency_ms": round(record.latency_ms),
        "redirect_host": record.redirect_host,
        "egress_ip": record.egress.ip,
        "egress_measured_at": stamp(record.egress.measured_at) if record.egress.measured_at is not None else None,
        "user_type": record.user_type,
        "fetch_status": record.fetch_status.value,
        "error": _error(record),
    }
    done = await step.execute(insert(obs_requests).values(**values))
    return done.inserted_primary_key[0]


async def insert_raw(step: LeasedStep, rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        await step.execute(insert(obs_raw).values(**row))


async def canary_extinguish_reasons(step: ReadStep, *, since: date | None) -> tuple[str | None, ...]:
    """Why each canary target date the breaker put out was put out, oldest first, from the budget rows (collect_mode
    canary1 or canary2, an extinguish time): breaker.ExtinguishReason, `wall` for a captcha or consent page. The row is
    written in the same step as the request row that put the day out, so a crash after it cannot lose it. `since`
    (PICK_OBS_CANARY_SINCE) starts the count afresh for a fix-and-rerun (TR-30)."""
    where = (obs_budget.c.channel == CHANNEL) & obs_budget.c.collect_mode.in_(EXTINGUISHED_CANARY_MODES) & obs_budget.c.extinguished_at.is_not(None)
    if since is not None:
        where = where & (obs_budget.c.budget_day >= codec.encode_day(since))
    found = await step.execute(select(obs_budget.c.extinguish_reason).where(where).order_by(obs_budget.c.budget_day))
    return tuple(reason for (reason,) in found.all())


# ---- raw rows (ggwp_obs_raw) ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SentRequest:
    """A request of the unit's last attempt and its row id."""

    record: RequestRecord
    request_id: int


def _last(sent: Sequence[SentRequest], phase: Phase) -> SentRequest | None:
    return next((item for item in reversed(sent) if item.record.phase is phase), None)


def _line_role(unit: QueryUnit, term: str) -> str:
    if unit.identity is None:
        return "market"  # a market series or the contract check: a phrase, never a drama
    return "bare" if term == unit.bare else "variant"


def _related_data(result: FetchResult) -> dict | None:
    found = result.related
    if found is None or found.top is None:
        return None

    def listed(queries):
        return [{"query": q.query, "value": q.value, "formattedValue": q.formatted_value} for q in queries]

    return {"top": listed(found.top), "rising": listed(found.rising)}


def _series_rows(unit: QueryUnit, result: FetchResult, request: SentRequest | None, base: dict, now: datetime) -> list[dict]:
    query, timeline = unit.query(), result.timeline
    rows = []
    for index, term in enumerate(query.terms):
        line = timeline.line(term)
        status = line.status if line is not None else timeline.status
        rows.append(
            {
                **base,
                "request_id": request.request_id if request else None,
                "line_role": _line_role(unit, term),
                "line_index": index,
                "fetch_status": status.value,
                "data_json": line.as_raw() if line is not None else None,
                "fetched_at": stamp(request.record.started_at if request else now),
            }
        )
    return rows


def _related_row(unit: QueryUnit, result: FetchResult, request: SentRequest | None, base: dict, now: datetime) -> dict:
    params = {**base["params_json"], "related_term": unit.query().related_keyword, "widget_missing": result.related.widget_missing}
    return {
        **base,
        "request_id": request.request_id if request else None,
        "line_role": "related",
        "line_index": None,
        "fetch_status": result.related.status.value,
        "params_json": params,
        "data_json": _related_data(result),
        "fetched_at": stamp(request.record.started_at if request else now),
    }


def raw_rows(batch_id: str, unit: QueryUnit, result: FetchResult, sent: Sequence[SentRequest], now: datetime) -> list[dict]:
    """One row per requested line (and one for the related queries): the series as the answer had it, or NULL data
    with the failure's status when the unit failed before it (never zeros, counterexample 1)."""
    query = unit.query()
    base = {
        "batch_id": batch_id,
        "identity": unit.identity,
        "geo": unit.geo,
        "property": query.search_property,
        "time_range": query.timeframe,
        "user_type": result.user_type,
        "params_json": {**query.request_params(), "unit": unit.key, "item": unit.item},
    }
    series_request = _last(sent, Phase.MULTILINE) or _last(sent, Phase.EXPLORE)
    rows = _series_rows(unit, result, series_request, base, now) if result.timeline is not None else []
    if result.related is not None:
        rows.append(_related_row(unit, result, _last(sent, Phase.RELATED) or series_request, base, now))
    return rows
