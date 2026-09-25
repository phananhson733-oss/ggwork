"""The observation radar's shared data contract (plan TR-33; D8, D11, D13, D24, D28, D31, D36, D37; design 7.x).

The wire and database shapes that the two collectors, the gateway, the agent and the data page share, written down once
before any of them is built. The contract is four modules:

- contract (this one): the switches, scalars and enums, the condition fields, the result-level observations and
  obs_as_of_json, how observation evidence is flattened into the existing evidence shape, and frozen_inputs_json;
- contract_rows: the judgment, V check, totals, link and alert rows and the set summaries;
- contract_api: the decision bodies and /sync's obs key;
- contract_views: the eight pick_obs views.

Shapes and constants only: nothing here judges, reads a table or calls out. Validators check a value's own shape (and the
invariants the design states for it, such as "no row is null, never 0"), never business rules. Stored and wire shapes are
Frozen (strict, closed, immutable, text kept verbatim); inputs from people and the model are StrictInput. The same content
is in docs/pick-workbench/observe-contract.md, with fixtures in tests/fixtures/obs_contract/ that the frontend reads too;
tests/observe/test_contract.py keeps the three equal. Changing it follows the plan's section 6 rule (TR-33 or a PR approved
at a G review, both sides' tests changed together).
"""

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal, Union, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from pydantic_core import PydanticCustomError

from ggwork_pick.contracts import IDENTITY_MAX_LENGTH, StrictInput, unstorable_path

# ---- switches (D11) ----------------------------------------------------------------------------------------------
# On the cron services: "1" publishes sets as live, anything else as shadow. On the gateway: "1" opens the seven condition
# fields to the model (the tool schema and its behaviour), anything else keeps the schema as before.
PUBLISH_SWITCH = "PICK_OBS_PUBLISH"
AGENT_SWITCH = "PICK_OBS_AGENT"
SWITCH_ON = "1"

# ---- scalars -----------------------------------------------------------------------------------------------------
STAMP_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00"  # repository.stamp()
TREND_GEO_PATTERN = r"WW|[A-Z]{2}"
GSC_COUNTRY_PATTERN = r"ALL|[A-Z]{3}"
MAX_ROW_ID = 2**63 - 1

Stamp = Annotated[str, Field(pattern=rf"^{STAMP_PATTERN}$")]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
SetId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
RoundId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
BatchId = Annotated[str, Field(min_length=1, max_length=64)]
RowId = Annotated[int, Field(ge=1, le=MAX_ROW_ID)]
Count = Annotated[int, Field(ge=0)]
PositiveCount = Annotated[int, Field(ge=1)]
Share = Annotated[float, Field(ge=0, le=1)]
Identity = Annotated[str, Field(min_length=1, max_length=IDENTITY_MAX_LENGTH)]
TrendGeo = Annotated[str, Field(pattern=rf"^({TREND_GEO_PATTERN})$")]
GscCountry = Annotated[str, Field(pattern=rf"^({GSC_COUNTRY_PATTERN})$")]
Scope = Annotated[str, Field(pattern=rf"^({TREND_GEO_PATTERN}|{GSC_COUNTRY_PATTERN})$")]
RulesVersion = Annotated[str, Field(pattern=r"^(trend|gsc|link|watch|eval)-rules-v[0-9A-Za-z]+$")]
LinkRulesVersion = Annotated[str, Field(pattern=r"^link-rules-v[0-9A-Za-z]+$")]
EvalRulesVersion = Annotated[str, Field(pattern=r"^eval-rules-v[0-9A-Za-z]+$")]
MarketMapVersion = Annotated[str, Field(pattern=r"^market-map-v[0-9A-Za-z]+$")]
CollectorVersion = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=500)]
ShortText = Annotated[str, Field(min_length=1, max_length=100)]

# ---- enums ---------------------------------------------------------------------------------------------------------
Channel = Literal["trends", "gsc"]
Mode = Literal["live", "shadow"]
SetStatus = Literal["published", "pruned"]
TrendState = Literal["rising", "emerging"]
GscState = Literal["rising", "surge", "from_zero", "high_ctr", "rank_push", "present"]
LinkState = Literal["both_rising", "trends_lead_page", "trends_lead_distribution", "site_only", "cooling"]
LinkLabel = Literal["both_rising", "trends_lead_page", "trends_lead_distribution", "site_only", "cooling", "global_parallel", "different_markets"]
Sort = Literal["evidence_date", "rank", "obs"]
ExclusionReason = Literal[
    "trend_first_only", "emerging_not_requested", "title_ambiguous", "shared_title", "correspondence_unconfirmed", "correspondence_presumed",
    "stale", "carried_over", "b_tier", "unstable", "control_unavailable", "gsc_descriptive_only", "migration_suspect", "mapping_changed",
    "set_batch_mismatch",
]  # fmt: skip
EvidenceKind = Literal["obs_trends", "obs_gsc", "obs_discovery"]
LinkActionabilityReason = Literal["label_not_actionable", "untimely_pair", "stale_row", "trends_set_too_old", "gsc_set_too_old"]
TrendsRowState = Literal["rising", "emerging", "cooling", "flat", "sparse", "insufficient_window", "ambiguous", "failed"]
GscLabel = Literal["surge", "from_zero", "high_ctr", "rank_push", "rising"]
TrendsFlag = Literal["shared_title", "unstable", "control_unavailable"]
GscFlag = Literal["migration_suspect", "mapping_changed", "gap_exceeded", "unverifiable", "detail_gap", "stale_slice", "short_history"]
LinkExcludedFlag = Literal["migration_suspect", "mapping_changed", "gap_exceeded", "unverifiable", "unstable"]
Ambiguity = Literal["clear", "generic", "contained", "unresolved", "manual_required"]
Confirmation = Literal["confirmed", "first"]
Admission = Literal["formal", "descriptive"]
Tier = Literal["A", "B"]
Correspondence = Literal["confirmed", "unconfirmed"]
IdEvidence = Literal["strong", "medium", "weak"]
TrendsWindowKind = Literal["H", "D"]
GscWindowKind = Literal["24h", "7d"]
DataState = Literal["hourly_all", "all", "final"]
VcheckKind = Literal["vh", "vd"]
WindowLabel = Literal["w0", "w_minus_1"]
VcheckStatus = Literal["fetched", "truncated", "failed", "regex_overflow"]
TotalsKind = Literal["a", "a_prime", "a2_all", "a2_final"]
TotalsStatus = Literal["fetched", "truncated", "failed", "unsupported"]
SliceDataset = Literal["C", "D", "E", "Q"]
SliceShape = Literal["whole", "by_country"]
SliceStatus = Literal["fetched", "truncated", "failed"]
Granularity = Literal["H", "D", "HD"]
TrendsAlertState = Literal["rising_first", "rising_confirmed", "emerging"]
GscAlertState = Literal["surge", "from_zero", "rising"]
DecisionKind = Literal[
    "alias_confirm", "alias_reject", "alias_pair", "correspondence_confirm", "correspondence_revoke", "watch_add", "watch_pause",
    "ambiguity_override", "alert_irrelevant",
]  # fmt: skip
AmbiguityVerdict = Literal["clear", "ambiguous"]
StatusCode = Literal[
    "stale_26h", "not_published_low_coverage", "extinguished_today", "disabled_7d", "canary_terminated", "usertype_changed", "all_zero_jump",
    "legacy_unmapped_2pct", "legacy_snapshot_missing", "gsc_gap_exceeded", "gsc_unverifiable", "run_missed", "shadow_mode", "db_size_cap",
    "parse_error",
]  # fmt: skip
BannerLevel = Literal["red", "warn", "info"]
DiscoveryProperty = Literal["web", "youtube"]
DiscoveryMatch = Literal["unique", "multiple", "out_of_pool"]
DiscoveryRoute = Literal["a_tier", "queue", "display_only"]
AliasStatus = Literal["auto", "suggested", "confirmed", "rejected"]
RunOutcome = Literal["running", "published", "withheld", "failed"]
UnattributedKind = Literal["legacy_unmapped", "delisted", "noncanonical", "locale_mismatch", "site_level", "editorial", "offsite", "blog"]
SiteAdmission = Literal["usable", "gap_exceeded", "unverifiable"]
UncoveredReason = Literal["truncated", "skipped_breaker", "deadline"]
Metric = Literal["impressions", "clicks"]
ViewType = Literal["text", "int", "bool", "json", "float"]

CHANNELS = get_args(Channel)
MODES = get_args(Mode)
TREND_STATES = get_args(TrendState)
GSC_STATES = get_args(GscState)
LINK_STATES = get_args(LinkState)
LINK_LABELS = get_args(LinkLabel)
SORTS = get_args(Sort)
OBS_SORT = "obs"
EXCLUSION_REASONS = get_args(ExclusionReason)
EVIDENCE_KINDS = get_args(EvidenceKind)
LINK_ACTIONABILITY_REASONS = get_args(LinkActionabilityReason)
TRENDS_ROW_STATES = get_args(TrendsRowState)
TRENDS_FLAGS = get_args(TrendsFlag)
GSC_FLAGS = get_args(GscFlag)
LINK_EXCLUDED_FLAGS = get_args(LinkExcludedFlag)
TRENDS_WINDOW_KINDS = get_args(TrendsWindowKind)
GSC_WINDOW_KINDS = get_args(GscWindowKind)
ALERT_STATES = MappingProxyType({"trends": get_args(TrendsAlertState), "gsc": get_args(GscAlertState)})
DECISION_KINDS = get_args(DecisionKind)
STATUS_CODES = get_args(StatusCode)
BANNER_LEVELS = get_args(BannerLevel)
# Every enum by the name the document gives it, including those with no tuple constant of their own.
_ENUM_TYPES = {
    "CHANNELS": Channel, "MODES": Mode, "SET_STATUSES": SetStatus, "TREND_STATES": TrendState, "GSC_STATES": GscState,
    "LINK_STATES": LinkState, "LINK_LABELS": LinkLabel, "SORTS": Sort, "EXCLUSION_REASONS": ExclusionReason,
    "EVIDENCE_KINDS": EvidenceKind, "LINK_ACTIONABILITY_REASONS": LinkActionabilityReason, "TRENDS_ROW_STATES": TrendsRowState,
    "GSC_LABELS": GscLabel, "TRENDS_FLAGS": TrendsFlag, "GSC_FLAGS": GscFlag, "LINK_EXCLUDED_FLAGS": LinkExcludedFlag,
    "AMBIGUITIES": Ambiguity, "CONFIRMATIONS": Confirmation, "ADMISSIONS": Admission, "TIERS": Tier, "CORRESPONDENCES": Correspondence,
    "ID_EVIDENCE_LEVELS": IdEvidence, "TRENDS_WINDOW_KINDS": TrendsWindowKind, "GSC_WINDOW_KINDS": GscWindowKind, "DATA_STATES": DataState,
    "VCHECK_KINDS": VcheckKind, "WINDOW_LABELS": WindowLabel, "VCHECK_STATUSES": VcheckStatus, "TOTALS_KINDS": TotalsKind,
    "TOTALS_STATUSES": TotalsStatus, "SLICE_DATASETS": SliceDataset, "SLICE_SHAPES": SliceShape, "SLICE_STATUSES": SliceStatus,
    "GRANULARITIES": Granularity, "TRENDS_ALERT_STATES": TrendsAlertState, "GSC_ALERT_STATES": GscAlertState, "DECISION_KINDS": DecisionKind,
    "AMBIGUITY_VERDICTS": AmbiguityVerdict, "STATUS_CODES": StatusCode, "BANNER_LEVELS": BannerLevel, "DISCOVERY_PROPERTIES": DiscoveryProperty,
    "DISCOVERY_MATCHES": DiscoveryMatch, "DISCOVERY_ROUTES": DiscoveryRoute, "ALIAS_STATUSES": AliasStatus, "RUN_OUTCOMES": RunOutcome,
    "UNATTRIBUTED_KINDS": UnattributedKind, "SITE_ADMISSIONS": SiteAdmission, "UNCOVERED_REASONS": UncoveredReason, "METRICS": Metric,
    "VIEW_TYPES": ViewType,
}  # fmt: skip
ENUMS = MappingProxyType({name: get_args(literal) for name, literal in _ENUM_TYPES.items()})

# ---- base classes --------------------------------------------------------------------------------------------------


class Frozen(BaseModel):
    """A stored or wire shape: closed, strict (no coercion), immutable, text verbatim and storable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _storable_text(cls, data):
        where = unstorable_path(data)
        if where is not None:
            raise PydanticCustomError("unstorable_text", "{where} 含 NUL 字符或孤立代理项，无法保存", {"where": where})
        return data


def refuse(code: str, problem: str | None, **context) -> None:
    """For the contract modules' validators: raise a validation error carrying the problem, when there is one."""
    if problem is not None:
        raise PydanticCustomError(code, "{problem}", {"problem": problem, **context})


def first_problem(checks) -> str | None:
    """For the contract modules' validators: the message of the first (holds, message) pair that does not hold."""
    return next((message for holds, message in checks if not holds), None)


# ---- conditions (D31, design 7.3) ----------------------------------------------------------------------------------
MAX_TREND_GEOS = 10
MAX_GSC_COUNTRIES = 30


class ObsConditionFields(StrictInput):
    """The seven observation condition fields. TR-27 adds them to PickConditions (PickConditionsObs) with descriptions.

    Stored conditions (conditions_json, the request hash) drop the falsy ones, so a result without observation conditions
    stores what it stored before. Each geo or country is judged on its own; any one matching is a match.
    """

    trend_state: TrendState | None = None
    trend_geos: list[TrendGeo] = Field(default_factory=list, max_length=MAX_TREND_GEOS)
    trend_include_first: StrictBool = False
    trend_include_presumed: StrictBool = False
    gsc_state: GscState | None = None
    gsc_countries: list[GscCountry] = Field(default_factory=list, max_length=MAX_GSC_COUNTRIES)
    link_state: LinkState | None = None


OBS_CONDITION_FIELDS = tuple(ObsConditionFields.model_fields)
# selection.unmappable_conditions appends these, each when truthy, after its seven existing names.
UNMAPPABLE_OBS_ORDER = OBS_CONDITION_FIELDS
OBS_CONDITION_LABELS = MappingProxyType(
    {
        "trend_state": "Google Trends 状态（上升或从零起量观察）",
        "trend_geos": "Google Trends 地区",
        "trend_include_first": "接受只观察到一天的上升（首次）",
        "trend_include_presumed": "接受推定对应（强证据、未人工确认）",
        "gsc_state": "站内搜索（GSC）标签",
        "gsc_countries": "站内搜索（GSC）国家",
        "link_state": "两个通道的同国家联动",
    }
)

# ---- observations and obs_as_of_json (D28, design 7.4) -------------------------------------------------------------


class TrendsSetRef(Frozen):
    set_id: SetId
    published_at: Stamp
    latest_block_end: Stamp


class GscSetRef(Frozen):
    set_id: SetId
    published_at: Stamp
    cutoff: Stamp


class ObsCoverage(Frozen):
    pool_identities: Count
    trends_observed: Count
    gsc_observed: Count


class ObsSummary(Frozen):
    coverage: ObsCoverage
    excluded: dict[ExclusionReason, PositiveCount]


class Observations(Frozen):
    """result_view's observations: obs_as_of_json's reference keys followed by its inner summary's keys."""

    trends: TrendsSetRef | None
    gsc: GscSetRef | None
    link_rules_version: LinkRulesVersion | None
    judged_at: Stamp
    coverage: ObsCoverage
    excluded: dict[ExclusionReason, PositiveCount]


OBS_AS_OF_REF_KEYS = ("trends", "gsc", "link_rules_version", "judged_at")


class ObsAsOf(Frozen):
    """ggwp_candidate_sets.obs_as_of_json. Never inside data_as_of_json."""

    trends: TrendsSetRef | None
    gsc: GscSetRef | None
    link_rules_version: LinkRulesVersion | None
    judged_at: Stamp
    observations: ObsSummary

    @model_validator(mode="after")
    def _pair(self):
        paired = self.trends is not None and self.gsc is not None
        checks = (
            (self.trends is not None or self.gsc is not None, "带观测条件的结果至少钉住一个集合"),
            ((self.link_rules_version is not None) == paired, "两个集合都在时才冻结 link-rules 版本"),
        )
        refuse("obs_as_of", first_problem(checks))
        return self


# ---- evidence (D8, premise 1) --------------------------------------------------------------------------------------
UNOBSERVED = "未观测到"
UNOBSERVED_TRENDS = "在 Google Trends 返回的数据里未观测到"
UNOBSERVED_GSC = "在 GSC 返回的数据里未观测到"
NOTE_SEPARATOR = "；"
LINK_NOTE_PREFIX = "联动："
LINK_NOTE_PATTERN = re.compile(
    rf"{LINK_NOTE_PREFIX}[^（）；]+（(?:{'|'.join(LINK_LABELS)})；"
    rf"(?:可行动|不可行动：(?:{'|'.join(LINK_ACTIONABILITY_REASONS)})(?:,(?:{'|'.join(LINK_ACTIONABILITY_REASONS)}))*)；判定于 {STAMP_PATTERN}）"
)


@dataclass(frozen=True, slots=True)
class EvidenceRule:
    """How one kind flattens: its values (None: GSC counts or a discovery term), grades, label scope, note rules family."""

    values: tuple[str, ...] | None
    grades: tuple[str, ...]
    scope: str
    rules: str
    unobserved: str | None


EVIDENCE_RULES = MappingProxyType(
    {
        "obs_trends": EvidenceRule(("rising", "emerging", "cooling", ""), ("confirmed", "first", ""), TREND_GEO_PATTERN, "trend", UNOBSERVED_TRENDS),
        "obs_gsc": EvidenceRule(None, get_args(Admission), GSC_COUNTRY_PATTERN, "gsc", UNOBSERVED_GSC),
        "obs_discovery": EvidenceRule(None, get_args(IdEvidence), TREND_GEO_PATTERN, "trend", None),
    }
)


def _evidence_value_problem(kind: str, value) -> str | None:
    rule = EVIDENCE_RULES[kind]
    if rule.values is not None:
        return None if value in rule.values else f"value 只能是 {'、'.join(rule.values)}"
    if kind == "obs_gsc":
        return None if isinstance(value, int) or value == "" else "value 是正整数的曝光数，未观测到时为空串"
    return None if isinstance(value, str) and value else "value 是发现词"


def _link_segments_ok(note: str) -> bool:
    return all(LINK_NOTE_PATTERN.match(note, found.start()) for found in re.finditer(LINK_NOTE_PREFIX, note))


class ObsEvidence(Frozen):
    """One obs_* entry of candidate_item's evidence: the existing nine keys, nothing added, rank always null."""

    citation_id: Annotated[str, Field(min_length=1, max_length=512)]
    kind: EvidenceKind
    source_ref: Annotated[str, Field(pattern=r"^obs:[0-9a-f]{32}:[1-9][0-9]{0,18}$")]
    observed_at: Stamp
    value: Annotated[StrictStr, Field(max_length=4000)] | Annotated[StrictInt, Field(ge=1)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    rank: None
    grade: Annotated[str, Field(max_length=100)]
    note: Annotated[str, Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _flattened(self):
        rule = EVIDENCE_RULES[self.kind]
        checks = (
            (_evidence_value_problem(self.kind, self.value) is None, _evidence_value_problem(self.kind, self.value)),
            (self.grade in rule.grades, f"grade 只能是 {'、'.join(rule.grades)}"),
            (re.match(rf"^({rule.scope}) · \S", self.label) is not None, "label 以 geo 或国家码加「 · 」开头"),
            (re.match(rf"^{rule.rules}-rules-v[0-9A-Za-z]+；截至 {STAMP_PATTERN}(；|$)", self.note) is not None, "note 以规则版本与「截至 T」开头"),
            (self.value != "" or rule.unobserved is None or rule.unobserved in self.note, f"空值要在 note 里写明「{rule.unobserved}」"),
            (_link_segments_ok(self.note), "联动段的写法不对"),
        )
        refuse("obs_evidence", first_problem(checks), kind=self.kind)
        return self


# ---- frozen_inputs_json (D36) --------------------------------------------------------------------------------------


class RulesRef(Frozen):
    version: RulesVersion
    params: dict[str, Any]


class SliceRef(Frozen):
    dataset: SliceDataset
    pt_date: Day
    version_id: RowId
    shape: SliceShape
    status: SliceStatus
    stale: StrictBool
    first_incomplete_hour: Annotated[str, Field(min_length=1, max_length=40)] | None
    first_incomplete_date: Day | None
    usable_until: Stamp | None
    watermark_absent: StrictBool


class TotalsRef(Frozen):
    a: list[RowId]
    a_prime: list[RowId] | None
    a2_all: list[RowId]
    a2_final: list[RowId]


class VcheckSummary(Frozen):
    requests: Count
    succeeded: Count
    failed: Count
    truncated: Count
    stale: Count
    reused: Count
    regex_overflow: Count


class _FrozenInputsBase(Frozen):
    channel: Channel
    collector_version: CollectorVersion
    source_catalog_batch_id: BatchId
    alias_version: Count
    decisions_version: Count
    market_map_version: MarketMapVersion
    link_rules_version: LinkRulesVersion
    rules: list[RulesRef] = Field(min_length=1)


class FrozenInputsTrends(_FrozenInputsBase):
    channel: Literal["trends"]
    granularity: Granularity
    target_date: Day
    window_end: Stamp


class FrozenInputsGsc(_FrozenInputsBase):
    channel: Literal["gsc"]
    round_id: RoundId
    legacy_snapshot_id: Annotated[str, Field(min_length=1, max_length=128)]
    mirror_version: PositiveCount | None
    slices: list[SliceRef]
    totals: TotalsRef
    vcheck_summary: VcheckSummary


FrozenInputs = Annotated[Union[FrozenInputsTrends, FrozenInputsGsc], Field(discriminator="channel")]  # noqa: UP007
