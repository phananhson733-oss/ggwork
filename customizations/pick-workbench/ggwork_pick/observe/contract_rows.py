"""The observation radar's stored rows (plan TR-33): judgment, V check, totals, link and alert rows, and set summaries.

Part of the shared data contract; see contract.py for the rules the four modules follow and the document they match.
"""

import json
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal, Union

from pydantic import Field, StrictBool, model_validator

from ggwork_pick.observe.contract import (
    ALERT_STATES,
    GSC_COUNTRY_PATTERN,
    GSC_FLAGS,
    GSC_STATES,
    GSC_WINDOW_KINDS,
    TREND_GEO_PATTERN,
    TREND_STATES,
    TRENDS_FLAGS,
    TRENDS_ROW_STATES,
    TRENDS_WINDOW_KINDS,
    Admission,
    Ambiguity,
    Channel,
    Confirmation,
    Correspondence,
    Count,
    DataState,
    Day,
    EvalRulesVersion,
    Frozen,
    GscAlertState,
    GscCountry,
    GscFlag,
    GscLabel,
    GscState,
    GscWindowKind,
    Identity,
    IdEvidence,
    LinkLabel,
    LinkRulesVersion,
    Metric,
    Mode,
    RoundId,
    RowId,
    Scope,
    SetId,
    Share,
    ShortText,
    SiteAdmission,
    Stamp,
    StatusCode,
    Text,
    Tier,
    TotalsKind,
    TotalsStatus,
    TrendGeo,
    TrendsAlertState,
    TrendsFlag,
    TrendsRowState,
    TrendsWindowKind,
    UnattributedKind,
    UncoveredReason,
    VcheckKind,
    VcheckStatus,
    VcheckSummary,
    WindowLabel,
    first_problem,
    refuse,
)

# ---- judgment rows (ggwp_obs_states, view pick_obs.states) --------------------------------------------------------


class LabelHit(Frozen):
    label: GscLabel
    formal: StrictBool
    condition: Text
    counts: dict[str, Count | None]


class QualityNote(Frozen):
    """D38: a note beside the 7-day rising label, never a gate."""

    tested: StrictBool
    rate_ratio: float | None
    dispersion: float | None
    z: float | None
    p_value: Share | None
    bh_adjusted: Share | None
    bh_q: Share
    bh_passed: StrictBool | None
    note: Annotated[str, Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _tested(self):
        numbers = (self.rate_ratio, self.dispersion, self.z, self.p_value, self.bh_adjusted, self.bh_passed)
        consistent = all(n is not None for n in numbers) if self.tested else all(n is None for n in numbers)
        refuse("obs_quality_note", None if consistent else "做了检验才有数值，没做检验全为空")
        return self


class PasteRow(Frozen):
    """Design 2.2: the editorial sheet's columns; url is the current drama page RealShort's sync parses."""

    title: Text
    url: Annotated[str, Field(pattern=r"^https://[a-z0-9.-]+/[a-z]{2}(-[A-Za-z]{2,4})?/drama/[^/?#\s]+-[0-9a-f]{24}$", max_length=2048)]
    impressions: Count | None
    clicks: Count | None
    window_kind: GscWindowKind
    top_query: Annotated[str, Field(min_length=1, max_length=200)] | None
    verified_on: Day
    note: Annotated[str, Field(max_length=1000)]


def _trends_row_problem(row) -> str | None:
    return first_problem(
        (
            (re.fullmatch(TREND_GEO_PATTERN, row.scope) is not None, "scope 是 WW 或两位国家码"),
            (row.window_kind in TRENDS_WINDOW_KINDS, "window_kind 是 H 或 D"),
            (row.state in TRENDS_ROW_STATES, "state 不是 Trends 的状态"),
            ((row.confirmation is not None) == (row.state in TREND_STATES), "rising、emerging 才带 confirmation"),
            (row.admission is None and not row.labels, "admission 与 labels 属于 GSC"),
            (None not in (row.tier, row.correspondence, row.id_evidence, row.ambiguity), "tier、correspondence、id_evidence、ambiguity 必填"),
            (row.latest_block_end is not None, "Trends 行带 latest_block_end（D39）"),
            (set(row.flags) <= set(TRENDS_FLAGS), "flags 不是 Trends 的标记"),
            (row.quality_note is None and row.paste_row is None, "质量注记与可粘贴行属于 GSC"),
            (row.carried_over or not row.stale, "stale 是沿用超过 3 天的行，只有 carried_over 的行才会 stale（设计 4.10）"),
        )
    )


def _gsc_row_problem(row) -> str | None:
    formal = {hit.label for hit in row.labels if hit.formal}
    return first_problem(
        (
            (re.fullmatch(GSC_COUNTRY_PATTERN, row.scope) is not None, "scope 是三位国家码或 ALL"),
            (row.window_kind in GSC_WINDOW_KINDS, "window_kind 是 24h 或 7d"),
            (row.state in GSC_STATES, "state 不是 gsc_state 的取值"),
            (row.admission is not None, "GSC 行记下两层准入的结果"),
            ((row.confirmation, row.tier, row.correspondence, row.id_evidence, row.ambiguity) == (None,) * 5, "GSC 行不带 Trends 的字段"),
            (row.latest_block_end is None and not row.carried_over and not row.stale, "GSC 行没有块终点、沿用与陈旧"),
            (set(row.flags) <= set(GSC_FLAGS), "flags 不是 GSC 的标记"),
            (row.quality_note is None or row.window_kind == "7d", "质量注记只属于 7 天窗口"),
            (row.admission != "descriptive" or not formal, "描述性行没有正式标签"),
            (row.state in formal if row.state != "present" else not formal, "state 取正式命中的标签之一，没有正式标签时为 present（设计 5.8）"),
        )
    )


class StateRow(Frozen):
    """One judgment row. Trends: identity x geo. GSC: identity x country or ALL x window; state is the primary formal label,
    or present when no label passed its formal bar; labels lists every hit, formal or descriptive, with its condition and
    raw counts (null: not observed, never 0)."""

    row_id: RowId
    set_id: SetId
    channel: Channel
    mode: Mode
    identity: Identity
    title: Text
    language: Annotated[str, Field(min_length=1, max_length=40)]
    theater: Annotated[str, Field(max_length=100)]
    scope: Scope
    window_kind: TrendsWindowKind | GscWindowKind
    state: TrendsRowState | GscState
    confirmation: Confirmation | None
    admission: Admission | None
    labels: list[LabelHit]
    tier: Tier | None
    correspondence: Correspondence | None
    id_evidence: IdEvidence | None
    ambiguity: Ambiguity | None
    flags: list[TrendsFlag | GscFlag]
    carried_over: StrictBool
    stale: StrictBool
    window_end: Stamp
    latest_block_end: Stamp | None
    metrics: dict[str, Any]
    quality_note: QualityNote | None
    paste_row: PasteRow | None
    created_at: Stamp

    @model_validator(mode="after")
    def _channel_shape(self):
        problem = _trends_row_problem(self) if self.channel == "trends" else _gsc_row_problem(self)
        refuse("obs_state_row", problem, channel=self.channel)
        return self


# ---- V checks and totals (D26, D27) ------------------------------------------------------------------------------
# Request statuses that yield no numbers: a failure never becomes a value, not even 0 (premise 1).
NO_VALUE_VCHECK_STATUSES = ("failed", "regex_overflow")
NO_VALUE_TOTALS_STATUSES = ("failed", "unsupported")


class SliceDay(Frozen):
    pt_date: Day
    version_id: RowId


class VcheckRow(Frozen):
    """ggwp_gsc_vchecks: identity x check x window x country x dataState; null counts mean the response had no row."""

    id: RowId
    round_id: RoundId
    identity: Identity
    check_kind: VcheckKind
    window_label: WindowLabel
    window_start: Stamp
    window_end: Stamp
    country: GscCountry
    data_state: DataState
    impressions: Count | None
    clicks: Count | None
    row_count: Count
    request_status: VcheckStatus
    chunk_count: Count
    slice_versions: list[SliceDay]
    fetched_at: Stamp
    reused: StrictBool
    reused_from: RowId | None

    @model_validator(mode="after")
    def _shape(self):
        hourly = self.check_kind == "vh"
        no_value = (self.row_count, self.impressions, self.clicks) == (0, None, None)
        checks = (
            ((self.data_state == "hourly_all") == hourly, "Vh 用 hourly_all，Vd 用 all 或 final"),
            ((not self.slice_versions) == hourly, "Vd 列出它覆盖的 PT 日与切片版本，Vh 不列"),
            (not (hourly and self.reused), "Vh 每轮重取，从不复用"),
            (self.reused == (self.reused_from is not None), "复用时写明复用的行"),
            ((self.row_count == 0) == (self.impressions is None) == (self.clicks is None), "无行记空值，有行记计数"),
            (self.request_status not in NO_VALUE_VCHECK_STATUSES or no_value, "失败或正则溢出的请求没有数值：row_count 为 0，计数为空"),
        )
        refuse("obs_vcheck_row", first_problem(checks))
        return self


TOTALS_SHAPES = MappingProxyType(
    {"a": ("hourly_all", "hour"), "a_prime": ("hourly_all", "hour"), "a2_all": ("all", "pt_date"), "a2_final": ("final", "pt_date")}
)


class TotalsRow(Frozen):
    """ggwp_gsc_totals: A and A' per hour, A''a and A''f per PT date; null counts mean no row (无行), never 0."""

    id: RowId
    round_id: RoundId
    kind: TotalsKind
    data_state: DataState
    hour: Stamp | None
    pt_date: Day | None
    impressions: Count | None
    clicks: Count | None
    request_status: TotalsStatus
    watermark: Annotated[str, Field(min_length=1, max_length=40)] | None
    fetched_at: Stamp

    @model_validator(mode="after")
    def _shape(self):
        data_state, period = TOTALS_SHAPES[self.kind]
        checks = (
            (self.data_state == data_state, f"{self.kind} 的 dataState 是 {data_state}"),
            ((self.hour is not None, self.pt_date is not None) == (period == "hour", period == "pt_date"), f"{self.kind} 按 {period} 记"),
            ((self.impressions is None) == (self.clicks is None), "曝光与点击同时为空"),
            (self.request_status not in NO_VALUE_TOTALS_STATUSES or (self.impressions, self.clicks) == (None, None), "失败或不支持的请求没有数值，计数为空"),
        )
        refuse("obs_totals_row", first_problem(checks))
        return self


# ---- links (D13, design 6.1) and alerts (D37, design 6.2) --------------------------------------------------------


class _LinkIds(Frozen):
    id: RowId
    link_rules_version: LinkRulesVersion
    trends_set_id: SetId
    gsc_set_id: SetId
    mode: Mode
    identity: Identity


class LinkFact(Frozen):
    """What link_facts returns for one identity and country; actionability is never frozen (obs_link_cases.json)."""

    country: GscCountry | None
    trends_geo: TrendGeo | None
    label: LinkLabel
    trends_row_id: RowId | None
    gsc_row_id: RowId | None
    trends_anchor: Stamp
    gsc_anchor: Stamp
    pair_gap_minutes: Count
    timely: StrictBool
    published_gap_minutes: Count
    stale: StrictBool

    @model_validator(mode="after")
    def _scope(self):
        country, geo = self.country, self.trends_geo
        checks = (
            ((self.label == "different_markets") == (country is None), "different_markets 是身份层的事实，没有国家"),
            ((self.label == "global_parallel") == (country == "ALL"), "global_parallel 只配 WW 与 ALL"),
            (country is not None or geo is None, "没有国家就没有 geo"),
            (country != "ALL" or geo == "WW", "ALL 配 WW"),
            (country in (None, "ALL") or geo not in (None, "WW"), "国家配两位 geo"),
        )
        refuse("obs_link_fact", first_problem(checks))
        return self


# The pairing's own timeliness bound per link-rules version (design 6.1: W0 and the latest block end at most 48 h apart).
# TR-10's link_rules reads it from here; a new version registers here first (plan section 6).
LINK_PAIR_MAX_GAP_MINUTES = MappingProxyType({"link-rules-v1": 48 * 60})


class LinkRow(LinkFact, _LinkIds):
    """ggwp_obs_links and view pick_obs.links: the ids, then the fact, then when it was written."""

    created_at: Stamp

    @model_validator(mode="after")
    def _timely(self):
        most = LINK_PAIR_MAX_GAP_MINUTES.get(self.link_rules_version)
        checks = (
            (most is not None, "link-rules 版本没有登记"),
            (most is None or self.timely == (self.pair_gap_minutes <= most), "timely 是锚点相隔不超过该版本的上限"),
        )
        refuse("obs_link_row", first_problem(checks))
        return self


def dedupe_key_of(root_identity: str, channel: str, state: str, scope: str, mode: str) -> str:
    """D37's key, written like an identity: [root_identity, channel, state, scope, mode]. The only implementation: TR-10's
    leadtime and TR-20's store import it."""
    return json.dumps([root_identity, channel, state, scope, mode], ensure_ascii=False, separators=(",", ":"))


class AlertRow(Frozen):
    id: RowId
    mode: Mode
    channel: Channel
    set_id: SetId
    state_row_id: RowId
    identity: Identity
    root_identity: Identity
    state: TrendsAlertState | GscAlertState
    scope: Scope
    dedupe_key: Annotated[str, Field(min_length=1, max_length=2048)]
    published_at: Stamp
    eval_rules_version: EvalRulesVersion
    alias_version: Count
    evidence: dict[str, Any]
    created_at: Stamp

    @model_validator(mode="after")
    def _keyed(self):
        scope = TREND_GEO_PATTERN if self.channel == "trends" else GSC_COUNTRY_PATTERN
        checks = (
            (self.state in ALERT_STATES[self.channel], "提示状态属于它的通道"),
            (re.fullmatch(scope, self.scope) is not None, "Trends 记 geo，GSC 记国家"),
            (self.dedupe_key == dedupe_key_of(self.root_identity, self.channel, self.state, self.scope, self.mode), "去重键与各字段不一致"),
        )
        refuse("obs_alert_row", first_problem(checks))
        return self


# ---- set summaries (ggwp_obs_sets.summary_json) --------------------------------------------------------------------


class UncoveredUnit(Frozen):
    identity: Identity
    geo: TrendGeo
    reason: UncoveredReason


class SetSummaryTrends(Frozen):
    channel: Literal["trends"]
    planned_units: Count
    fetched_units: Count
    uncovered_units: list[UncoveredUnit]
    a_tier_coverage: Share
    breaker_events: Count
    all_zero_rate: Share | None
    user_types: list[ShortText]
    ambiguous_undecided: Count
    carried_over: Count
    stale: Count
    rule4_no_data: StrictBool
    status_codes: list[StatusCode]


class CoverageLayers(Frozen):
    """Design 5.6 for one metric: received = attributed + every unattributed kind; detail_gap = site_total - received."""

    metric: Metric
    received: Count
    attributed: Count
    unattributed: dict[UnattributedKind, Count]
    site_total: Count | None
    detail_gap: int | None

    @model_validator(mode="after")
    def _layers(self):
        gap = None if self.site_total is None else self.site_total - self.received
        checks = (
            (self.received == self.attributed + sum(self.unattributed.values()), "第一层不守恒"),
            (self.detail_gap == gap, "明细缺口等于全站总量减收到的明细，没有总量时为空"),
        )
        refuse("obs_coverage_layers", first_problem(checks))
        return self


class Unknowable(Frozen):
    truncated_slices: Count
    failed_slices: Count
    stale_slices: Count
    hours: Count


class SetSummaryGsc(Frozen):
    """cutoff is never null: this round's H_c, or the previous GSC set's when A has no watermark (design 5.3, cutoff_carried).
    formal_24h_window says this round has a formal 24-hour window; a carried cutoff never has one."""

    channel: Literal["gsc"]
    cutoff: Stamp
    cutoff_carried: StrictBool
    formal_24h_window: StrictBool
    layers: list[CoverageLayers]
    unknowable: Unknowable
    site_admission_24h: SiteAdmission
    site_admission_7d: SiteAdmission
    vcheck_summary: VcheckSummary
    requests: Count
    quota_errors: Count
    status_codes: list[StatusCode]

    @model_validator(mode="after")
    def _window(self):
        carried_formal = self.cutoff_carried and self.formal_24h_window
        refuse("obs_set_summary", None if not carried_formal else "沿用上一轮的 cutoff 时本轮没有正式 24 小时窗口")
        return self


SetSummary = Annotated[Union[SetSummaryTrends, SetSummaryGsc], Field(discriminator="channel")]  # noqa: UP007
