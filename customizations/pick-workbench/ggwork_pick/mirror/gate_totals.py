"""G7, the control totals (plan 5.3; implementation note P2-4): RealShort's own counts, recounted on the built version.

Each item of manifest.meta the manifest's functions computed at as_of is recomputed over pickm_vN with the same meaning
(checked against RealShort 816ca2e) and must be equal: an int for a count, the same instant for a timestamp, the same
string for metricAt. A theater kind with no signals has no rankCounts key on RealShort's side (queries-rank.ts:139-140),
so a missing key is 0 on either side. THEATER_BASES and BASES are read from manifest.meta.rules.basisLabels, never
written in here. A failure degrades the run (consequence mirror); if these counts drift from RealShort's, every run would.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ggwork_pick.mirror.contracts import ts_datetime
from ggwork_pick.mirror.gate_result import CONTROL_TOTALS, MISSING, STATEMENT_TIMEOUT, GateResult, count_or_none, dotted, is_count, read, value_at
from ggwork_pick.mirror.versions import check_schema_name

# rs:src/lib/pick/feed-map.ts:303-315: the three ReelShort conditions, in the order v1 appends them; BASES less these
# are the theaters (request.ts:43, :110-114).
RS_FLAG_KINDS = ("clk", "bill", "gsc")
# queries-rank.ts:127-137: each rs rank counts what one of rsCounts does. rs_growth follows the default sort rr, which is
# no growth sort, so d7 (request.ts:23, :443; queries-rank.ts:289).
RS_RANK_COUNTS = MappingProxyType(
    {"rs_rr": "all", "rs_growth": "growthD7", "rs_cand": "cand", "rs_pc": "pc", "rs_clk": "clk", "rs_gsc": "gsc", "rs_bill": "bill", "rs_ledger": "ledger"}
)

# RealShort's own functions over the version (implementation note G7, checked against 816ca2e): loadFreshness
# (queries.ts:257-291), loadRsCounts (observe/queries.ts:533-570), loadRankMeta (queries-rank.ts:106), loadPostedStats
# (queries-posted.ts:249-280, coalesce as there), the posted tab's STATE_WHERE counts (:140-145, :184-189), readCounts'
# ledger (export-v2.ts:423-436). Sums over no rows are NULL in SQL and 0 in the manifest: hence coalesce.
_TOTALS = """SELECT
  (SELECT count(*) FROM {s}.catalog_rows) AS catalog_rows_n,
  (SELECT count(*) FROM {s}.catalog_rows WHERE has_signal) AS with_signal,
  (SELECT max(imported_at) FROM {s}.catalog_rows) AS imported_at,
  (SELECT count(*) FROM {s}.catalog_signals) AS signals,
  (SELECT count(*) FROM {s}.catalog_accounts) AS accounts,
  (SELECT coalesce(sum(source_rows), 0) FROM {s}.rs_bill_orders) AS ledger,
  (SELECT coalesce(sum(order_cnt), 0) FROM {s}.rs_bill_orders) AS ledger_orders,
  rs.*, p.*
FROM (SELECT count(*) AS rs_all,
             count(*) FILTER (WHERE has_signal) AS rs_candidates,
             count(*) FILTER (WHERE search_impressions > 0 OR clicks7 > 0 OR bill_orders > 0) AS cand,
             count(*) FILTER (WHERE rr1 IS NOT NULL) AS growth_d1,
             count(*) FILTER (WHERE rr7 IS NOT NULL) AS growth_d7,
             count(*) FILTER (WHERE p1 IS NOT NULL) AS growth_dp1,
             count(*) FILTER (WHERE p7 IS NOT NULL) AS growth_dp7,
             count(*) FILTER (WHERE promoters_cnt > 0) AS pc,
             count(*) FILTER (WHERE clicks7 > 0) AS clk,
             count(*) FILTER (WHERE search_impressions > 0) AS gsc,
             count(*) FILTER (WHERE bill_orders > 0) AS bill
        FROM {s}.rs_rows) rs,
     (SELECT count(*) AS posted,
             count(*) FILTER (WHERE post_count > 0) AS pub,
             count(*) FILTER (WHERE post_count = 0 AND sched_count > 0) AS sched,
             count(*) FILTER (WHERE post_count = 0 AND sched_count = 0) AS unposted,
             count(*) FILTER (WHERE cardinality(row_keys) = 0 AND cardinality(drama_ids) = 0) AS nomatch,
             coalesce(sum(post_count), 0) AS posts_sum,
             coalesce(sum(views_total), 0)::bigint AS views_sum,
             max(metric_at) AS metric_at,
             max(imported_at) AS posted_imported_at
        FROM {s}.catalog_posted) p"""
# queries-rank.ts:106: every kind, not only theaters, and no off_on filter; control_totals_gate keeps THEATER_BASES.
_KINDS = "SELECT kind, count(DISTINCT row_key) AS n FROM {s}.catalog_signals GROUP BY kind ORDER BY kind"

_FRESHNESS, _RS_COUNTS, _CONTROL = ("meta", "freshness"), ("meta", "rsCounts"), ("meta", "control")
# manifest key -> _TOTALS column
_FRESHNESS_COLUMNS = (("rows", "catalog_rows_n"), ("withSignal", "with_signal"), ("signals", "signals"), ("posted", "posted"))
_FRESHNESS_RS = (("rsCanonical", "rs_all"), ("rsCandidates", "rs_candidates"))
_RS_COUNT_COLUMNS = MappingProxyType(
    {
        "all": "rs_all",
        "cand": "cand",
        "growthD1": "growth_d1",
        "growthD7": "growth_d7",
        "growthDp1": "growth_dp1",
        "growthDp7": "growth_dp7",
        "pc": "pc",
        "clk": "clk",
        "gsc": "gsc",
        "bill": "bill",
        "ledger": "ledger",
    }
)
_POSTED_STATS = (("total", "posted"), ("pubCount", "pub"), ("postsSum", "posts_sum"), ("viewsSum", "views_sum"), ("accountCount", "accounts"))
_POSTED_STATES = (("pub", "pub"), ("sched", "sched"), ("none", "unposted"), ("nomatch", "nomatch"))
_LEDGER = (("rows", "ledger"), ("orders", "ledger_orders"))


@dataclass(frozen=True, slots=True)
class ControlTotals:
    """G7's recount on the version: _TOTALS' columns by name, and each signal kind's distinct row_key count."""

    values: Mapping[str, object]
    kinds: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _Total:
    """One item compared: a count, a moment (by instant) or a text (metricAt, as a string)."""

    path: tuple[str, ...]
    kind: str
    value: object
    zero_if_missing: bool = False


async def control_totals(conn, schema_name: str, *, timeout: float = STATEMENT_TIMEOUT) -> ControlTotals:
    schema = check_schema_name(schema_name)
    row = await read(conn, CONTROL_TOTALS, "fetchrow", _TOTALS.format(s=schema), timeout=timeout)
    kinds = await read(conn, CONTROL_TOTALS, "fetch", _KINDS.format(s=schema), timeout=timeout)
    return ControlTotals(MappingProxyType(dict(row)), MappingProxyType({kind["kind"]: kind["n"] for kind in kinds}))


def signal_kinds(manifest: Mapping) -> tuple[str, ...]:
    """BASES: the keys of manifest.meta.rules.basisLabels, a full Record<Basis, ...> (request.ts:114, :121)."""
    labels = value_at(manifest, ("meta", "rules", "basisLabels"))
    if not isinstance(labels, Mapping):
        raise ValueError("manifest 缺少 meta.rules.basisLabels，无法得出信号种类")
    return tuple(kind for kind in labels if isinstance(kind, str))


def theater_kinds(manifest: Mapping) -> tuple[str, ...]:
    """THEATER_BASES: BASES less the three ReelShort conditions (request.ts:43, :110-114); nothing written in here."""
    return tuple(kind for kind in signal_kinds(manifest) if kind not in RS_FLAG_KINDS)


def _counted(totals: ControlTotals) -> Iterator[tuple[tuple[str, ...], int]]:
    values = totals.values
    yield from (((*_FRESHNESS, key), values[column]) for key, column in (*_FRESHNESS_COLUMNS, *_FRESHNESS_RS))
    yield from (((*_RS_COUNTS, key), values[column]) for key, column in _RS_COUNT_COLUMNS.items())
    yield from (((*_CONTROL, "rankCounts", rank), values[_RS_COUNT_COLUMNS[of]]) for rank, of in RS_RANK_COUNTS.items())
    yield from (((*_CONTROL, "postedStats", key), values[column]) for key, column in _POSTED_STATS)
    yield from (((*_CONTROL, "postedStates", key), values[column]) for key, column in _POSTED_STATES)
    yield from (((*_CONTROL, "ledger", key), values[column]) for key, column in _LEDGER)


def _totals(totals: ControlTotals, theaters: Iterable[str]) -> tuple[_Total, ...]:
    """Every item G7 compares. Only theaters with signals have a rankCounts key (queries-rank.ts:139-140): missing is 0."""
    counts = tuple(_Total(path, "count", value) for path, value in _counted(totals))
    ranks = tuple(_Total((*_CONTROL, "rankCounts", kind), "count", totals.kinds.get(kind, 0), zero_if_missing=True) for kind in theaters)
    others = (
        _Total((*_FRESHNESS, "importedAt"), "moment", totals.values["imported_at"]),
        _Total((*_CONTROL, "postedStats", "importedAt"), "moment", totals.values["posted_imported_at"]),
        _Total((*_CONTROL, "postedStats", "metricAt"), "text", totals.values["metric_at"]),
    )
    return (*counts, *ranks, *others)


def _same_moment(claimed, value) -> bool:
    if claimed is None or value is None:
        return claimed is None and value is None
    if not isinstance(claimed, str):
        return False
    try:
        return ts_datetime(claimed) == value
    except ValueError:
        return False


def _agrees(total: _Total, claimed) -> bool:
    if claimed is MISSING:
        return total.zero_if_missing and total.value == 0
    if total.kind == "count":
        return is_count(claimed) and claimed == total.value
    if total.kind == "moment":
        return _same_moment(claimed, total.value)
    return (claimed is None or isinstance(claimed, str)) and claimed == total.value


def _off_detail(off: Sequence[tuple[_Total, object]]) -> dict:
    paths = {dotted(total.path): 1 for total, _ in off}
    counts = {dotted(total.path): {"manifest": count_or_none(claimed), "mirror": total.value} for total, claimed in off if total.kind == "count"}
    return {"paths": paths, **({"counts": counts} if counts else {}), "total": len(off)}


def _unchecked_ranks(manifest: Mapping, theaters: Iterable[str]) -> list[str]:
    """rankCounts keys no RealShort rank this port knows of: listed, not failed (a new rank is RealShort's to add)."""
    ranks = value_at(manifest, (*_CONTROL, "rankCounts"))
    known = {*theaters, *RS_RANK_COUNTS}
    return sorted(dotted((*_CONTROL, "rankCounts", key)) for key in ranks if key not in known) if isinstance(ranks, Mapping) else []


def control_totals_gate(totals: ControlTotals, manifest: Mapping) -> GateResult:
    """G7: every control total the manifest carries equals its recount on the version (freshness.rsSyncedAt is not one:
    it is every dramas row's, the version holds canonical ones; facets are P4-3's). Consequence mirror."""
    theaters = theater_kinds(manifest)
    compared = [(total, value_at(manifest, total.path)) for total in _totals(totals, theaters)]
    off = [(total, claimed) for total, claimed in compared if not _agrees(total, claimed)]
    unchecked = _unchecked_ranks(manifest, theaters)
    detail = {**(_off_detail(off) if off else {}), **({"unchecked": unchecked} if unchecked else {})}
    return GateResult(CONTROL_TOTALS, not off, detail)
