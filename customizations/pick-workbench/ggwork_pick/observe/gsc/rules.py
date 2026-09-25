"""gsc-rules-v1: the labels, their formal bars and one row's two-layer admission (plan TR-09, D38; design 5.6, 5.8).

The rule modules (cutoff, pageset, params, coverage, quality, rules) are pure functions over values the collector has
already read: no database, no HTTP, no clock of their own. Every threshold comes from the caller's GscRulesParams
(params.py); a comparison made with other parameters than the judgment's is refused.

A GSC judgment row is identity x country (or ALL) x window. It is admitted (formal) only when every coverage condition
holds: the round has a formal window (24 hours: a fresh H_c whose 48 hours are covered; 7 days: no stale D/E day), the
site layer is usable, and in both windows the detail sum and the filter request agree (coverage.per_identity_consistency).
Otherwise the row is descriptive and every hit on it is descriptive; reasons says why.

| label     | hit (on the admitted values, or the detail's when the row is descriptive)      | formal bar                               |
| surge     | W0 >= 2000, or W0 >= W-1 +50% (W-1 observed and > 0)                           | W-1 >= 20; else "小基数曝光上升"          |
| from_zero | W-1 has no row in the detail nor in the filter response, W0 >= 20              | the filter checked W-1: both sides no row |
| high_ctr  | W0 clicks >= 50 and CTR >= 5%                                                  | clicks agree in W0 too                    |
| rank_push | weighted position 4-20 and Q shows the title with an intent word; ALL only     | W0 >= 50, Q on a PT day of W0             |
| rising    | 7 days: W0 >= 100, ratio >= 1.5, increment >= 30                               | the 7-day row is admitted                 |

rising with a W-1 that has no row, or rows summing to 0, still hits on W0 and the increment (D38 reserves "基线未观测，
不检验" for its quality note): the ratio is undefined, and the condition says so instead of claiming it.

mapping_changed keeps the rising kinds (surge, from_zero, rising) descriptive this round (design 5.5). A row's state is
its first formal label in GSC_STATES order, present when none. Every hit carries its condition text and raw counts;
None means not observed, never 0, and an observed count is written as observed (观测到 n), so an observed 0 never reads
like the forms premise 1 forbids (premise 1). No label gives a cause: a rise reads 曝光上升, with or without 伴随平均排名
改善 >=2 位, and 原因未定 (counterexample 12). The quality note (quality.py) is attached after the whole set is judged and
never changes a label (D38).

Which identities get a filter request (pending, wants_filter) is decided on the detail values, a lower bound of the
admitted value max(X_flt, X_det): gsc-rules-v1 keeps this deliberately conservative, so an identity just under a bar on
the detail (W-1 detail 18, filter 20; W0 detail 1990, filter 2050) is never asked for and stays descriptive. The shadow
run counts those near misses before a later version decides whether to widen the pre-filter by the consistency
tolerance.

The phrases below are spelled out here until wording.py (TR-10, same batch) can be imported; a test pins them to it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.contract import (
    GSC_FLAGS,
    GSC_STATES,
    UNOBSERVED_GSC,
    Admission,
    GscFlag,
    GscLabel,
    GscState,
    GscWindowKind,
    SiteAdmission,
)
from ggwork_pick.observe.contract_rows import LabelHit, QualityNote
from ggwork_pick.observe.gsc.coverage import ALL, Comparison
from ggwork_pick.observe.gsc.cutoff import Cutoff, Window, daily_windows, hourly_windows
from ggwork_pick.observe.gsc.params import GscRulesParams
from ggwork_pick.observe.instants import stamp

W0_TEXT, W1_TEXT = "W0", "W−1"
METRIC_TEXT = MappingProxyType({"impressions": "曝光", "clicks": "点击"})
INPUT_FLAGS = ("migration_suspect", "mapping_changed", "short_history")  # attribution's and collection's; the rest are computed here
RISING_KINDS = ("surge", "from_zero", "rising")
RISE, NO_CAUSE = "曝光上升", "原因未定"  # wording.IMPRESSIONS_UP, wording.CAUSE_UNDETERMINED
WITH_RANK_GAIN, WITHOUT_RANK_GAIN = "伴随平均排名改善 ≥{n} 位", "未伴随排名改善"  # wording.WITH_RANK_GAIN (n=2), WITHOUT_RANK_GAIN
SMALL_BASE = "小基数曝光上升"  # wording.SMALL_BASE_SURGE
FROM_ZERO_NOTE, NEW_UNCHECKED = "基线未观测到", "新出现，基线未核对"  # wording.FROM_ZERO_NOTE, wording.FROM_ZERO_UNCHECKED
OBSERVED = "观测到 {n}"
FROM_ZERO_CHECKED = f"W−1 两边都没有行（{FROM_ZERO_NOTE}），W0 一致且 ≥ {{n}}"
FROM_ZERO_BASE_ONLY = f"W−1 两边都没有行（{FROM_ZERO_NOTE}），W0 ≥ {{n}}（W0 两份下界不一致，只作描述）"
FROM_ZERO_UNCHECKED = f"W−1 明细没有行，W0 ≥ {{n}}（{NEW_UNCHECKED}）"
# Reasons that only wait for this identity's (new) filter result; any other reason stops the per-identity requests.
AWAITING_FILTER = ("filter_missing", "vh_not_this_round", "vd_invalid")


# ---- inputs ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricWindows:
    w0: Comparison
    w_minus_1: Comparison


@dataclass(frozen=True, slots=True)
class QueryEvidence:
    """Q's evidence for rank_push: the PT day it was taken on and the query with the title and an intent word."""

    pt_date: date
    query: str


@dataclass(frozen=True, slots=True)
class Inputs24h:
    scope: str
    impressions: MetricWindows
    clicks: MetricWindows
    cutoff: Cutoff
    site: SiteAdmission
    position_w0: float | None = None
    position_w_minus_1: float | None = None
    query: QueryEvidence | None = None
    flags: tuple[GscFlag, ...] = ()


@dataclass(frozen=True, slots=True)
class Inputs7d:
    """latest_day: this round's latest complete PT day (cutoff.latest_complete_day); the windows must end on it."""

    scope: str
    impressions: MetricWindows
    site: SiteAdmission
    latest_day: date
    stale_days: tuple[date, ...] = ()
    position_w0: float | None = None
    position_w_minus_1: float | None = None
    flags: tuple[GscFlag, ...] = ()


# ---- the judgment ----------------------------------------------------------------------------------------------------


def _stamp(moment: datetime) -> str:
    # repository.stamp()'s form, through the observe package's own copy: a collector module never reaches the gateway's
    # repository, which writes the database outside the lease (TR-13, test_write_paths).
    return stamp(moment)


def _comparison_json(comparison: Comparison) -> dict[str, Any]:
    flt = comparison.x_flt
    return {
        "x_det": comparison.x_det.value,
        "x_det_rows": comparison.x_det.row_count,
        "x_flt": None if flt is None else flt.value,
        "x_flt_rows": None if flt is None else flt.row_count,
        "admitted": comparison.admitted,
        "value": comparison.value,
        "reason": comparison.reason,
    }


@dataclass(frozen=True, slots=True)
class Judgment:
    """One GSC judgment row's rule output. pending: labels whose own bar holds and that turn formal once the row is
    admitted, which is what the per-identity requests are sent for. reasons: why the row, or a label on it, stayed
    descriptive."""

    window_kind: GscWindowKind
    scope: str
    state: GscState
    admission: Admission
    labels: tuple[LabelHit, ...]
    flags: tuple[GscFlag, ...]
    reasons: tuple[str, ...]
    pending: tuple[GscLabel, ...]
    narrative: tuple[str, ...]
    impressions: MetricWindows
    clicks: MetricWindows | None
    site: SiteAdmission
    positions: tuple[float | None, float | None]
    query: QueryEvidence | None
    stale_days: tuple[date, ...]
    quality_note: QualityNote | None = None

    def metrics(self) -> dict[str, Any]:
        """Every intermediate value and raw count for the row's metrics column, as a new JSON-ready dict."""
        pairs = (("impressions", self.impressions), *((("clicks", self.clicks),) if self.clicks else ()))
        return {
            **{name: {"w0": _comparison_json(pair.w0), "w_minus_1": _comparison_json(pair.w_minus_1)} for name, pair in pairs},
            "windows": {
                key: [_stamp(w.start), _stamp(w.end)] for key, w in (("w0", self.impressions.w0.window), ("w_minus_1", self.impressions.w_minus_1.window))
            },
            "position_w0": self.positions[0],
            "position_w_minus_1": self.positions[1],
            "site_admission": self.site,
            "stale_days": [day.strftime("%Y-%m-%d") for day in self.stale_days],
            "reasons": list(self.reasons),
            "pending": list(self.pending),
            "narrative": list(self.narrative),
            "rank_push_query": None if self.query is None else self.query.query,
        }

    def row_fields(self) -> dict[str, Any]:
        """The StateRow fields this judgment decides, JSON-ready: the collector adds the identity, set and stamps."""
        return {
            "scope": self.scope,
            "window_kind": self.window_kind,
            "window_end": _stamp(self.impressions.w0.window.end),
            "state": self.state,
            "admission": self.admission,
            "labels": [hit.model_dump() for hit in self.labels],
            "flags": list(self.flags),
            "metrics": self.metrics(),
            "quality_note": None if self.quality_note is None else self.quality_note.model_dump(),
        }

    def with_quality_note(self, note: QualityNote) -> "Judgment":
        """The same judgment with its D38 note: a note only, nothing else changes."""
        if self.window_kind != "7d":
            raise ValueError("a quality note belongs to a 7-day row only")
        return replace(self, quality_note=note)


def wants_filter(judgment: Judgment) -> bool:
    """Whether to send this identity's Vh (24 hours) or Vd (7 days): a label's own bar holds on the detail values and
    nothing but the filter result stands in the way (an admitted row waits for nothing). The site layer and the window
    must already pass (design 5.6), so a descriptive round sends none. A Vh from another round, and a Vd that is no
    longer valid (D26) or did not answer in an earlier round (coverage.vd_current), are asked again whatever their
    status; this round's own failure is not asked twice."""
    waiting = any(reason in AWAITING_FILTER for reason in judgment.reasons)
    return bool(judgment.pending) and waiting and all(reason in (*AWAITING_FILTER, "mapping_changed") for reason in judgment.reasons)


def count_text(value: int | None) -> str:
    """A count as the page and the agent show it: not observed is said so, never written as 0, and an observed count,
    0 included, is marked observed (premise 1)."""
    return UNOBSERVED_GSC if value is None else OBSERVED.format(n=value)


def _shown(comparison: Comparison, formal: bool) -> int | None:
    return comparison.value if formal and comparison.admitted else comparison.x_det.value


def count_lines(judgment: Judgment) -> tuple[str, ...]:
    """The counts the labels were judged on: admitted values on a formal row, the detail's on a descriptive one.

    Written "W0 的曝光", never "W0 曝光": the forbidden phrase 0 曝光 must not appear even inside a window name; the
    count follows as count_text writes it (W−1 的点击：观测到 0)."""
    formal = judgment.admission == "formal"
    pairs = (("impressions", judgment.impressions), *((("clicks", judgment.clicks),) if judgment.clicks else ()))
    return tuple(
        f"{window} 的{METRIC_TEXT[name]}：{count_text(_shown(comparison, formal))}"
        for name, pair in pairs
        for window, comparison in ((W0_TEXT, pair.w0), (W1_TEXT, pair.w_minus_1))
    )


# ---- labels ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidate:
    label: GscLabel
    pending: bool  # its own bar holds
    formal: bool
    condition: str
    counts: Mapping[str, int | None]


def _surge(i0: int | None, i1: int | None, row_formal: bool, blocked: bool, p: GscRulesParams) -> _Candidate | None:
    by_size = i0 is not None and i0 >= p.surge_min_w0
    by_ratio = i0 is not None and i1 is not None and 0 < i1 < i0 and i0 * 100 >= p.surge_ratio_percent * i1
    if not (by_size or by_ratio):
        return None
    branches = "、".join(text for hit, text in ((by_size, f"W0 ≥ {p.surge_min_w0}"), (by_ratio, f"W0 对 W−1 ≥ +{p.surge_ratio_percent - 100}%")) if hit)
    base_ok = i1 is not None and i1 >= p.surge_min_base
    small = f"（{SMALL_BASE}，只作描述）"
    base = f"W−1 ≥ {p.surge_min_base}" if base_ok else (f"W−1 < {p.surge_min_base}{small}" if i1 is not None else f"W−1 {UNOBSERVED_GSC}{small}")
    pending = base_ok and not blocked
    return _Candidate("surge", pending, pending and row_formal, f"{branches}，{base}", {"w0": i0, "w_minus_1": i1})


def _from_zero(impressions: MetricWindows, i0: int | None, row_formal: bool, blocked: bool, p: GscRulesParams) -> _Candidate | None:
    before = impressions.w_minus_1
    rows_seen = before.x_det.observed or (before.x_flt is not None and before.x_flt.observed)
    if rows_seen or i0 is None or i0 < p.from_zero_min_w0:
        return None
    checked = before.admitted and before.x_flt is not None  # the filter asked for W-1 and neither side had a row
    text = FROM_ZERO_UNCHECKED if not checked else (FROM_ZERO_CHECKED if impressions.w0.admitted else FROM_ZERO_BASE_ONLY)
    pending = not blocked
    return _Candidate("from_zero", pending, pending and row_formal and checked, text.format(n=p.from_zero_min_w0), {"w0": i0, "w_minus_1": None})


def _high_ctr(inputs: Inputs24h, row_formal: bool, p: GscRulesParams) -> _Candidate | None:
    agreed = row_formal and inputs.clicks.w0.admitted
    impressions = inputs.impressions.w0.value if agreed else inputs.impressions.w0.x_det.value
    clicks = inputs.clicks.w0.value if agreed else inputs.clicks.w0.x_det.value
    if not impressions or clicks is None or clicks < p.high_ctr_min_clicks or clicks * 100 < p.high_ctr_min_percent * impressions:
        return None
    condition = f"W0 点击 ≥ {p.high_ctr_min_clicks} 且 CTR ≥ {p.high_ctr_min_percent}%"
    return _Candidate("high_ctr", True, agreed, condition, {"clicks": clicks, "impressions": impressions})


def _rank_push(inputs: Inputs24h, i0: int | None, w0: Window, row_formal: bool, p: GscRulesParams) -> _Candidate | None:
    position = inputs.position_w0
    if inputs.scope != ALL or inputs.query is None or position is None or not p.rank_push_best <= position <= p.rank_push_worst:
        return None
    pending = i0 is not None and i0 >= p.rank_push_min_impressions and inputs.query.pt_date in w0.pt_days
    condition = (
        f"加权排名 {p.rank_push_best}–{p.rank_push_worst}，Q 里出现剧名加意图词；"
        f"W0 的曝光 ≥ {p.rank_push_min_impressions}，Q 与 W0 同一个 PT 日（只作全站证据）"
    )
    return _Candidate("rank_push", pending, pending and row_formal, condition, {"impressions": i0})


def _rising(i0: int | None, i1: int | None, row_formal: bool, blocked: bool, p: GscRulesParams) -> _Candidate | None:
    base = i1 or 0  # no row, or rows summing to 0: the ratio is undefined and W0 and the increment decide
    if i0 is None or i0 < p.rising_min_w0 or i0 * 100 < p.rising_ratio_percent * base or i0 - base < p.rising_min_increment:
        return None
    pending = not blocked
    return _Candidate("rising", pending, pending and row_formal, _rising_condition(i1, p), {"w0": i0, "w_minus_1": i1})


def _rising_condition(i1: int | None, p: GscRulesParams) -> str:
    bars = f"W0 ≥ {p.rising_min_w0}、增量 ≥ {p.rising_min_increment}"
    if i1:
        return f"W0 ≥ {p.rising_min_w0}、比值 ≥ {p.rising_ratio_percent / 100:g}、增量 ≥ {p.rising_min_increment}"
    return f"W−1 {UNOBSERVED_GSC if i1 is None else count_text(i1)}（比值不适用），{bars}"


# ---- assembling a row ------------------------------------------------------------------------------------------------


def _unique(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _comparison_reasons(impressions: MetricWindows) -> tuple[str, ...]:
    return tuple(c.reason for c in (impressions.w0, impressions.w_minus_1) if not c.admitted)


def _narrative(candidates, positions: tuple[float | None, float | None], p: GscRulesParams) -> tuple[str, ...]:
    if not any(c.label in RISING_KINDS for c in candidates):
        return ()
    now, before = positions
    improved = now is not None and before is not None and before - now >= p.rank_improvement
    return RISE, (WITH_RANK_GAIN.format(n=p.rank_improvement) if improved else WITHOUT_RANK_GAIN), NO_CAUSE


def _flags(given: tuple[str, ...], site: str, impressions: MetricWindows, stale: bool) -> tuple[GscFlag, ...]:
    computed = {
        "gap_exceeded": site == "gap_exceeded",
        "unverifiable": site == "unverifiable",
        "detail_gap": "detail_gap" in _comparison_reasons(impressions),
        "stale_slice": stale,
    }
    return tuple(flag for flag in GSC_FLAGS if flag in given or computed.get(flag, False))


def _check_common(scope: str, flags: tuple[str, ...], pairs: tuple[MetricWindows, ...], params: GscRulesParams) -> None:
    if not (scope == ALL or (len(scope) == 3 and scope.isascii() and scope.isalpha() and scope.isupper())):
        raise ValueError(f"scope {scope!r} is ALL or an upper-case alpha-3 country")
    unknown = [flag for flag in flags if flag not in INPUT_FLAGS]
    if unknown:
        raise ValueError(f"flags {unknown} are computed by the rules, not passed in")
    tolerance = (params.consistency_percent, params.consistency_floor)
    if any(c.tolerance != tolerance for pair in pairs for c in (pair.w0, pair.w_minus_1)):
        raise ValueError("the comparisons were made with other gsc-rules parameters than this judgment's")


@dataclass(frozen=True, slots=True)
class _Found:
    """What one judge_* call found, handed to _assemble; clicks and query exist for the 24-hour windows only."""

    kind: GscWindowKind
    candidates: tuple[_Candidate | None, ...]
    coverage_reasons: tuple[str, ...]
    stale_days: tuple[date, ...]
    clicks: MetricWindows | None = None
    query: QueryEvidence | None = None


def _assemble(inputs: Inputs24h | Inputs7d, found_in: _Found, p: GscRulesParams) -> Judgment:
    found = tuple(c for c in found_in.candidates if c is not None)
    formal_labels = {c.label for c in found if c.formal}
    order = {label: index for index, label in enumerate(GSC_STATES)}
    ordered = sorted(found, key=lambda c: order[c.label])
    positions = (inputs.position_w0, inputs.position_w_minus_1)
    return Judgment(
        window_kind=found_in.kind,
        scope=inputs.scope,
        state=next((state for state in GSC_STATES if state in formal_labels), "present"),
        admission="descriptive" if found_in.coverage_reasons else "formal",
        labels=tuple(LabelHit(label=c.label, formal=c.formal, condition=c.condition, counts=dict(c.counts)) for c in ordered),
        flags=_flags(inputs.flags, inputs.site, inputs.impressions, bool(found_in.stale_days)),
        reasons=_unique((*found_in.coverage_reasons, *(("mapping_changed",) if "mapping_changed" in inputs.flags else ()))),
        pending=tuple(c.label for c in ordered if c.pending),
        narrative=_narrative(found, positions, p),
        impressions=inputs.impressions,
        clicks=found_in.clicks,
        site=inputs.site,
        positions=positions,
        query=found_in.query,
        stale_days=found_in.stale_days,
    )


def judge_24h(inputs: Inputs24h, params: GscRulesParams) -> Judgment:
    """One identity x scope for the 24-hour windows of the cutoff's H_c, with the set's gsc-rules parameters."""
    _check_common(inputs.scope, inputs.flags, (inputs.impressions, inputs.clicks), params)
    if inputs.cutoff.h_c is None:
        raise ValueError("no cutoff at all: nothing to judge the 24-hour windows against")
    w0, w1 = hourly_windows(inputs.cutoff.h_c)
    pairs = (inputs.impressions, inputs.clicks)
    if any((pair.w0.window, pair.w_minus_1.window) != (w0, w1) for pair in pairs):
        raise ValueError("both 24-hour windows come from this round's one cutoff")
    window_reasons = () if inputs.cutoff.formal_24h else ("no_formal_window", *inputs.cutoff.reasons)
    site_reasons = () if inputs.site == "usable" else (f"site_{inputs.site}",)
    coverage_reasons = _unique((*window_reasons, *site_reasons, *_comparison_reasons(inputs.impressions)))
    row_formal = not coverage_reasons
    blocked = "mapping_changed" in inputs.flags
    i0 = _shown(inputs.impressions.w0, row_formal)
    i1 = _shown(inputs.impressions.w_minus_1, row_formal)
    candidates = (
        _surge(i0, i1, row_formal, blocked, params),
        _from_zero(inputs.impressions, i0, row_formal, blocked, params),
        _high_ctr(inputs, row_formal, params),
        _rank_push(inputs, i0, w0, row_formal, params),
    )
    stale_days = inputs.cutoff.stale_dates if "stale_slice" in inputs.cutoff.reasons else ()
    return _assemble(inputs, _Found("24h", candidates, coverage_reasons, stale_days, inputs.clicks, inputs.query), params)


def judge_7d(inputs: Inputs7d, params: GscRulesParams) -> Judgment:
    """One identity x scope for the 7-day windows ending on the round's latest complete PT day, with the set's gsc-rules
    parameters; the site layer covers all 14 PT days (coverage.site_admission_7d)."""
    _check_common(inputs.scope, inputs.flags, (inputs.impressions,), params)
    w0, w1 = inputs.impressions.w0.window, inputs.impressions.w_minus_1.window
    if daily_windows(inputs.latest_day) != (w0, w1):
        raise ValueError("the 7-day windows are this round's latest 7 complete PT days and the 7 before them")
    if any(day not in w0.days + w1.days for day in inputs.stale_days):
        raise ValueError("stale days lie within the 14 PT days")
    site_reasons = () if inputs.site == "usable" else (f"site_{inputs.site}",)
    stale_reasons = ("stale_slice",) if inputs.stale_days else ()
    coverage_reasons = _unique((*stale_reasons, *site_reasons, *_comparison_reasons(inputs.impressions)))
    row_formal = not coverage_reasons
    i0 = _shown(inputs.impressions.w0, row_formal)
    i1 = _shown(inputs.impressions.w_minus_1, row_formal)
    candidate = _rising(i0, i1, row_formal, "mapping_changed" in inputs.flags, params)
    return _assemble(inputs, _Found("7d", (candidate,), coverage_reasons, tuple(sorted(inputs.stale_days))), params)
