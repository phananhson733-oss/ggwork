"""Additive completion wire contracts. No owner authority or changes to stored result JSON.

See docs/pick-workbench/completion-contract.md for routes and service invariants.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_serializer, model_validator

from ggwork_pick.contracts import DramaInput, StrictInput
from ggwork_pick.feedback.contracts import RevenueObservation
from ggwork_pick.mirror.contracts import ROW_MODELS, GrowthBaseline, PostedStats, RsCounts, Rules, Source

Identifier = Annotated[str, Field(min_length=1, max_length=64)]
Identity = Annotated[str, Field(min_length=1, max_length=512)]
Count = Annotated[StrictInt, Field(ge=0)]
Version = Annotated[StrictInt, Field(ge=1)]
Channel = Literal["youtube", "tiktok", "facebook"]


class QueryPin(StrictInput):
    catalog_batch_id: Identifier
    mirror_version: Version | None
    knowledge_batch_id: Identifier | None
    rule_version: Annotated[str, Field(min_length=1, max_length=128)]
    feedback_version_id: Identifier | None = None


class QueryPeriod(StrictInput):
    kind: Literal["latest", "daily", "weekly"] = "latest"
    value: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None

    @model_validator(mode="after")
    def explicit_period(self):
        if (self.kind == "latest") != (self.value is None):
            raise ValueError("历史期次必须指定日期；latest 不接受日期")
        if self.value is not None:
            date.fromisoformat(self.value)
        return self


class CommonQuery(StrictInput):
    domain: Literal["candidates", "catalog", "rankings", "posted", "rules"]
    scope: Literal["candidate_pool", "full_catalog"]
    pin: QueryPin | None = None
    query: Annotated[str, Field(max_length=200)] | None = None
    source: Annotated[str, Field(max_length=100)] | None = None
    source_id: Annotated[str, Field(max_length=256)] | None = None
    language: Annotated[str, Field(max_length=40)] | None = None
    theater: Annotated[str, Field(max_length=100)] | None = None
    channel: Channel | None = None
    account: Annotated[str, Field(max_length=200)] | None = None
    published_from: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    published_to: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    exclude_posted: StrictBool = False
    exclude_selected: StrictBool = False
    confirmed_eligible_only: StrictBool = True
    exclude_previous: StrictBool = False
    hot_only: StrictBool = False
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    signal_kind: Annotated[str, Field(max_length=20)] | None = None
    period: QueryPeriod = Field(default_factory=QueryPeriod)
    order: Literal["evidence_date", "evidence", "listed", "rank", "title", "published_at"] = "evidence_date"
    offset: Count = 0
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 20
    budget_ms: Annotated[StrictInt, Field(ge=1, le=10000)] = 10000

    posted_filter: Literal["", "no", "yes", "pool"] = ""
    posted_state: Literal["", "pub", "sched", "none", "nomatch"] = ""
    with_off: StrictBool = False
    signal_only: StrictBool = False
    wide: StrictBool = False
    youtube_ok: StrictBool = False
    dated_only: StrictBool = False
    in_use_only: StrictBool = False
    rank: (
        Literal[
            "kd",
            "kw",
            "qc",
            "qr",
            "sm",
            "smd",
            "mg",
            "fh",
            "sh",
            "gh",
            "gn",
            "ghh",
            "dbn",
            "rs_rr",
            "rs_growth",
            "rs_cand",
            "rs_pc",
            "rs_clk",
            "rs_gsc",
            "rs_bill",
            "rs_ledger",
        ]
        | None
    ) = None
    grade: Literal["SSS", "SS", "S", "A", "B", "C", "D"] | None = None
    rs_sort: Literal["rr", "d1", "d7", "dp1", "dp7", "promoters", "publish", "bill", "eff", "gsc", "clicks"] = "rr"
    rs_locale: Annotated[str, Field(max_length=40)] | None = None
    rs_bucket: Literal["0-7", "8-30", "31-90", "91-365", "366+"] | None = None
    legacy_week_label: Annotated[str, Field(max_length=40)] | None = None
    result_id: Identifier | None = None

    @model_validator(mode="after")
    def publication_dates(self):
        for value in (self.published_from, self.published_to):
            if value is not None:
                date.fromisoformat(value)
        if self.published_from and self.published_to and self.published_from > self.published_to:
            raise ValueError("发布起始日期不能晚于结束日期")
        return self


class QueryCounts(StrictInput):
    total: Count
    matched: Count
    returned: Count
    excluded: dict[str, Count] = Field(default_factory=dict)


class QueryRow(StrictInput):
    identity: Identity
    drama: DramaInput
    posted_status: Literal["posted", "not_posted", "unknown"]
    # Complete only for the requested account/channel/window, including archived publications.
    posted_scope_complete: StrictBool
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(default_factory=list, max_length=100)
    exclusion_reasons: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=30)


# Preserve full domain records rather than projecting posted/rank rows into candidate cards.
CatalogRow = ROW_MODELS["catalog_rows"]
CatalogSignal = ROW_MODELS["catalog_signals"]
CatalogPosted = ROW_MODELS["catalog_posted"]
CatalogAccount = ROW_MODELS["catalog_accounts"]
RsRow = ROW_MODELS["rs_rows"]
RsId = ROW_MODELS["rs_ids"]
RsBillOrder = ROW_MODELS["rs_bill_orders"]


class QueryRankRow(StrictInput):
    row_key: Identity
    signal: CatalogSignal
    day_rank: StrictInt | None = None
    day_note: str = ""


class QueryBillRow(StrictInput):
    bill_date: str
    book_id: str
    promotion_type: str
    canonical_id: str | None
    title: str
    locale: str | None
    order_cnt: StrictInt
    source_rows: StrictInt
    same_day_clicks: StrictInt


class QueryBillTotals(StrictInput):
    rows: Count
    merged_rows: Count
    orders: Count
    merged_with_clicks: Count
    rows_with_clicks: Count


class QueryBoardData(StrictInput):
    catalog_rows: list[CatalogRow] = Field(default_factory=list, max_length=200)
    signals: list[CatalogSignal] = Field(default_factory=list, max_length=10000)
    posted: list[CatalogPosted] = Field(default_factory=list, max_length=1000)
    accounts: list[CatalogAccount] = Field(default_factory=list, max_length=1000)
    rs_rows: list[RsRow] = Field(default_factory=list, max_length=200)
    rs_ids: list[RsId] = Field(default_factory=list, max_length=10000)
    bill_orders: list[RsBillOrder] = Field(default_factory=list, max_length=200)
    rules: Rules | None = None
    rs_counts: RsCounts | None = None
    growth_baseline: dict[str, GrowthBaseline] = Field(default_factory=dict)
    sources: dict[str, Source] = Field(default_factory=dict)
    posted_stats: PostedStats | None = None
    rank_rows: list[QueryRankRow] = Field(default_factory=list, max_length=200)
    bill_rows: list[QueryBillRow] = Field(default_factory=list, max_length=200)
    bill_totals: QueryBillTotals | None = None
    effective_sort: str | None = None
    legacy_total: Count | None = None
    rank_limit: Count | None = None

    @field_serializer("catalog_rows", "signals", "posted", "accounts", "rs_rows", "rs_ids", "bill_orders", "rank_rows")
    def source_rows_wire(self, values):
        # Source JSON distinguishes absent optional fields from explicit null.
        return [value.model_dump(mode="json", by_alias=True, exclude_unset=True) for value in values]

    @field_serializer("rules", "rs_counts", "posted_stats")
    def source_meta_wire(self, value):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True) if value is not None else None

    @field_serializer("sources", "growth_baseline")
    def source_map_wire(self, values):
        return {key: value.model_dump(mode="json", by_alias=True, exclude_unset=True) for key, value in values.items()}

    # Ordered page identities; decoration arrays above are not independent pages.
    row_keys: list[Identity] = Field(default_factory=list, max_length=200)


class QueryFacets(StrictInput):
    language_order: list[str] = Field(default_factory=list)
    platforms: dict[str, Count] = Field(default_factory=dict)
    languages: dict[str, Count] = Field(default_factory=dict)
    bases: dict[str, Count] = Field(default_factory=dict)
    posted: dict[Literal["no", "yes", "pool"], Count] = Field(default_factory=dict)
    posted_states: dict[Literal["pub", "sched", "none", "nomatch"], Count] = Field(default_factory=dict)
    ranks: dict[str, Count] = Field(default_factory=dict)
    grades: dict[Literal["SSS", "SS", "S", "A", "B", "C", "D"], Count] = Field(default_factory=dict)


class WeekOption(StrictInput):
    week: Annotated[str, Field(max_length=40)]
    start: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class QueryPeriodOptions(StrictInput):
    days: list[Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]] = Field(default_factory=list)
    weeks: list[WeekOption] = Field(default_factory=list)
    resolution: Literal["latest", "exact", "label", "ambiguous", "missing"]


class QueryResponse(StrictInput):
    contract_version: Literal["pick-completion-v1"] = "pick-completion-v1"
    request: CommonQuery
    pin: QueryPin
    actual_period: QueryPeriod | None
    order_version: Annotated[str, Field(min_length=1, max_length=128)]
    counts: QueryCounts
    truncated: StrictBool
    next_offset: Count | None
    rows: list[QueryRow] = Field(max_length=200)
    board: QueryBoardData | None = None
    facets: QueryFacets | None = None
    period_options: QueryPeriodOptions | None = None
    source_as_of: Annotated[str, Field(max_length=40)] | None
    mirror_synced_at: Annotated[str, Field(max_length=40)] | None
    warnings: list[str] = Field(default_factory=list, max_length=50)


class CompletionError(StrictInput):
    code: Literal[
        "invalid_query",
        "unauthorized",
        "not_found",
        "version_gone",
        "period_missing",
        "source_unavailable",
        "query_timeout",
        "version_conflict",
        "export_blocked",
        "link_conflict",
    ]
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    retryable: StrictBool
    current_version: Version | None = None


class ResultReference(StrictInput):
    result_id: Identifier
    item_ids: list[Identifier] = Field(min_length=1, max_length=20)


class CheckedFact(StrictInput):
    claim: Annotated[str, Field(min_length=1, max_length=4000)]
    status: Literal["confirmed", "unknown", "contradicted"]
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(max_length=100)
    reason: Annotated[str, Field(max_length=1000)]

    @model_validator(mode="after")
    def confirmed_evidence(self):
        if self.status == "confirmed" and not self.evidence_refs:
            raise ValueError("已确认事实必须有证据引用")
        return self


class CheckedPublication(StrictInput):
    thread_id: Identifier
    run_id: Identifier
    message_id: Identifier
    status: Literal["confirmed", "partial", "incomplete"]
    content: Annotated[str, Field(max_length=100000)]
    facts: list[CheckedFact] = Field(max_length=500)
    references: list[ResultReference] = Field(max_length=2)
    checker_version: Annotated[str, Field(min_length=1, max_length=128)]
    correction_count: Annotated[StrictInt, Field(ge=0, le=1)]
    checked_at: Annotated[str, Field(min_length=1, max_length=40)]

    @model_validator(mode="after")
    def confirmed_facts(self):
        if self.status == "confirmed" and any(fact.status != "confirmed" for fact in self.facts):
            raise ValueError("存在未确认事实时不能声明全部已核对")
        return self


class PlanRowInput(StrictInput):
    row_id: Identifier
    identity: Identity
    source_result_id: Identifier
    source_item_id: Identifier
    selection_id: Identifier | None = None
    account: Annotated[str, Field(max_length=200)] | None = None
    channel: Channel | None = None
    local_time: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")] | None = None
    # An explicit fold for a repeated local time. None requires user clarification.
    fold: Annotated[StrictInt, Field(ge=0, le=1)] | None = None
    copy_text: Annotated[str, Field(max_length=4000)] = ""
    note: Annotated[str, Field(max_length=2000)] = ""


class PlanCreate(StrictInput):
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]
    rows: list[PlanRowInput] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_rows(self):
        if len({row.row_id for row in self.rows}) != len(self.rows):
            raise ValueError("计划行标识不能重复")
        return self


class PlanUpdate(PlanCreate):
    expected_version: Version
    timezone_change: Literal["keep_local_time", "keep_instant"] | None = None


class PlanRow(PlanRowInput):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    theater: Annotated[str, Field(max_length=100)]
    language: Annotated[str, Field(min_length=1, max_length=40)]
    source_pin: QueryPin
    scheduled_at: Annotated[str, Field(max_length=40)] | None


class Plan(StrictInput):
    id: Identifier
    version: Version
    title: Annotated[str, Field(min_length=1, max_length=200)]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]
    rows: list[PlanRow] = Field(max_length=100)
    created_at: Annotated[str, Field(min_length=1, max_length=40)]
    updated_at: Annotated[str, Field(min_length=1, max_length=40)]


class PlanVersionCommand(StrictInput):
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    expected_version: Version


class PlanCheck(StrictInput):
    row_id: Identifier
    status: Literal["ready", "blocked"]
    blockers: list[str] = Field(max_length=30)
    warnings: list[str] = Field(max_length=30)
    current_pin: QueryPin | None


class PlanPreview(StrictInput):
    plan: Plan
    preview_id: Identifier
    checked_at: Annotated[str, Field(min_length=1, max_length=40)]
    exportable: StrictBool
    checks: list[PlanCheck] = Field(max_length=100)


class PlanExportCommand(PlanVersionCommand):
    preview_id: Identifier


class PlanExport(StrictInput):
    id: Identifier
    plan_id: Identifier
    plan_version: Version
    preview_id: Identifier
    created_at: Annotated[str, Field(min_length=1, max_length=40)]
    filename: Annotated[str, Field(min_length=1, max_length=200)]
    row_count: Count
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PlanLinkCommand(StrictInput):
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    plan_id: Identifier
    row_id: Identifier
    expected_plan_version: Version
    feedback_version_id: Identifier
    post_key: Identity
    confirmation: Literal["manual"]


class PlanLink(StrictInput):
    id: Identifier
    plan_id: Identifier
    row_id: Identifier
    plan_version: Version
    feedback_version_id: Identifier
    post_key: Identity
    method: Literal["manual", "verified_external_id"]
    status: Literal["confirmed", "needs_review", "conflict"]
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=100)
    created_at: Annotated[str, Field(min_length=1, max_length=40)]


class PlanList(StrictInput):
    items: list[Plan] = Field(max_length=100)
    total: Count
    next_offset: Count | None


class PlanLinkList(StrictInput):
    items: list[PlanLink]


class ReviewQuery(StrictInput):
    feedback_version_id: Identifier | None = None
    account_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    channel: Channel | None = None
    language: Annotated[str, Field(max_length=40)] | None = None
    published_from: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    published_to: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    offset: Count = 0
    limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20


class ReviewPost(StrictInput):
    post_key: Identity
    account_id: Annotated[str, Field(min_length=1, max_length=128)] | None
    channel: Channel | None
    identity: Identity | None
    title: Annotated[str, Field(max_length=500)]
    language: Annotated[str, Field(max_length=40)] | None
    published_at: Annotated[str, Field(max_length=40)] | None
    observed_at: Annotated[str, Field(max_length=40)] | None
    observation_days: Count | None
    requested_observation_days: Annotated[StrictInt, Field(ge=1)]
    window_complete: StrictBool
    views: Count | None
    likes: Count | None
    comments: Count | None
    # Existing owner-private revenue schema preserves currency, metric, source lane and attribution.
    revenue: list[RevenueObservation] = Field(default_factory=list)
    link: PlanLink | None
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=100)


class ReviewPosts(StrictInput):
    status: Literal["ok", "disabled", "unavailable", "auth_required"]
    feedback_version_id: Identifier | None
    scan_completed_at: Annotated[str, Field(max_length=40)] | None
    items: list[ReviewPost] = Field(max_length=50)
    total: Count | None
    next_offset: Count | None
    warnings: list[str] = Field(default_factory=list)


class CheckedMessageMetadata(StrictInput):
    status: Literal["confirmed", "partial", "incomplete"]
    checker_version: Annotated[str, Field(min_length=1, max_length=128)]
    checked_at: Annotated[str, Field(min_length=1, max_length=40)]
    correction_count: Annotated[StrictInt, Field(ge=0, le=1)]


class PickProcessingEvent(StrictInput):
    thread_id: Identifier
    run_id: Identifier
    stage: Literal["querying", "checking", "correcting", "finalizing"]
