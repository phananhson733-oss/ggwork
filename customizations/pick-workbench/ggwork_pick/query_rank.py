"""Pinned mirror rank reads on an authenticated caller-owned read-only connection.

The caller owns version resolution, search_path, transaction and the end-to-end
budget. This module never opens a connection or accepts SQL/schema from clients.
Ordering is the board's PostgreSQL ordering, including database text collation.
"""

import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    import asyncpg

from ggwork_pick.completion_contracts import CommonQuery, QueryPeriod

THEATER_RANKS = ("kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn")
DAILY_RANKS = ("kd", "qc", "qr")
GRADES = ("SSS", "SS", "S", "A", "B", "C", "D")


class RankSignal(TypedDict):
    row_key: str
    kind: str
    ord: int
    evidence_on: str | None
    rank: int | None
    grade: str
    note: str
    payload: dict[str, Any]


class TheaterRankRow(TypedDict):
    row_key: str
    signal: RankSignal
    day_rank: int | None
    day_note: str


class LedgerRow(TypedDict):
    bill_date: str
    book_id: str
    promotion_type: str
    canonical_id: str | None
    title: str
    locale: str
    order_cnt: int
    source_rows: int
    same_day_clicks: int


class RankFacets(TypedDict):
    ranks: dict[str, int]
    grades: dict[str, int]


class PeriodOptions(TypedDict):
    days: list[str]
    weeks: list[dict[str, str]]
    resolution: Literal["latest", "exact", "label", "ambiguous", "missing"]


@dataclass(frozen=True)
class RankQueryResult:
    row_keys: list[str]
    total: int
    matched: int
    facets: RankFacets
    actual_period: QueryPeriod | None
    period_options: PeriodOptions
    has_more: bool
    rank_rows: list[TheaterRankRow] = field(default_factory=list)
    bill_rows: list[LedgerRow] = field(default_factory=list)
    bill_totals: dict[str, int] | None = None
    effective_sort: str | None = None
    legacy_total: int | None = None
    rank_limit: int | None = None


async def query_rank(conn: "asyncpg.Connection", request: CommonQuery, *, rules: dict, meta: dict, deadline: float | None = None) -> RankQueryResult:
    """Read one rank page; deadline is the parent's absolute loop/monotonic time.

    Rank membership intentionally includes off-shelf rows and is independent of
    the current theater eligibility rules. ``rules`` keeps the common reader
    signature; these historical source facts are not candidate recommendations.
    ``has_more`` describes pages inside a capped rank; ``matched`` still reports
    all comparable matches and lets the UI disclose the top-50 truncation.
    """
    end = min(deadline if deadline is not None else float("inf"), asyncio.get_running_loop().time() + request.budget_ms / 1000)
    if end <= asyncio.get_running_loop().time():
        raise TimeoutError("rank query deadline expired")
    async with asyncio.timeout_at(end):
        facets = await _facets(conn, request, meta)
        if request.rank == "rs_ledger":
            return await _ledger(conn, request, facets)
        if (request.rank or "").startswith("rs_"):
            return await _rs(conn, request, meta, facets)
        return await _theater(conn, request, facets)


async def _period(conn, req, kind):
    options = {"days": [], "weeks": [], "resolution": "latest"}
    if kind in DAILY_RANKS:
        rows = await conn.fetch(
            """SELECT DISTINCT x->>0 AS d FROM catalog_signals s,
            jsonb_array_elements(s.payload->'h') x WHERE s.kind = $1
            AND jsonb_typeof(s.payload->'h') = 'array' ORDER BY d DESC""",
            kind,
        )
        options["days"] = [r["d"] for r in rows if r["d"]]
        choices = options["days"]
        raw = req.period.value
        value = raw if raw in choices else (choices[0] if choices else None)
        options["resolution"] = "latest" if raw is None else "exact" if raw in choices else "missing"
        return QueryPeriod(kind="daily", value=value) if value else None, options
    if kind == "kw":
        rows = await conn.fetch("""SELECT x->>0 AS start, max(x->>1) AS week FROM catalog_signals s,
            jsonb_array_elements(s.payload->'h') x WHERE s.kind = 'kw'
            AND jsonb_typeof(s.payload->'h') = 'array' GROUP BY start ORDER BY start DESC""")
        options["weeks"] = [
            {"week": r["week"], "start": r["start"]} for r in rows if r["week"] and r["start"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["start"])
        ]
        weeks = options["weeks"]
        raw = req.period.value or req.legacy_week_label
        value = weeks[0]["start"] if weeks else None
        if raw:
            exact = [w for w in weeks if w["start"] == raw]
            labels = [w for w in weeks if w["week"] == raw] if req.period.value is None else []
            hits = exact or labels
            if hits:
                value = hits[0]["start"]
                options["resolution"] = "exact" if exact else "label" if len(hits) == 1 else "ambiguous"
            else:
                options["resolution"] = "missing"
        return QueryPeriod(kind="weekly", value=value) if value else None, options
    return QueryPeriod(), options


def _rank_row(row):
    signal = {k: row[k] for k in ("row_key", "kind", "ord", "evidence_on", "rank", "grade", "note", "payload")}
    if isinstance(signal["payload"], str):
        signal["payload"] = json.loads(signal["payload"])
    return {"row_key": row["row_key"], "signal": signal, "day_rank": row.get("day_rank"), "day_note": row.get("day_note", "")}


async def _theater(conn, req, facets):
    kind = req.rank or req.signal_kind or "kd"
    if kind not in THEATER_RANKS:
        raise ValueError("unsupported theater rank")
    if kind in ("sm", "mg"):
        grades = await conn.fetch("SELECT grade, count(DISTINCT row_key)::int AS n FROM catalog_signals WHERE kind=$1 GROUP BY grade", kind)
        facets["grades"] = {r["grade"]: r["n"] for r in grades if r["grade"] in GRADES}
    period, options = await _period(conn, req, kind)
    if period is None:
        return RankQueryResult([], 0, 0, facets, None, options, False, legacy_total=0)
    args = [kind]
    extra = ""
    if kind in DAILY_RANKS:
        args.append(period.value)
        source = """FROM catalog_signals s JOIN catalog_rows c ON c.row_key = s.row_key
            JOIN LATERAL (SELECT (x->>1)::int AS day_rank, coalesce(x->>2, '') AS day_note
              FROM jsonb_array_elements(s.payload->'h') x
              WHERE x->>0 = $2 AND jsonb_typeof(x->1) = 'number' LIMIT 1) e ON true
            WHERE s.kind = $1 AND jsonb_typeof(s.payload->'h') = 'array'"""
        order = "e.day_rank, c.title, c.row_key"
        extra = ", e.day_rank, e.day_note"
        total = await conn.fetchval("SELECT count(*)::int " + source, *args)
    else:
        where = "s.kind = $1"
        if kind == "kw":
            args.append(period.value)
            where += " AND jsonb_typeof(s.payload->'h') = 'array' AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.payload->'h') x WHERE x->>0 = $2)"
        total = await conn.fetchval("SELECT count(DISTINCT s.row_key)::int FROM catalog_signals s WHERE " + where, *args)
        if kind in ("sm", "mg") and req.grade:
            args.append(req.grade)
            where += f" AND s.grade = ${len(args)}"
        inner = "(s.payload->>'weeks')::int DESC NULLS LAST, s.ord" if kind == "kw" else "s.ord"
        source = (
            f"FROM (SELECT DISTINCT ON (s.row_key) s.* FROM catalog_signals s WHERE {where} "
            f"ORDER BY s.row_key, {inner}) s JOIN catalog_rows c ON c.row_key = s.row_key"
        )
        if kind == "kw":
            order = "(s.payload->>'weeks')::int DESC NULLS LAST, c.title, c.row_key"
        elif kind in ("sm", "mg"):
            order = "array_position(ARRAY['SSS','SS','S','A','B','C','D']::text[], s.grade) NULLS LAST, c.listed_on DESC NULLS LAST, c.title, c.row_key"
        else:
            order = "s.evidence_on DESC NULLS LAST, c.listed_on DESC NULLS LAST, c.title, c.row_key"
    matched = await conn.fetchval("SELECT count(*)::int " + source, *args)
    rows = await conn.fetch(f"SELECT s.*{extra} {source} ORDER BY {order} LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}", *args, req.limit, req.offset)
    return RankQueryResult(
        [r["row_key"] for r in rows],
        total,
        matched,
        facets,
        period,
        options,
        req.offset + len(rows) < matched,
        rank_rows=[_rank_row(r) for r in rows],
        legacy_total=matched,
    )


RS_ORDER = {
    "rr": "rr DESC",
    "d1": "(rr::numeric - s1_rr::numeric) DESC",
    "d7": "(rr::numeric - s7_rr::numeric) DESC",
    "dp1": "(promoters_cnt - s1_p) DESC",
    "dp7": "(promoters_cnt - s7_p) DESC",
    "promoters": "promoters_cnt DESC",
    "publish": "publish_at DESC",
    "bill": "bill_rank ASC",
    "eff": "(round(rr::numeric, 2) / NULLIF(promoters_cnt, 0)) DESC",
    "gsc": "search_impressions DESC",
    "clicks": "clicks7 DESC",
}
GROWTH_COLUMNS = {"d1": "rr1", "d7": "rr7", "dp1": "p1", "dp7": "p7"}
FORCED_SORT = {"rs_pc": "promoters", "rs_clk": "clicks", "rs_gsc": "gsc", "rs_bill": "bill"}
METRIC_FILTER = {"rs_pc": "promoters_cnt > 0", "rs_clk": "clicks7 > 0", "rs_gsc": "search_impressions > 0", "rs_bill": "bill_orders > 0"}


async def _rs(conn, req, meta, facets):
    kind = req.rank
    sort = FORCED_SORT.get(kind, req.rs_sort)
    if kind == "rs_growth" and sort not in GROWTH_COLUMNS:
        sort = "d7"
    conditions = []
    if kind == "rs_cand":
        conditions.append("has_signal")
    if kind == "rs_growth":
        conditions.append(GROWTH_COLUMNS[sort] + " IS NOT NULL")
    if kind in METRIC_FILTER:
        conditions.append(METRIC_FILTER[kind])
    base = " AND ".join(conditions) or "true"
    total = await conn.fetchval("SELECT count(*)::int FROM rs_rows WHERE " + base)
    args = []
    if req.rs_locale:
        args.append(req.rs_locale)
        conditions.append(f"locale = ${len(args)}")
    if req.rs_bucket:
        args.append(str(meta["as_of"]))
        age = f"(((${len(args)}::text)::timestamptz AT TIME ZONE 'UTC')::date - (publish_at AT TIME ZONE 'UTC')::date)"
        lo, hi = {"0-7": (0, 7), "8-30": (8, 30), "31-90": (31, 90), "91-365": (91, 365), "366+": (366, None)}[req.rs_bucket]
        conditions.append(f"publish_at IS NOT NULL AND {age} >= {lo}" + (f" AND {age} <= {hi}" if hi else ""))
    if req.query:
        args.extend([f"%{req.query}%", req.query])
        pattern, exact = len(args) - 1, len(args)
        conditions.append(f"(title ILIKE ${pattern} OR drama_id = ${exact} OR drama_id = (SELECT canonical_id FROM rs_ids WHERE id = ${exact}))")
    where = " AND ".join(conditions) or "true"
    matched = await conn.fetchval("SELECT count(*)::int FROM rs_rows WHERE " + where, *args)
    cap = 50 if kind == "rs_growth" else None
    limit = min(req.limit, max(0, cap - req.offset)) if cap else req.limit
    rows = await conn.fetch(
        f"SELECT row_key FROM rs_rows WHERE {where} ORDER BY {RS_ORDER[sort]} NULLS LAST, drama_id ASC LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
        *args,
        limit,
        req.offset,
    )
    return RankQueryResult(
        [r["row_key"] for r in rows],
        total,
        matched,
        facets,
        QueryPeriod(),
        {"days": [], "weeks": [], "resolution": "latest"},
        req.offset + len(rows) < (min(matched, cap) if cap else matched),
        effective_sort=sort,
        legacy_total=None if cap else matched,
        rank_limit=cap,
    )


def _meta_count(value) -> int:
    # The existing feed accepts scalar metadata; the board's Number()/finite
    # fallback treats missing, null and nonnumeric counts as zero.
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    if number < 0 or not number.is_integer():
        raise ValueError("source rank count is not a nonnegative integer")
    return int(number)


async def _facets(conn, req, meta):
    counts = await conn.fetch("SELECT kind, count(DISTINCT row_key)::int AS n FROM catalog_signals GROUP BY kind")
    raw = meta.get("rsCounts", {})
    raw = raw if isinstance(raw, dict) else {}
    growth = {"d1": "growthD1", "d7": "growthD7", "dp1": "growthDp1", "dp7": "growthDp7"}.get(req.rs_sort, "growthD7")
    names = {"rs_rr": "all", "rs_growth": growth, "rs_cand": "cand", "rs_pc": "pc", "rs_clk": "clk", "rs_gsc": "gsc", "rs_bill": "bill", "rs_ledger": "ledger"}
    ranks = {key: _meta_count(raw.get(value)) for key, value in names.items()}
    ranks.update({r["kind"]: r["n"] for r in counts if r["kind"] in THEATER_RANKS})
    return {"ranks": ranks, "grades": {}}


async def _ledger(conn, req, facets):
    totals = dict(
        await conn.fetchrow("""SELECT coalesce(sum(source_rows), 0)::int AS rows,
        count(*)::int AS merged_rows, coalesce(sum(order_cnt), 0)::int AS orders,
        count(*) FILTER (WHERE same_day_clicks > 0)::int AS merged_with_clicks,
        coalesce(sum(source_rows) FILTER (WHERE same_day_clicks > 0), 0)::int AS rows_with_clicks
        FROM rs_bill_orders""")
    )
    rows = await conn.fetch(
        """SELECT o.bill_date, o.book_id, o.promotion_type, o.canonical_id,
        coalesce(i.title, o.book_title) AS title, coalesce(i.locale, '') AS locale,
        o.order_cnt, o.source_rows, o.same_day_clicks FROM rs_bill_orders o
        LEFT JOIN rs_ids i ON i.id = o.book_id
        ORDER BY o.bill_date DESC, o.order_cnt DESC, o.book_id, o.promotion_type LIMIT $1 OFFSET $2""",
        req.limit,
        req.offset,
    )
    total = totals["merged_rows"]
    keys = list(dict.fromkeys("reelshort-" + row["canonical_id"] for row in rows if row["canonical_id"] is not None))
    return RankQueryResult(
        keys,
        total,
        total,
        facets,
        QueryPeriod(),
        {"days": [], "weeks": [], "resolution": "latest"},
        req.offset + len(rows) < total,
        bill_rows=[dict(r) for r in rows],
        bill_totals=totals,
        legacy_total=total,
    )
