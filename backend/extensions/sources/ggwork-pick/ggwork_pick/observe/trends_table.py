"""GET /api/pick/obs/trends-table: the simplified radar's read-only table (simplified scope 2026-09-30, sections 2 and 6
items 4 and 6). Every signed-in user reads the same answer, from the tables the gateway owns; no migration, no new grant.

The table is one night's task list: the newest planned batch of the stable mode (status.table_batch), and every drama
unit its plan_json lists, truncated ones included, in its order (the source's picks, trends/top_dramas.py). Each row
carries the pick's basis from plan_json.notes.top_dramas, and what that night fetched for it:
- the bare line's raw row (ggwp_obs_raw) of that batch, the series as Google answered it: a day without data
  (hasData false) is null, never 0; `partial` marks a day that is not a complete UTC day (isPartial, or on or after the
  batch's window_end);
- result: data (the line is ok), no_data (ok_zero or no_data: Google answered, with nothing), not_fetched (a failed
  request, a unit cut from the plan, stopped by the breaker or the deadline, or never reached), pending (the batch is
  still running and its deadline has not passed); status names the fetch status or the stop reason.
A unit the night never got to has no raw row (run.py writes raw rows at the end of a unit), so it is not_fetched: an
older night's curve never stands in for it. Averages, change, labels and links are the page's (frontend
core/pick/trends-table.ts), so a threshold changes with a frontend deploy only.

The banners are /sync's Trends banners (status.banners_of, the stale one judged on table batches), read in the same
snapshot, without shadow_mode: the simplified radar publishes no set, so the publish switch says nothing about the
table. The reads share one session, each statement its own snapshot: a unit that finishes in between shows up with its
data, which is the newer answer, never an older one. At most ROW_LIMIT rows and MAX_POINTS points a row.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import select

from ggwork_pick.models import obs_batches, obs_raw
from ggwork_pick.observe.contract import Frozen, Identity, Stamp, TrendGeo
from ggwork_pick.observe.contract_api import ObsBanner
from ggwork_pick.observe.instants import instant, stamp
from ggwork_pick.observe.status import banners_of, status_row, table_batch
from ggwork_pick.observe.trends.budget import DEADLINE
from ggwork_pick.observe.trends.top_dramas import NOTES_KEY, SOURCE_NAME
from ggwork_pick.observe.trends.units import QueryUnit, SessionPlan
from ggwork_pick.repository import SHARED_OWNER, PickRepository

logger = logging.getLogger(__name__)

ROW_LIMIT = 200  # stable's plan holds 165 such units at most (budget.plan_budget / 2); the source takes 100
MAX_POINTS = 40  # today 1-m answers 30 or 31 days
HIDDEN_BANNERS = frozenset({"shadow_mode"})
DATA, NO_DATA, NOT_FETCHED, PENDING = "data", "no_data", "not_fetched", "pending"
ANSWERED_EMPTY = frozenset({"ok_zero", "no_data"})
NOT_REACHED = "not_reached"  # a unit a night that ended (or died) never got to, with no reason recorded
UNREADABLE = "unreadable"  # a raw row the table cannot read: logged, shown as not fetched

Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Short = Annotated[str, Field(max_length=100)]
BasisKind = Literal["qc", "qr", "kd", "revenue"]
Result = Literal["data", "no_data", "not_fetched", "pending"]


class TableBasis(Frozen):
    kind: BasisKind
    board_date: Day | None
    rank: Annotated[int, Field(ge=0, le=1_000_000)]
    identity: Identity | None = None


class TablePoint(Frozen):
    date: Day
    value: Annotated[int, Field(ge=0, le=100)] | None
    partial: bool


class TableRow(Frozen):
    order: Annotated[int, Field(ge=1)]
    unit: Short
    identity: Identity
    title: Annotated[str, Field(max_length=500)]
    platform: Short
    language: Short
    term: Annotated[str, Field(min_length=1, max_length=200)]
    geo: TrendGeo
    time_range: Short
    basis: list[TableBasis]
    result: Result
    status: Short | None
    series: list[TablePoint] | None


class TableBoard(Frozen):
    kind: BasisKind
    board_date: Day | None
    listed: Annotated[int, Field(ge=0)]


class TableRevenue(Frozen):
    available: bool
    reason: Short | None
    mirror_version: int | None
    day: Day | None
    filled: Annotated[int, Field(ge=0)]


class TableSources(Frozen):
    boards: list[TableBoard]
    revenue: TableRevenue | None


class TableCounts(Frozen):
    planned: int
    data: int
    no_data: int
    not_fetched: int
    pending: int


class TableBatch(Frozen):
    batch_id: Short
    target_date: Day
    collect_mode: Short
    outcome: Short
    started_at: Stamp
    finished_at: Stamp | None
    window_end: Stamp
    catalog_batch_id: Annotated[str, Field(max_length=200)] | None
    sources: TableSources
    counts: TableCounts


class TrendsTable(Frozen):
    checked_at: Stamp
    banners: list[ObsBanner]
    batch: TableBatch | None
    rows: list[TableRow]
    row_limit: int
    truncated: bool


# ---- reading ---------------------------------------------------------------------------------------------------------


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


async def _table_batch(session) -> Mapping[str, Any] | None:
    columns = [
        obs_batches.c[name] for name in ("id", "target_date", "collect_mode", "outcome", "started_at", "finished_at", "window_end", "plan_json", "summary_json")
    ]
    statement = select(*columns).where(table_batch()).order_by(obs_batches.c.target_date.desc(), obs_batches.c.id.desc()).limit(1)
    return (await session.execute(statement)).mappings().first()


async def _bare_lines(session, batch_id: str) -> dict[str, Mapping[str, Any]]:
    """The batch's bare lines by unit key, the last one a unit wrote winning."""
    statement = (
        select(obs_raw.c.params_json, obs_raw.c.fetch_status, obs_raw.c.data_json)
        .where(obs_raw.c.batch_id == batch_id, obs_raw.c.line_role == "bare")
        .order_by(obs_raw.c.id)
    )
    found: dict[str, Mapping[str, Any]] = {}
    for row in (await session.execute(statement)).mappings().all():
        params = _json(row["params_json"])
        key = params.get("unit") if isinstance(params, Mapping) else None
        if isinstance(key, str):
            found = {**found, key: {"status": row["fetch_status"], "data": _json(row["data_json"])}}
    return found


# ---- one row ---------------------------------------------------------------------------------------------------------


def series_of(data: object, window_end: date) -> list[dict[str, Any]]:
    """A raw line's points: its UTC day, the value (null where hasData is false), and whether the day is partial (not
    a complete UTC day). ValueError when the arrays do not line up or a value is out of range."""
    if not isinstance(data, Mapping):
        raise ValueError("line data is an object")
    times, values = data.get("time"), data.get("value")
    partial, has_data = data.get("isPartial") or [], data.get("hasData") or []
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        raise ValueError("time and value line up")
    points = []
    for index, (moment, value) in enumerate(zip(times, values, strict=True)):
        day = datetime.fromtimestamp(int(moment), UTC).date()
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("a value is 0 to 100")
        missing = index < len(has_data) and has_data[index] is False
        cut = (index < len(partial) and partial[index] is True) or day >= window_end
        points.append({"date": day.isoformat(), "value": None if missing else value, "partial": cut})
    return points[-MAX_POINTS:]


def _outcome(
    unit: QueryUnit, line: Mapping[str, Any] | None, reasons: Mapping[str, str], *, truncated: bool, pending: bool, window_end: date
) -> dict[str, Any]:
    """result, status and series of one unit."""
    if truncated:
        return {"result": NOT_FETCHED, "status": "truncated", "series": None}
    if line is None:
        return (
            {"result": PENDING, "status": None, "series": None}
            if pending
            else {"result": NOT_FETCHED, "status": reasons.get(unit.key, NOT_REACHED), "series": None}
        )
    status, data = line["status"], line["data"]
    try:
        series = series_of(data, window_end) if data is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        logger.warning("[pick-obs] trends table: unit %s has a raw line the table cannot read", unit.key)
        return {"result": NOT_FETCHED, "status": UNREADABLE, "series": None}
    if status == "ok" and series is not None:
        return {"result": DATA, "status": status, "series": series}
    return {"result": NO_DATA if status in ANSWERED_EMPTY else NOT_FETCHED, "status": status, "series": series}


def _row(order: int, unit: QueryUnit, pick: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "order": order,
        "unit": unit.key,
        "identity": unit.identity,
        "title": str(pick.get("title") or unit.bare or unit.terms[0]),
        "platform": str(pick.get("platform") or ""),
        "language": str(pick.get("language") or ""),
        "term": unit.bare or unit.terms[0],
        "geo": unit.geo,
        "time_range": unit.query().timeframe,
        "basis": list(pick.get("basis") or []),
        **outcome,
    }


# ---- the table -------------------------------------------------------------------------------------------------------


def _pending(batch: Mapping[str, Any], now: datetime) -> bool:
    """Still running, before its night's deadline: a unit not reached yet may still come."""
    target = date.fromisoformat(batch["target_date"])
    return batch["outcome"] == "running" and now < datetime.combine(target, DEADLINE, UTC)


def _counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    tally = {result: sum(1 for row in rows if row["result"] == result) for result in (DATA, NO_DATA, NOT_FETCHED, PENDING)}
    return {"planned": len(rows), **tally}


def table_of(batch: Mapping[str, Any], lines: Mapping[str, Mapping[str, Any]], now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """The batch's header, its rows (at most ROW_LIMIT) and whether rows were left out."""
    plan = SessionPlan.from_dict(_json(batch["plan_json"]))
    notes = plan.notes.get(NOTES_KEY) if isinstance(plan.notes.get(NOTES_KEY), Mapping) else {}
    picks = notes.get("picks") if isinstance(notes.get("picks"), Mapping) else {}
    summary = _json(batch["summary_json"]) or {}
    reasons = {entry["key"]: entry["reason"] for entry in summary.get("uncovered_units", []) if isinstance(entry, Mapping)}
    reasons = {**reasons, **{key: entry["reason"] for key, entry in (summary.get("units") or {}).items() if isinstance(entry, Mapping) and entry.get("reason")}}
    window_end = instant(batch["window_end"]).date()
    pending = _pending(batch, now)
    listed = [(unit, False) for unit in plan.tasks.planned if unit.identity is not None]
    listed += [(unit, True) for unit in plan.tasks.truncated if unit.identity is not None]
    rows = [
        _row(order, unit, picks.get(unit.key) or {}, _outcome(unit, lines.get(unit.key), reasons, truncated=cut, pending=pending, window_end=window_end))
        for order, (unit, cut) in enumerate(listed[:ROW_LIMIT], start=1)
    ]
    header = {
        "batch_id": batch["id"],
        "target_date": batch["target_date"],
        "collect_mode": batch["collect_mode"],
        "outcome": batch["outcome"],
        "started_at": stamp(instant(batch["started_at"])),
        "finished_at": stamp(instant(batch["finished_at"])) if batch["finished_at"] is not None else None,
        "window_end": stamp(instant(batch["window_end"])),
        "catalog_batch_id": plan.catalog_batch_id,
        "sources": {"boards": list(notes.get("boards") or []), "revenue": _revenue_note(notes.get("revenue"))},
        "counts": _counts(rows),
    }
    return header, rows, len(listed) > ROW_LIMIT


def _revenue_note(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {name: value.get(name) for name in ("available", "reason", "mirror_version", "day", "filled")}


async def trends_table(repo: PickRepository, *, now: datetime) -> dict[str, Any]:
    """The table at `now` (timezone-aware). repo must be PickRepository.shared(...): the same table for everyone."""
    if repo.owner_id != SHARED_OWNER:
        raise ValueError("趋势表按共享数据计算：传 PickRepository.shared(...)")
    moment = instant(now)
    async with repo.session_factory() as session:
        banners = [banner for banner in banners_of("trends", await status_row(session, moment), moment) if banner.code not in HIDDEN_BANNERS]
        batch = await _table_batch(session)
        found = await _bare_lines(session, batch["id"]) if batch is not None else {}
    header, rows, cut = table_of(batch, found, moment) if batch is not None and _is_ours(batch) else (None, [], False)
    table = TrendsTable(checked_at=stamp(moment), banners=banners, batch=header, rows=rows, row_limit=ROW_LIMIT, truncated=cut)
    return table.model_dump(mode="json")


def _is_ours(batch: Mapping[str, Any]) -> bool:
    """A stable batch another source wrote (none today; TR-18's watch list was shelved) is not this table's."""
    plan = _json(batch["plan_json"])
    return isinstance(plan, Mapping) and plan.get("source") == SOURCE_NAME
