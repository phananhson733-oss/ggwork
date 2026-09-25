"""Stage 0's report (plan TR-05, section 8; design 4.9, 4.11; D39).

Built from the day runner's files only (results, meta, the task lists), never from the network. With one day in, it is
an interim report: every conclusion that needs both sessions is marked "待第二天" and the route is tentative. With both
days in, it settles gate A per granularity, gate B, the granularity, N, the lag, the section 8 route and the full text
of trend-rules-v1, and adds D39's data contract change sheet when D or H+D is chosen.

Premise 1 holds in every table: a unit that failed or never sent reads "未观测到" with its status, never a count.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from ggwork_pick.observe.trends.stage0 import DAYS, Controls, DayPlan
from ggwork_pick.observe.trends.stage0_checks import DESIGN_LAG_HOURS, InterfaceSummary, LagSummary, interface_summary, lag_summary
from ggwork_pick.observe.trends.stage0_metrics import (
    A_TIER_DRAMAS,
    DAY_NAMES,
    DEFAULT_N,
    MIN_POSITIVES_OBSERVED,
    N_CANDIDATES,
    GateA,
    GateB,
    Route,
    Series,
    ShapeComparison,
    choose_granularity,
    gate_a,
    gate_b,
    parse_manual_csv,
    route_for,
    series_of,
    shape_compare,
)
from ggwork_pick.observe.trends.stage0_run import Stage0Paths, load_meta, load_results, write_private
from ggwork_pick.observe.trends.stage0_text import render_report

DEFAULT_UTC_OFFSET_MINUTES = 480  # the browser that exports U5 runs on UTC+8, like the GSC export
GEO_NAMES = MappingProxyType(
    {
        "united states": "US",
        "美国": "US",
        "bulgaria": "BG",
        "保加利亚": "BG",
        "germany": "DE",
        "德国": "DE",
        "france": "FR",
        "法国": "FR",
        "italy": "IT",
        "意大利": "IT",
        "poland": "PL",
        "波兰": "PL",
        "romania": "RO",
        "罗马尼亚": "RO",
        "mexico": "MX",
        "墨西哥": "MX",
        "taiwan": "TW",
        "台湾": "TW",
        "brazil": "BR",
        "巴西": "BR",
        "worldwide": "WW",
        "全球": "WW",
    }
)


# ---- inputs --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DayData:
    day: int
    results: tuple[Mapping, ...]  # the latest line of each unit
    meta: Mapping | None
    planned: tuple[str, ...] | None  # control ids in the day's task list; None without one

    @property
    def ran(self) -> bool:
        return bool(self.results) or bool(self.meta and self.meta.get("sessions"))


def _planned(paths: Stage0Paths, day: int) -> tuple[str, ...] | None:
    path = paths.plan_file(day)
    if not path.exists():
        return None
    plan = DayPlan.from_document(json.loads(path.read_text(encoding="utf-8")))
    return tuple(dict.fromkeys(unit.control for unit in plan.units))


def load_days(paths: Stage0Paths) -> tuple[DayData, ...]:
    """Both days as the runner left them; a day that has not run has no results and no sessions."""
    return tuple(DayData(day, tuple(load_results(paths.run_dir(day)).values()), load_meta(paths.run_dir(day)), _planned(paths, day)) for day in DAYS)


# ---- the manual comparison -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ManualRow:
    name: str
    term: str | None
    geo: str | None
    comparison: ShapeComparison | None
    problem: str | None


def _matching(series: Sequence[Series], term: str, geo: str | None, granularity: str) -> Series | None:
    found = [
        s
        for s in series
        if s.observed and s.repeat_of is None and s.granularity == granularity and s.term.casefold() == term.casefold() and (geo is None or s.geo == geo)
    ]
    return found[-1] if found else None


def manual_row(name: str, text: str, series: Sequence[Series], *, utc_offset_minutes: int) -> ManualRow:
    try:
        manual = parse_manual_csv(text)
    except ValueError as error:
        return ManualRow(name, None, None, None, str(error))
    geo = GEO_NAMES.get(manual.geo_label.strip().casefold())
    scraped = _matching(series, manual.term, geo, manual.granularity)
    if scraped is None:
        return ManualRow(name, manual.term, geo or manual.geo_label, None, "找不到同词、同地区、同颗粒度的抓取序列")
    return ManualRow(name, manual.term, scraped.geo, shape_compare(manual, scraped, utc_offset_minutes=utc_offset_minutes), None)


# ---- the report ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    generated_at: datetime
    days: tuple[DayData, ...]
    controls: Controls
    series: tuple[Series, ...]
    n: int
    gate_h: GateA
    gate_d: GateA
    gate_b: GateB
    granularity: str | None
    route: Route | None
    sensitivity: tuple[tuple[int, GateA], ...]
    lag: LagSummary  # hourly series
    lag_daily: LagSummary
    interface: InterfaceSummary
    manual: tuple[ManualRow, ...]
    utc_offset_minutes: int

    @property
    def interim(self) -> bool:
        return not all(day.ran for day in self.days)

    @property
    def missing_days(self) -> tuple[str, ...]:
        return tuple(DAY_NAMES[day.day] for day in self.days if not day.ran)

    @property
    def lines(self) -> tuple[Mapping, ...]:
        return tuple(line for day in self.days for line in day.results)

    @property
    def gate_a_passed(self) -> bool | None:
        if self.granularity is not None:
            return True
        return False if self.gate_h.passed is False and self.gate_d.passed is False else None

    @property
    def lag_hours(self) -> int:
        return self.lag.suggested if self.lag.suggested is not None else DESIGN_LAG_HOURS

    @property
    def lag_days(self) -> int:
        """Whole days the daily window ends before the UTC day boundary; 0 while the lag stays under a day."""
        return self.lag_daily.suggested // 24 if self.lag_daily.suggested is not None else 0

    def day_of(self, control: str) -> int | None:
        """The day a control runs on: from the task lists, else from its result lines."""
        planned = next((day.day for day in self.days if day.planned is not None and control in day.planned), None)
        seen = next((line["day"] for line in self.lines if line.get("control") == control), None)
        return planned if planned is not None else seen


def settle_granularity(series: Sequence[Series], gate_h: GateA, gate_d: GateA, *, n: int, min_positives: int) -> str | None:
    """choose_granularity, with H+D's halved A tier (design 4.5) re-checked: when half the dramas no longer reach the
    daily judgement floor, D alone is chosen."""
    chosen = choose_granularity(gate_h, gate_d)
    if chosen != "H+D":
        return chosen
    halved = gate_a(series, granularity="D", n=n, a_tier_dramas=A_TIER_DRAMAS // 2, min_positives=min_positives)
    return "H+D" if halved.passed else "D"


def build_report(
    controls: Controls,
    days: Iterable[DayData],
    *,
    generated_at: datetime,
    n: int = DEFAULT_N,
    manual: Sequence[tuple[str, str]] = (),
    utc_offset_minutes: int = DEFAULT_UTC_OFFSET_MINUTES,
) -> Report:
    days = tuple(days)
    lines = tuple(line for day in days for line in day.results)
    series = tuple(s for s in map(series_of, lines) if s is not None)
    floor = MIN_POSITIVES_OBSERVED if all(day.ran for day in days) else MIN_POSITIVES_OBSERVED // 2
    gate_h, gate_d = (gate_a(series, granularity=g, n=n, min_positives=floor) for g in ("H", "D"))
    gate_b_result = gate_b(lines, days=tuple(day.day for day in days))
    granularity = settle_granularity(series, gate_h, gate_d, n=n, min_positives=floor)
    report = Report(
        generated_at=generated_at,
        days=days,
        controls=controls,
        series=series,
        n=n,
        gate_h=gate_h,
        gate_d=gate_d,
        gate_b=gate_b_result,
        granularity=granularity,
        route=None,
        sensitivity=tuple((c, gate_a(series, granularity="D", n=c, min_positives=floor)) for c in sorted({*N_CANDIDATES, n})),
        lag=lag_summary(series),
        lag_daily=lag_summary(series, granularity="D"),
        interface=interface_summary(lines),
        manual=tuple(manual_row(name, text, series, utc_offset_minutes=utc_offset_minutes) for name, text in manual),
        utc_offset_minutes=utc_offset_minutes,
    )
    return Report(**{**report.__dict__, "route": route_for(report.gate_a_passed, gate_b_result.passed)})


def render(report: Report) -> str:
    return render_report(report)


def write_report(paths: Stage0Paths, report: Report) -> Path:
    """The report as markdown under the artifacts root (mode 600), named by the day it was generated."""
    path = paths.report_file(report.generated_at.date(), interim=report.interim)
    write_private(path, render(report))
    return path
