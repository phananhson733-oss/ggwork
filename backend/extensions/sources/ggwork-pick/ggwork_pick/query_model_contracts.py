"""Closed, bounded model/operator projection; the HTTP query contract stays complete."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictFloat, StrictInt

from ggwork_pick.completion_contracts import CommonQuery, QueryCounts, QueryPeriod, QueryPin
from ggwork_pick.contracts import StrictInput

MODEL_PAGE_LIMIT = 20
MODEL_BYTE_LIMIT = 48000
MODEL_SIGNAL_LIMIT = 5


class ProjectionOutput(StrictInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, allow_inf_nan=False)


class ModelSignal(ProjectionOutput):
    kind: Annotated[str, Field(max_length=100)]
    observed_at: Annotated[str, Field(max_length=40)] | None
    value: StrictInt | StrictFloat | Annotated[str, Field(max_length=64)] | None
    rank: StrictInt | None
    grade: Annotated[str, Field(max_length=100)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]


class ModelRankMetric(ProjectionOutput):
    key: Literal["rr", "d1", "d7", "dp1", "dp7", "promoters", "publish", "bill", "eff", "gsc", "clicks"]
    value: Annotated[str, Field(max_length=64)] | None
    current: Annotated[str, Field(max_length=64)] | None
    baseline: Annotated[str, Field(max_length=64)] | None
    denominator: StrictInt | None
    comparison_days: Literal[1, 7] | None
    unit: Literal["source_cents", "people", "source_cents_per_promoter", "timestamp", "rank", "impressions", "clicks"]
    scope: Literal[
        "upstream_platform_rolling_30d",
        "change_in_promoters",
        "change_in_platform_rolling_30d",
        "platform_metric_per_promoter",
        "publication_date",
        "upstream_promoters",
        "source_bill_rank",
        "site_search_impressions",
        "site_outbound_7d",
    ]
    observed_at: Annotated[str, Field(max_length=40)] | None
    baseline_at: Annotated[str, Field(max_length=40)] | None
    verified: bool | None
    reference: Annotated[str, Field(min_length=1, max_length=1024)]


class ModelDramaRow(ProjectionOutput):
    kind: Literal["drama"] = "drama"
    rank_metric: ModelRankMetric | None = None
    identity: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]
    source: Annotated[str, Field(max_length=100)]
    source_id: Annotated[str, Field(max_length=256)]
    title: Annotated[str, Field(max_length=500)]
    language: Annotated[str, Field(max_length=40)]
    theater: Annotated[str, Field(max_length=100)]
    availability: Literal["active", "delisted", "unknown"]
    channel_rules: dict[Literal["youtube", "tiktok", "facebook"], Literal["allowed", "denied", "unknown"]]
    posted_status: Literal["posted", "not_posted", "unknown"]
    posted_scope_complete: bool
    signals: list[ModelSignal] = Field(max_length=MODEL_SIGNAL_LIMIT)
    signal_count: Annotated[StrictInt, Field(ge=0)]
    signals_truncated: bool


class ModelPostedRow(ProjectionOutput):
    kind: Literal["posted"] = "posted"
    identity: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]
    sd: Annotated[str, Field(min_length=1, max_length=512)]
    title: Annotated[str, Field(max_length=500)]
    title_truncated: bool
    archived: bool
    post_count: StrictInt
    sched_count: StrictInt
    last_post_on: Annotated[str, Field(max_length=40)] | None
    accounts: list[Annotated[str, Field(max_length=200)]] = Field(max_length=5)
    account_count: Annotated[StrictInt, Field(ge=0)]
    accounts_truncated: bool


class ModelBillRow(ProjectionOutput):
    kind: Literal["bill"] = "bill"
    identity: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]
    book_id: Annotated[str, Field(max_length=256)]
    canonical_id: Annotated[str, Field(max_length=256)] | None
    bill_date: Annotated[str, Field(max_length=40)]
    title: Annotated[str, Field(max_length=500)]
    title_truncated: bool
    promotion_type: Annotated[str, Field(max_length=100)]
    order_cnt: StrictInt


class ModelRuleRow(ProjectionOutput):
    kind: Literal["rule"] = "rule"
    identity: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]
    platform: Annotated[str, Field(max_length=100)]
    name: Annotated[str, Field(max_length=500)]
    name_truncated: bool
    youtube_rule: Literal["ok", "only", "warn", "no"] | None


class ModelCatalogRecord(ProjectionOutput):
    kind: Literal["catalog_record"] = "catalog_record"
    rank_metric: ModelRankMetric | None = None
    identity: None = None
    row_key: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]
    source_table: Literal["catalog_rows", "rs_rows"]
    source_ref: Annotated[str, Field(min_length=1, max_length=1024)]
    title: Annotated[str, Field(max_length=500)]
    title_truncated: bool
    language: Annotated[str, Field(max_length=40)]
    theater: Annotated[str, Field(max_length=100)]
    listed_on: Annotated[str, Field(max_length=40)] | None
    availability: Literal["unknown", "delisted"]
    eligibility: Literal["unknown"] = "unknown"


ModelQueryRow = Annotated[ModelDramaRow | ModelPostedRow | ModelBillRow | ModelRuleRow | ModelCatalogRecord, Field(discriminator="kind")]


class ModelQueryPage(ProjectionOutput):
    page_limit: Literal[20] = MODEL_PAGE_LIMIT
    byte_limit: Literal[48000] = MODEL_BYTE_LIMIT
    requested_limit: Annotated[StrictInt, Field(ge=1, le=200)]
    shown: Annotated[StrictInt, Field(ge=0, le=20)]
    available_count: Annotated[StrictInt, Field(ge=0)]
    omitted_rows: Annotated[StrictInt, Field(ge=0)]
    signals_omitted: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None
    truncated: bool


class QueryModelProjection(ProjectionOutput):
    projection_version: Literal["pick-query-model-v1"] = "pick-query-model-v1"
    effective_sort: Annotated[str, Field(max_length=40)] | None = None
    rank_limit: Annotated[StrictInt, Field(ge=0)] | None = None
    request: CommonQuery
    pin: QueryPin
    actual_period: QueryPeriod | None
    period_resolution: Literal["latest", "exact", "label", "ambiguous", "missing"] | None
    source_as_of: Annotated[str, Field(max_length=40)] | None
    mirror_synced_at: Annotated[str, Field(max_length=40)] | None
    order_version: Annotated[str, Field(min_length=1, max_length=128)]
    counts: QueryCounts
    query_next_offset: Annotated[StrictInt, Field(ge=0)] | None
    query_truncated: bool
    projection: ModelQueryPage
    rows: list[ModelQueryRow] = Field(max_length=MODEL_PAGE_LIMIT)
