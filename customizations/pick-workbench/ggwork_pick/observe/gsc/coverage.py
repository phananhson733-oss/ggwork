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
round, a Vd no longer valid (D26), or a window or dataState that differs from the detail's is never compared. A result
that is not this round's is reported as such whatever became of it, so that it is asked again (premise 4): a Vh is
this round's only when this round fetched it, and a Vd carries over (vd_current) only when it answered (fetched) and
D26's three conditions still hold; a failed, truncated or overflowed one stands for the round that asked it.

The site layer compares a site total with the detail sum, unit by unit (design 5.6). 24 hours: A' against C hour by
hour over both windows' 48 hours; without A', A''a against C per PT day. 7 days: each of the 14 PT days against A''f
when that day's detail is an E slice (final) and A''a when it is a D slice (all) (D27). A unit whose gap is at most tau
is usable, one more makes the check gap_exceeded, a missing total or detail makes it unverifiable. The percentages
(premise 3's 10% and 3, tau) are the caller's GscRulesParams (params.py), recorded with the result.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Literal, get_args

from ggwork_pick.observe.contract import DataState, Metric, SiteAdmission, TotalsStatus, VcheckKind, VcheckStatus
from ggwork_pick.observe.gsc.cutoff import HOUR, Window, window_days
from ggwork_pick.observe.gsc.pageset import PageSet
from ggwork_pick.observe.gsc.params import GscRulesParams

ALL = "ALL"
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


def consistent(x_flt: WindowValue, x_det: WindowValue, *, params: GscRulesParams) -> bool:
    """|X_flt - X_det| <= max(percent% x max(X_flt, X_det), floor), in integers, no rounding."""
    percent, floor = params.consistency_percent, params.consistency_floor
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
    Vh and vd_current for Vd."""

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
    None when neither side had a row. reason says why a comparison was not admitted. tolerance: the (percent, floor) of
    the gsc-rules parameters it was compared with, which the judgment checks against its own."""

    window: Window
    x_det: WindowValue
    x_flt: WindowValue | None
    admitted: bool
    value: int | None
    reason: ComparisonReason | None
    tolerance: tuple[int, int]


_STATUS_REASONS: Mapping[str, ComparisonReason] = MappingProxyType(
    {"failed": "filter_failed", "regex_overflow": "regex_overflow", "truncated": "filter_truncated"}
)


def _not_comparable(detail: DetailSide, filtered: FilterSide | None) -> ComparisonReason | None:
    if filtered is None:
        return "filter_missing"
    if not filtered.current:  # before its status: another round's failure is asked again, not read as this round's
        return "vh_not_this_round" if filtered.kind == "vh" else "vd_invalid"
    if filtered.status in _STATUS_REASONS:
        return _STATUS_REASONS[filtered.status]
    if (filtered.window.kind, filtered.window.start, filtered.window.end, filtered.window.days) != (
        detail.window.kind, detail.window.start, detail.window.end, detail.window.days,
    ):  # fmt: skip
        return "window_mismatch"
    if filtered.data_states != detail.data_states:
        return "datastate_mismatch"
    return None


def per_identity_consistency(detail: DetailSide, filtered: FilterSide | None, *, params: GscRulesParams) -> Comparison:
    """The per-identity layer for one identity x country x window x metric (design 5.6, premise 3)."""
    x_flt = None if filtered is None else filtered.value
    tolerance = (params.consistency_percent, params.consistency_floor)
    reason = _not_comparable(detail, filtered)
    if reason is None and not consistent(x_flt, detail.value, params=params):
        reason = "detail_gap"
    if reason is not None:
        return Comparison(detail.window, detail.value, x_flt, False, None, reason, tolerance)
    both_empty = not detail.value.observed and not x_flt.observed
    value = None if both_empty else max(detail.value.as_int, x_flt.as_int)
    return Comparison(detail.window, detail.value, x_flt, True, value, None, tolerance)


def vh_current(fetched_round_id: str, round_id: str) -> bool:
    """Vh is fetched every round and never reused (D26)."""
    return fetched_round_id == round_id


@dataclass(frozen=True, slots=True)
class VdDay:
    """One of the 14 PT days a Vd result compared: the D or E slice version active that day and its dataState."""

    pt_date: date
    version_id: int
    data_state: DataState


def vd_validity(previous: Sequence[VdDay], current: Sequence[VdDay], *, expected_days: Sequence[date]) -> VdInvalidReason | None:
    """D26: a Vd result stays valid while its 14 PT days, their active slice versions and their dataStates are unchanged.

    expected_days: this round's 14 PT days (cutoff.window_days of its two 7-day windows). Both sides must list exactly
    those, each once; agreeing with each other is not enough."""
    before, now = sorted(previous, key=lambda d: d.pt_date), sorted(current, key=lambda d: d.pt_date)
    expected = sorted(set(expected_days))
    if len(expected) != len(expected_days) or any([d.pt_date for d in side] != expected for side in (before, now)):
        return "window_changed"
    if [d.version_id for d in before] != [d.version_id for d in now]:
        return "slice_version_changed"
    if [d.data_state for d in before] != [d.data_state for d in now]:
        return "datastate_changed"
    return None


def vd_valid(previous: Sequence[VdDay], current: Sequence[VdDay], *, expected_days: Sequence[date]) -> bool:
    return vd_validity(previous, current, expected_days=expected_days) is None


def vd_current(
    *,
    status: VcheckStatus,
    fetched_round_id: str,
    round_id: str,
    previous: Sequence[VdDay],
    current: Sequence[VdDay],
    expected_days: Sequence[date],
) -> bool:
    """FilterSide.current for a Vd result. This round's own result stands for this round whatever its status; one from an
    earlier round carries over only when it answered (fetched) and D26 still holds: a failed, truncated or overflowed
    result is never carried over, so it is asked again (premise 4) instead of waiting for the 14 days to move."""
    if not vd_valid(previous, current, expected_days=expected_days):
        return False
    return fetched_round_id == round_id or status == "fetched"


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
    """One compared unit: a UTC hour or a PT day. None: that total or detail is not available."""

    key: datetime | date
    total: int | None
    detail: int | None


@dataclass(frozen=True, slots=True)
class SiteCheck:
    """The site layer of one window kind; tau_percent is the gsc-rules parameter it was checked with."""

    admission: SiteAdmission
    source: SiteSource
    units: tuple[GapUnit, ...]
    exceeded: tuple[GapUnit, ...]
    missing: tuple[GapUnit, ...]
    tau_percent: int


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


def check_units(units: Sequence[GapUnit], source: SiteSource, *, params: GscRulesParams) -> SiteCheck:
    """Every unit within tau is usable; a unit without its total or detail makes the whole check unverifiable."""
    tau = params.site_gap_percent
    missing = tuple(unit for unit in units if unit.total is None or unit.detail is None)
    exceeded = tuple(unit for unit in units if unit.total is not None and unit.detail is not None and not within_gap(unit, tau))
    admission = "unverifiable" if missing or not units else ("gap_exceeded" if exceeded else "usable")
    return SiteCheck(admission, source, tuple(units), exceeded, missing, tau)


def _fetched(totals: HourlyTotals | DailyTotals | None) -> bool:
    return totals is not None and totals.status == "fetched"


def site_admission_24h(
    windows: Sequence[Window],
    *,
    a_prime: HourlyTotals | None,
    a2_all: DailyTotals | None,
    c_by_hour: Mapping[datetime, int],
    c_by_day: Mapping[date, int],
    params: GscRulesParams,
) -> SiteCheck:
    """The site layer of both 24-hour windows: A' against C hour by hour (an hour without a C row has a detail of 0, one
    without an A' row is unverifiable), or A''a against C per PT day without A'."""
    if _fetched(a_prime):
        units = tuple(GapUnit(hour, a_prime.by_hour.get(hour), c_by_hour.get(hour, 0)) for hour in _window_hours(windows))
        return check_units(units, "a_prime", params=params)
    if _fetched(a2_all):
        units = tuple(GapUnit(day, a2_all.by_day.get(day), c_by_day.get(day)) for day in window_days(*windows))
        return check_units(units, "a2_all", params=params)
    return SiteCheck("unverifiable", "none", (), (), (), params.site_gap_percent)


def _window_hours(windows: Sequence[Window]) -> tuple[datetime, ...]:
    """Every UTC hour of the windows, in order, each once."""
    return tuple(sorted({window.start + n * HOUR for window in windows for n in range(window.span // HOUR)}))


def site_admission_7d(windows: Sequence[Window], days: Sequence[DayTotals], *, params: GscRulesParams) -> SiteCheck:
    """The site layer of the 7-day windows: each of the 14 PT days against the total of its detail's dataState (D27)."""
    by_day = {day.pt_date: day for day in days}
    units = tuple(_daily_unit(day, by_day.get(day)) for day in window_days(*windows))
    return check_units(units, "a2_by_state", params=params)


def _daily_unit(day: date, totals: DayTotals | None) -> GapUnit:
    if totals is None or totals.detail_state is None:
        return GapUnit(day, None, None)
    total = totals.a2_final if totals.detail_state == "final" else totals.a2_all
    return GapUnit(day, total, totals.detail)
