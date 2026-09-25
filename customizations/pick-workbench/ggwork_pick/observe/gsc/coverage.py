"""Window sums, the per-identity consistency check and the site layer of admission (plan TR-09, D26, D27; design 5.6).

Two lower bounds per identity x country x window x metric (plan section 2, premise 3):

- X_det, the detail sum: rows of C (24 hours) or D/E (7 days) whose page is in P, country c (ALL: every country, GSC's
  unknown one included) and time in W;
- X_flt, the filter sum: rows of the Vh or Vd response for country c and time in W.

A window without a row is "no row" (None, row_count 0), never 0 (premise 1). The two agree when
|X_flt - X_det| <= max(10% x max(X_flt, X_det), 3), in integers, the larger value as the denominator; a side with no row
counts as 0 in the difference and stays "no row" in the record. When they agree the admitted value is the larger one.
Agreement is an admission rule only (premise 2): it proves neither completeness nor independence, and the result says
only admitted and, when not, why. A filter result that failed, was truncated or overflowed its regex, a Vh from another
round, a Vd no longer valid (D26), or a window or dataState that differs from the detail's is never compared.

The site layer compares a site total with the detail sum. 24 hours: A' against C per window, summed over the window's
hours; without A', A''a against C per PT day. 7 days: each of the 14 PT days against A''f when that day's detail is an E
slice (final) and A''a when it is a D slice (all) (D27). A gap of at most tau (5%) is usable, more is gap_exceeded, a
missing total or detail is unverifiable.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Literal, get_args

from ggwork_pick.observe.contract import DataState, SiteAdmission, TotalsStatus, VcheckKind, VcheckStatus
from ggwork_pick.observe.gsc.cutoff import HOUR, Window, window_days
from ggwork_pick.observe.gsc.pageset import PageSet

ALL = "ALL"
Metric = Literal["impressions", "clicks"]
METRICS = get_args(Metric)
ADMISSION_TEXT = "两份下界一致（准入）"
ComparisonReason = Literal[
    "filter_missing", "filter_failed", "filter_truncated", "regex_overflow", "vh_not_this_round", "vd_invalid", "window_mismatch",
    "datastate_mismatch", "detail_gap",
]  # fmt: skip
COMPARISON_REASONS = get_args(ComparisonReason)
VdInvalidReason = Literal["window_changed", "slice_version_changed", "datastate_changed"]
SiteSource = Literal["a_prime", "a2_all", "a2_by_state", "none"]
DATA_STATE_OF_DATASET = MappingProxyType({"C": "hourly_all", "D": "all", "E": "final"})  # D27: a 7-day day follows its active slice
CONSISTENCY_PERCENT = 10
CONSISTENCY_FLOOR = 3
SITE_GAP_PERCENT = 5


@dataclass(frozen=True, slots=True)
class GscRow:
    """One row of a GSC response as the rules read it: an hourly row has hour, a daily row pt_date; filter rows have no
    page. country is alpha-3 upper case (GSC's unknown country included); position is GSC's average position."""

    country: str
    impressions: int
    clicks: int
    page: str | None = None
    hour: datetime | None = None
    pt_date: date | None = None
    position: float | None = None

    def metric(self, name: Metric) -> int:
        return self.impressions if name == "impressions" else self.clicks


@dataclass(frozen=True, slots=True)
class WindowValue:
    """A window's sum: value None exactly when the response had no row (not observed), never 0."""

    value: int | None
    row_count: int

    def __post_init__(self):
        valid = self.row_count >= 0 and (self.value is None) == (self.row_count == 0) and (self.value is None or self.value >= 0)
        if not valid:
            raise ValueError(f"a window with {self.row_count} rows cannot sum to {self.value!r}")

    @classmethod
    def of(cls, values: Iterable[int]) -> "WindowValue":
        counted = tuple(values)
        return cls(sum(counted) if counted else None, len(counted))

    @property
    def observed(self) -> bool:
        return self.row_count > 0

    @property
    def as_int(self) -> int:
        """For the difference only: no row counts as 0."""
        return self.value or 0


def _check_metric(metric: str) -> None:
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")


def _in_window(row: GscRow, window: Window) -> bool:
    if window.kind == "24h":
        return row.hour is not None and window.covers_hour(row.hour)
    return row.pt_date is not None and window.covers_day(row.pt_date)


def _in_scope(row: GscRow, country: str) -> bool:
    return country == ALL or row.country == country


def detail_value(rows: Iterable[GscRow], pages: PageSet, window: Window, country: str, metric: Metric = "impressions") -> WindowValue:
    """X_det: detail rows with page in P, the country (or ALL) and time in the window."""
    _check_metric(metric)
    return WindowValue.of(r.metric(metric) for r in rows if r.page is not None and pages.contains(r.page) and _in_scope(r, country) and _in_window(r, window))


def filter_value(rows: Iterable[GscRow], window: Window, country: str, metric: Metric = "impressions") -> WindowValue:
    """X_flt: a Vh or Vd response's rows (already filtered to P by GSC) for the country (or ALL) and the window."""
    _check_metric(metric)
    return WindowValue.of(r.metric(metric) for r in rows if _in_scope(r, country) and _in_window(r, window))


def detail_position(rows: Iterable[GscRow], pages: PageSet, window: Window, country: str) -> float | None:
    """The impression-weighted average position of the identity's detail rows; None when nothing carries a position."""
    weighted = tuple(
        (r.position, r.impressions)
        for r in rows
        if r.position is not None and r.impressions > 0 and r.page is not None and pages.contains(r.page) and _in_scope(r, country) and _in_window(r, window)
    )
    total = sum(weight for _, weight in weighted)
    return sum(position * weight for position, weight in weighted) / total if total else None


def consistent(x_flt: WindowValue, x_det: WindowValue, *, percent: int = CONSISTENCY_PERCENT, floor: int = CONSISTENCY_FLOOR) -> bool:
    """|X_flt - X_det| <= max(percent% x max(X_flt, X_det), floor), in integers, no rounding."""
    larger = max(x_flt.as_int, x_det.as_int)
    difference = abs(x_flt.as_int - x_det.as_int)
    return difference <= floor or difference * 100 <= percent * larger


# ---- the per-identity layer ------------------------------------------------------------------------------------------


def _check_states(window: Window, states: tuple[str, ...]) -> None:
    expected = 1 if window.kind == "24h" else len(window.days)
    if len(states) != expected or any(state not in get_args(DataState) for state in states):
        raise ValueError(f"a {window.kind} window takes {expected} dataState(s), got {states!r}")
    hourly = tuple(state == "hourly_all" for state in states)
    if any(hourly) != (window.kind == "24h"):
        raise ValueError("24-hour windows use hourly_all and 7-day windows use all or final")


@dataclass(frozen=True, slots=True)
class DetailSide:
    """The detail sum of one window; data_states: ("hourly_all",) for 24 hours, one per PT day for 7 days."""

    window: Window
    data_states: tuple[DataState, ...]
    value: WindowValue

    def __post_init__(self):
        _check_states(self.window, self.data_states)


@dataclass(frozen=True, slots=True)
class FilterSide:
    """A Vh or Vd result for one window. status combines its chunks (combine_chunk_statuses); current is vh_current for
    Vh and vd_valid for Vd."""

    kind: VcheckKind
    window: Window
    data_states: tuple[DataState, ...]
    status: VcheckStatus
    current: bool
    value: WindowValue

    def __post_init__(self):
        _check_states(self.window, self.data_states)
        if (self.kind == "vh") != (self.window.kind == "24h"):
            raise ValueError("Vh checks 24-hour windows and Vd 7-day windows")


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two lower bounds of one window (the detail's). admitted: they agree (两份下界一致（准入）); value is then the larger,
    None when neither side had a row. reason says why a comparison was not admitted."""

    window: Window
    x_det: WindowValue
    x_flt: WindowValue | None
    admitted: bool
    value: int | None
    reason: ComparisonReason | None


_STATUS_REASONS: Mapping[str, ComparisonReason] = MappingProxyType(
    {"failed": "filter_failed", "regex_overflow": "regex_overflow", "truncated": "filter_truncated"}
)


def _not_comparable(detail: DetailSide, filtered: FilterSide | None) -> ComparisonReason | None:
    if filtered is None:
        return "filter_missing"
    if filtered.status in _STATUS_REASONS:
        return _STATUS_REASONS[filtered.status]
    if not filtered.current:
        return "vh_not_this_round" if filtered.kind == "vh" else "vd_invalid"
    if (filtered.window.kind, filtered.window.start, filtered.window.end, filtered.window.days) != (
        detail.window.kind, detail.window.start, detail.window.end, detail.window.days,
    ):  # fmt: skip
        return "window_mismatch"
    if filtered.data_states != detail.data_states:
        return "datastate_mismatch"
    return None


def per_identity_consistency(
    detail: DetailSide, filtered: FilterSide | None, *, percent: int = CONSISTENCY_PERCENT, floor: int = CONSISTENCY_FLOOR
) -> Comparison:
    """The per-identity layer for one identity x country x window x metric (design 5.6, premise 3)."""
    x_flt = None if filtered is None else filtered.value
    reason = _not_comparable(detail, filtered)
    if reason is None and not consistent(x_flt, detail.value, percent=percent, floor=floor):
        reason = "detail_gap"
    if reason is not None:
        return Comparison(detail.window, detail.value, x_flt, False, None, reason)
    both_empty = not detail.value.observed and not x_flt.observed
    return Comparison(detail.window, detail.value, x_flt, True, None if both_empty else max(detail.value.as_int, x_flt.as_int), None)


def vh_current(fetched_round_id: str, round_id: str) -> bool:
    """Vh is fetched every round and never reused (D26)."""
    return fetched_round_id == round_id


@dataclass(frozen=True, slots=True)
class VdDay:
    """One of the 14 PT days a Vd result compared: the D or E slice version active that day and its dataState."""

    pt_date: date
    version_id: int
    data_state: DataState


def vd_validity(previous: Sequence[VdDay], current: Sequence[VdDay]) -> VdInvalidReason | None:
    """D26: a Vd result stays valid while its 14 PT days, their active slice versions and their dataStates are unchanged."""
    before, now = sorted(previous, key=lambda d: d.pt_date), sorted(current, key=lambda d: d.pt_date)
    if [d.pt_date for d in before] != [d.pt_date for d in now]:
        return "window_changed"
    if [d.version_id for d in before] != [d.version_id for d in now]:
        return "slice_version_changed"
    if [d.data_state for d in before] != [d.data_state for d in now]:
        return "datastate_changed"
    return None


def vd_valid(previous: Sequence[VdDay], current: Sequence[VdDay]) -> bool:
    return vd_validity(previous, current) is None


_CHUNK_ORDER: tuple[VcheckStatus, ...] = ("regex_overflow", "failed", "truncated")


def combine_chunk_statuses(statuses: Sequence[VcheckStatus]) -> VcheckStatus:
    """One status for a filter request split into regex chunks: any chunk that overflowed, failed or was truncated
    decides it; no chunk at all is a failure (nothing was asked)."""
    if not statuses:
        return "failed"
    return next((status for status in _CHUNK_ORDER if status in statuses), "fetched")


# ---- the site layer ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GapUnit:
    """One compared unit: a window (keyed by its start) or a PT day. None: that total or detail is not available."""

    key: datetime | date
    total: int | None
    detail: int | None


@dataclass(frozen=True, slots=True)
class SiteCheck:
    admission: SiteAdmission
    source: SiteSource
    units: tuple[GapUnit, ...]
    exceeded: tuple[GapUnit, ...]
    missing: tuple[GapUnit, ...]


@dataclass(frozen=True, slots=True)
class HourlyTotals:
    """A' this round: its request status and the total per UTC hour (a missing hour: no row)."""

    status: TotalsStatus
    by_hour: Mapping[datetime, int | None]


@dataclass(frozen=True, slots=True)
class DailyTotals:
    """A''a or A''f this round: its request status and the total per PT day (a missing day: no row)."""

    status: TotalsStatus
    by_day: Mapping[date, int | None]


@dataclass(frozen=True, slots=True)
class DayTotals:
    """One of the 14 PT days of the 7-day windows: the dataState of its active detail slice (None: no slice), the detail
    sum and both daily totals."""

    pt_date: date
    detail_state: DataState | None
    detail: int | None
    a2_all: int | None
    a2_final: int | None


def within_gap(unit: GapUnit, tau_percent: int) -> bool:
    return abs(unit.total - unit.detail) * 100 <= tau_percent * unit.total


def check_units(units: Sequence[GapUnit], source: SiteSource, tau_percent: int = SITE_GAP_PERCENT) -> SiteCheck:
    """Every unit within tau is usable; a unit without its total or detail makes the whole check unverifiable."""
    missing = tuple(unit for unit in units if unit.total is None or unit.detail is None)
    exceeded = tuple(unit for unit in units if unit.total is not None and unit.detail is not None and not within_gap(unit, tau_percent))
    if missing or not units:
        return SiteCheck("unverifiable", source, tuple(units), exceeded, missing)
    return SiteCheck("gap_exceeded" if exceeded else "usable", source, tuple(units), exceeded, missing)


def _fetched(totals: HourlyTotals | DailyTotals | None) -> bool:
    return totals is not None and totals.status == "fetched"


def _sum_known(values: Iterable[int | None]) -> int | None:
    counted = tuple(values)
    return None if any(v is None for v in counted) else sum(counted)


def site_admission_24h(
    windows: Sequence[Window],
    *,
    a_prime: HourlyTotals | None,
    a2_all: DailyTotals | None,
    c_by_hour: Mapping[datetime, int],
    c_by_day: Mapping[date, int],
    tau_percent: int = SITE_GAP_PERCENT,
) -> SiteCheck:
    """The site layer of both 24-hour windows: A' against C per window, or A''a against C per PT day without A'."""
    if _fetched(a_prime):
        units = tuple(_hourly_unit(window, a_prime, c_by_hour) for window in windows)
        return check_units(units, "a_prime", tau_percent)
    if _fetched(a2_all):
        units = tuple(GapUnit(day, a2_all.by_day.get(day), c_by_day.get(day)) for day in window_days(*windows))
        return check_units(units, "a2_all", tau_percent)
    return SiteCheck("unverifiable", "none", (), (), ())


def _hourly_unit(window: Window, a_prime: HourlyTotals, c_by_hour: Mapping[datetime, int]) -> GapUnit:
    hours = tuple(window.start + n * HOUR for n in range(window.span // HOUR))
    return GapUnit(window.start, _sum_known(a_prime.by_hour.get(hour) for hour in hours), sum(c_by_hour.get(hour, 0) for hour in hours))


def site_admission_7d(windows: Sequence[Window], days: Sequence[DayTotals], *, tau_percent: int = SITE_GAP_PERCENT) -> SiteCheck:
    """The site layer of the 7-day windows: each of the 14 PT days against the total of its detail's dataState (D27)."""
    by_day = {day.pt_date: day for day in days}
    units = tuple(_daily_unit(day, by_day.get(day)) for day in window_days(*windows))
    return check_units(units, "a2_by_state", tau_percent)


def _daily_unit(day: date, totals: DayTotals | None) -> GapUnit:
    if totals is None or totals.detail_state is None:
        return GapUnit(day, None, None)
    total = totals.a2_final if totals.detail_state == "final" else totals.a2_all
    return GapUnit(day, total, totals.detail)
