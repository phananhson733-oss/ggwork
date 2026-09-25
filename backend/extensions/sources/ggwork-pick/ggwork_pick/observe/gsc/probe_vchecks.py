"""Vh and Vd of the GSC probe's P4: the two filter requests, each compared with the unfiltered detail the way TR-23a
will compare them (plan TR-07, D25, D26; section 2 premise 3; design 5.2, 5.6).

The filter side is the request with the seed's page set regex: Vh is [hour,country] over yesterday, Vd is
[date,country] over the 14 PT days, with all and with final. The detail side is never filtered on the page: for Vh it
is yesterday's C from P3, for Vd it is [date,page,country]/all fetched one PT day at a time as D is (yesterday and the
two days before), and the seed's rows are picked out locally with PageSet.contains. A detail request filtered with the
same regex would only compare the filter with itself, and would hide exactly what counterexample 22 is about: a page
the unfiltered detail leaves out while the filter request still counts it.

The comparison is premise 3's, through coverage.detail_value, filter_value and consistent with the parameters of
params_of(GSC_RULES_VERSION): one window per country (and ALL), yesterday's whole PT day for Vh and the compared PT days
for Vd. The cells per hour (Vh) or per PT day (Vd) and country are an auxiliary table. A detail that came back full is
flagged: its sums are lower bounds with rows cut, so a disagreement there says less. A disagreement is data, never a
failure of the probe.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from ggwork_pick.observe.gsc import coverage
from ggwork_pick.observe.gsc.cutoff import Window, pt_day_bounds, utc
from ggwork_pick.observe.gsc.pageset import PageSet
from ggwork_pick.observe.gsc.params import GscRulesParams, params_of
from ggwork_pick.observe.gsc.probe import Answer, ProbeContext, Table, ask, filters, sum_by
from ggwork_pick.observe.gsc.probe_checks import pt_hour_text
from ggwork_pick.observe.gsc.query import GscQuery, GscRow, format_day, parse_hour
from ggwork_pick.observe.versions import GSC_RULES_VERSION

VD_DAYS = 14
VD_DETAIL_DAYS = 3  # yesterday and the two PT days before it, one [date,page,country] request each
SHOWN_ROWS = 10
NO_ROW = "无行"
Carry = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Part:
    """One part of P4: its tables, its values, a request without a clear answer (the item is then undecided), a
    refusal that decides against the design's request shape, a refusal the design has a fallback for, and what the
    answers showed that decides against the D25 regex (semantics only)."""

    tables: tuple[Table, ...]
    backfill: Mapping[str, object]
    broken: Answer | None = None
    refused: str | None = None
    fallback: str | None = None
    problems: tuple[str, ...] = ()


def _params() -> GscRulesParams:
    return params_of(GSC_RULES_VERSION)


# ---- rows as coverage reads them ---------------------------------------------------------------------------------------


def _hourly_detail(rows: Iterable[GscRow]) -> tuple[coverage.GscRow, ...]:
    """[hour,page,country] rows."""
    return tuple(coverage.GscRow(row.keys[2].upper(), row.impressions, row.clicks, page=row.keys[1], hour=utc(parse_hour(row.keys[0]))) for row in rows)


def _hourly_filter(rows: Iterable[GscRow]) -> tuple[coverage.GscRow, ...]:
    """[hour,country] rows."""
    return tuple(coverage.GscRow(row.keys[1].upper(), row.impressions, row.clicks, hour=utc(parse_hour(row.keys[0]))) for row in rows)


def _daily_detail(rows: Iterable[GscRow]) -> tuple[coverage.GscRow, ...]:
    """[date,page,country] rows."""
    return tuple(coverage.GscRow(row.keys[2].upper(), row.impressions, row.clicks, page=row.keys[1], pt_date=date.fromisoformat(row.keys[0])) for row in rows)


def _daily_filter(rows: Iterable[GscRow]) -> tuple[coverage.GscRow, ...]:
    """[date,country] rows."""
    return tuple(coverage.GscRow(row.keys[1].upper(), row.impressions, row.clicks, pt_date=date.fromisoformat(row.keys[0])) for row in rows)


# ---- the comparison -----------------------------------------------------------------------------------------------------


def _shown(value: coverage.WindowValue) -> str:
    return NO_ROW if value.value is None else str(value.value)


def _judge(flt, det, pset: PageSet, window: Window, country: str) -> tuple[str, coverage.WindowValue, coverage.WindowValue, bool]:
    x_flt, x_det = coverage.filter_value(flt, window, country), coverage.detail_value(det, pset, window, country)
    return country, x_flt, x_det, coverage.consistent(x_flt, x_det, params=_params())


def compare_windows(flt, det, pset: PageSet, window: Window, title: str) -> tuple[int, int, Table]:
    """Premise 3 per country and ALL over one window: how many were compared, how many agree, and a table of ALL and
    of the countries that disagree."""
    mine = tuple(row for row in det if row.page is not None and pset.contains(row.page))
    countries = (*sorted({row.country for row in (*flt, *mine)}), coverage.ALL)
    judged = tuple(_judge(flt, det, pset, window, country) for country in countries)
    shown = [item for item in judged if item[0] == coverage.ALL or not item[3]][: SHOWN_ROWS + 1]
    rows = tuple((country, _shown(x_flt), _shown(x_det), "是" if agrees else "否") for country, x_flt, x_det, agrees in shown)
    table = Table(title, ("国家", "过滤请求", "明细合计", "在容差内"), rows)
    return len(judged), sum(1 for item in judged if item[3]), table


def compare_cells(flt, det, pset: PageSet, key, title: str, labels: tuple[str, str]) -> tuple[int, int, Table]:
    """The same rule cell by cell (auxiliary): a cell is key(row), such as (hour, country); a missing side is no row."""
    flt_cells = sum_by(flt, key, lambda row: row.impressions)
    det_cells = sum_by((row for row in det if row.page is not None and pset.contains(row.page)), key, lambda row: row.impressions)
    cells = sorted(flt_cells.keys() | det_cells.keys())

    def value(cells_of: dict, cell) -> coverage.WindowValue:
        return coverage.WindowValue.of((cells_of[cell],) if cell in cells_of else ())

    agree = [cell for cell in cells if coverage.consistent(value(flt_cells, cell), value(det_cells, cell), params=_params())]
    rows = tuple((*map(str, cell), str(flt_cells.get(cell, NO_ROW)), str(det_cells.get(cell, NO_ROW))) for cell in cells if cell not in agree)
    return len(cells), len(agree), Table(title, (*labels, "过滤请求", "明细合计"), rows[:SHOWN_ROWS])


# ---- Vh ---------------------------------------------------------------------------------------------------------------


def _day_window(days: tuple[date, ...], kind: str) -> Window:
    first, last = min(days), max(days)
    return Window("w0", kind, pt_day_bounds(first)[0], pt_day_bounds(last)[1], () if kind == "24h" else tuple(sorted(days)))


async def vh(ctx: ProbeContext, pset: PageSet, regex: str, carry: Carry) -> Part:
    """[hour,country] with the regex over yesterday, against yesterday's unfiltered C from P3."""
    answer = await ask(
        ctx, GscQuery(ctx.yesterday, ctx.yesterday, ("hour", "country"), "hourly_all", dimension_filter_groups=filters("page", "includingRegex", regex))
    )
    backfill = {"vh_supported": answer.ok, "vh_seed_book_id": pset.book_ids[0], "vh_windows": None, "vh_windows_agree": None,
                "vh_cells": None, "vh_cells_agree": None, "vh_detail_truncated": None}  # fmt: skip
    if not answer.ok:
        refused = "按页面过滤的 [hour,country]（Vh）被拒" if answer.error.kind == "bad_request" else None
        return Part((), {**backfill, "vh_supported": False if refused else None}, None if refused else answer, refused)
    detail = (carry.get("c_responses") or {}).get(ctx.yesterday)
    if detail is None or carry.get("c_shape") != "hour,page,country":
        return Part((), backfill)
    flt, det = _hourly_filter(answer.response.rows), _hourly_detail(detail.rows)
    title = "Vh 与 C 明细（未过滤）按国家的窗口合计（昨天整个 PT 日，曝光）"
    windows, agree, table = compare_windows(flt, det, pset, _day_window((ctx.yesterday,), "24h"), title)
    cells, cells_agree, aux = compare_cells(
        flt, det, pset, lambda row: (pt_hour_text(row.hour), row.country), "Vh 与 C 明细不一致的小时格（辅助）", ("PT 小时", "国家")
    )
    counts = {"vh_windows": windows, "vh_windows_agree": agree, "vh_cells": cells, "vh_cells_agree": cells_agree, "vh_detail_truncated": detail.truncated}
    return Part((table, aux), {**backfill, **counts})


# ---- Vd ---------------------------------------------------------------------------------------------------------------


async def _vd_requests(ctx: ProbeContext, regex: str) -> tuple[Answer, Answer, tuple[Answer, ...]]:
    days, groups = (ctx.yesterday - timedelta(days=VD_DAYS - 1), ctx.yesterday), filters("page", "includingRegex", regex)
    vd_all = await ask(ctx, GscQuery(*days, ("date", "country"), "all", dimension_filter_groups=groups))
    vd_final = await ask(ctx, GscQuery(*days, ("date", "country"), "final", dimension_filter_groups=groups))
    detail_days = tuple(ctx.yesterday - timedelta(days=back) for back in range(VD_DETAIL_DAYS))
    details = tuple([await ask(ctx, GscQuery(day, day, ("date", "page", "country"), "all")) for day in detail_days])
    return vd_all, vd_final, details


async def vd(ctx: ProbeContext, pset: PageSet, regex: str) -> Part:
    """[date,country] with the regex over 14 PT days (all and final), against [date,page,country]/all unfiltered."""
    vd_all, vd_final, details = await _vd_requests(ctx, regex)
    detail_days = tuple(answer.query.start_date for answer in details)
    backfill = {"vd_supported": {"all": vd_all.ok, "final": vd_final.ok}, "vd_windows": None, "vd_windows_agree": None, "vd_cells": None,
                "vd_cells_agree": None, "vd_detail_truncated": None, "vd_detail_days": [format_day(day) for day in detail_days]}  # fmt: skip
    refused = "按页面过滤的 [date,country]（Vd，all）被拒" if not vd_all.ok and vd_all.error.kind == "bad_request" else None
    fallback = "按页面过滤的 [date,country] 用 final 被拒：Vd 只能用 all" if not vd_final.ok and vd_final.error.kind == "bad_request" else None
    unclear = (*(answer for answer in (vd_all, vd_final) if not answer.ok and answer.error.kind != "bad_request"), *(a for a in details if not a.ok))
    broken = next(iter(unclear), None)
    if not (vd_all.ok and all(answer.ok for answer in details)):
        return Part((), backfill, broken, refused, fallback)
    compared = set(detail_days)
    flt = tuple(row for row in _daily_filter(vd_all.response.rows) if row.pt_date in compared)
    det = _daily_detail(row for answer in details for row in answer.response.rows)
    title = "Vd 与 [date,page,country] 明细（未过滤）按国家的窗口合计（all，曝光）"
    windows, agree, table = compare_windows(flt, det, pset, _day_window(detail_days, "7d"), title)
    cells, cells_agree, aux = compare_cells(
        flt, det, pset, lambda row: (format_day(row.pt_date), row.country), "Vd 与明细不一致的日格（辅助）", ("PT 日", "国家")
    )
    counts = {"vd_windows": windows, "vd_windows_agree": agree, "vd_cells": cells, "vd_cells_agree": cells_agree,
              "vd_detail_truncated": any(answer.response.truncated for answer in details)}  # fmt: skip
    return Part((table, aux), {**backfill, **counts}, broken, refused, fallback)


def shape_texts(backfill: Mapping[str, object]) -> tuple[str, ...]:
    """What Vh and Vd showed: usable, and how many windows (and cells) agree with the unfiltered detail."""
    shapes = (("Vh", "vh", backfill["vh_supported"], "小时"), ("Vd", "vd", backfill["vd_supported"]["all"], "日"))
    return tuple(text for text in (_shape_text(backfill, *shape) for shape in shapes) if text)


def _shape_text(backfill: Mapping[str, object], name: str, key: str, usable: bool | None, unit: str) -> str | None:
    windows, agree = backfill[f"{key}_windows"], backfill[f"{key}_windows_agree"]
    if windows is None:
        return f"{name} 可用（没有可比的明细）" if usable else None
    tail = f"，{windows - agree} 个窗口不一致" if agree < windows else ""
    cut = "；明细满额，合计是被截断的下界，比较只作参考" if backfill[f"{key}_detail_truncated"] else ""
    cells = f"（逐{unit}的格 {backfill[f'{key}_cells_agree']}/{backfill[f'{key}_cells']} 在容差内）"
    return f"{name} 可用，与未过滤明细按国家窗口比较 {agree}/{windows} 个窗口在前提 3 的容差内{tail}{cells}{cut}"
