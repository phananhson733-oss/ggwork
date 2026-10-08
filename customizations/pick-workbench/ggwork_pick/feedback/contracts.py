"""Feedback query inputs and source facts; runtime authority is never model input."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, StrictBool, StrictInt, StringConstraints, model_validator

from ggwork_pick.contracts import StrictInput

BASE_TOKEN = "OtnsbnRnwaLmnVsJByscTkFMntd"
CONTRACT_VERSION = "feedback-v1"
TRANSFORM_VERSION = "feedback-v2"


@dataclass(frozen=True)
class SourceTable:
    key: str
    table_id: str
    name: str


TABLES_V1 = (
    SourceTable("accounts", "tblHeWrgRPNshRdE", "账号台账"),
    SourceTable("dramas", "tbl4efRfwhJRqryA", "选剧池"),
    SourceTable("observations", "tblNTly7d7tV1jG5", "采集数据"),
    SourceTable("posts", "tbl5Kzrhuz9B7LTE", "发布记录"),
    SourceTable("daily", "tblRLIXEFxLFkmt9", "每日播放趋势"),
    SourceTable("account_daily", "tblFQYFDWi7mDNYr", "每日播放趋势-分账号"),
    SourceTable("drama_totals", "tblMelhKOBsexG6S", "短剧播放数据汇总"),
    SourceTable("release_daily", "tblggih9cjCArHy6", "短剧发布趋势"),
    SourceTable("first_release_daily", "tbl2DP9zxoJCtrLY", "短剧新发趋势-按日去重"),
    SourceTable("cps_auto", "tbl16HKB8myAqlvJ", "CPS账号数据明细"),
    SourceTable("cps_manual", "tbl2ZkOYGw7BVhaE", "CPS账号数据明细 手动版"),
    SourceTable("theater_revenue", "tbl3dOSgrF0UeeQI", "每日剧场收益"),
    SourceTable("revenue_totals", "tbl3BJlPKayLxSV9", "收益汇总表"),
    SourceTable("operator_daily", "tblYCdQgOk5c1QWH", "运营日报汇总"),
    SourceTable("commission_rules", "tblTDKF4IpJdKYkW", "分成比例配置"),
)
TABLES = (*TABLES_V1, SourceTable("external_ids", "tblEuEDLaqrcu5Ym", "外部剧集ID映射"))
TABLE_BY_ID = {table.table_id: table for table in TABLES}
TABLE_BY_KEY = {table.key: table for table in TABLES}

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Count = Annotated[StrictInt, Field(ge=0)]


class FeedbackDetailQuery(StrictInput):
    result_id: Identifier
    item_ids: list[Identifier] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_items(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("条目标识不能重复")
        return self


class FeedbackAnalysisQuery(StrictInput):
    # country remains an explicit request we can answer as unsupported, never an alias for language.
    group_by: Literal["genre", "language", "theater", "country"] = "genre"
    published_from: date | None = None
    published_to: date | None = None
    language: str | None = Field(default=None, max_length=40)
    theater: str | None = Field(default=None, max_length=100)
    channel: Literal["tiktok", "youtube", "facebook"] | None = None
    account_id: str | None = Field(default=None, max_length=128)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=50)] = 20

    @model_validator(mode="after")
    def date_range(self):
        if self.published_from and self.published_to and self.published_from > self.published_to:
            raise ValueError("起始日期不能晚于结束日期")
        return self


class PlaybackObservation(StrictInput):
    post_key: Annotated[str, Field(min_length=1, max_length=512)]
    record_id: Identifier
    drama_record_id: Identifier | None = None
    account_id: Identifier | None = None
    observed_at: datetime | None = None
    published_at: datetime | None = None
    views: Count | None = None
    likes: Count | None = None
    comments: Count | None = None
    saves: Count | None = None
    shares: Count | None = None
    quality: Literal["complete", "partial", "unknown"] = "unknown"


class RevenueObservation(StrictInput):
    record_id: Identifier
    source_lane: Literal["cps_auto", "cps_manual", "post_rs"]
    grain: Literal["drama", "post", "account", "platform", "unknown"]
    currency: Annotated[str, Field(min_length=1, max_length=12)]
    metric: Literal["order_amount", "refund", "commission", "advertising", "brokerage", "bonus", "orders"]
    amount: Decimal | None = None
    drama_record_id: Identifier | None = None
    metric_on: date | None = None
    amount_basis: str | None = Field(default=None, max_length=100)
    attribution: Literal["confirmed", "ambiguous", "unmatched"] = "unmatched"


Quality = Literal["complete", "partial", "unknown"]
FeedbackStatus = Literal["ok", "disabled", "unavailable", "auth_required", "refresh_pending", "refresh_failed", "schema_changed", "unsupported_dimension"]


class SourceField(StrictInput):
    field_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=500)]
    field_type: Annotated[str, Field(min_length=1, max_length=100)]
    semantic_name: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class SourceRecord(StrictInput):
    record_id: Identifier
    values: dict[str, JsonValue]


class SourceRecordV2(SourceRecord):
    # V1 keeps its historical normalization; v2 external IDs and scopes are exact source strings.
    model_config = ConfigDict(str_strip_whitespace=False)
    record_id: Annotated[Identifier, StringConstraints(strip_whitespace=True)]


class SourcePage(StrictInput):
    table_id: Identifier
    records: list[SourceRecord]
    has_more: StrictBool
    next_offset: Count | None = None
    revision: str | None = Field(default=None, max_length=128)
    total: Count | None = None

    @model_validator(mode="after")
    def continuation(self):
        if self.table_id not in TABLE_BY_ID or (self.has_more and self.next_offset is None):
            raise ValueError("来源表或分页坐标无效")
        return self


class SourcePageV2(SourcePage):
    records: list[SourceRecordV2]


class TableSnapshot(StrictInput):
    table_id: Identifier
    fields: list[SourceField]
    records: list[SourceRecord]
    complete: Literal[True]
    pages: Annotated[StrictInt, Field(ge=1)]
    revision: str | None = Field(default=None, max_length=128)
    source_quality: Quality = "unknown"

    @model_validator(mode="after")
    def table_integrity(self):
        field_ids = {field.field_id for field in self.fields}
        if self.table_id not in TABLE_BY_ID or len(field_ids) != len(self.fields):
            raise ValueError("未知表或重复字段")
        if len({record.record_id for record in self.records}) != len(self.records):
            raise ValueError("来源记录重复")
        if any(not set(record.values).issubset(field_ids) for record in self.records):
            raise ValueError("记录包含未声明字段")
        return self


class TableSnapshotV2(TableSnapshot):
    records: list[SourceRecordV2]


class FeedbackSnapshot(StrictInput):
    scan_started_at: AwareDatetime
    scan_completed_at: AwareDatetime
    consistency: Literal["bounded_scan"]
    tables: list[TableSnapshot]
    transform_version: Literal["feedback-v1", "feedback-v2"] = CONTRACT_VERSION

    @model_validator(mode="before")
    @classmethod
    def versioned_records(cls, data):
        # Parse v2 before the legacy table annotation can normalize nested JSON strings.
        # This also covers publication revalidation and reconstruction from stored manifests.
        if isinstance(data, dict) and data.get("transform_version") == "feedback-v2" and isinstance(data.get("tables"), list):
            return {
                **data,
                "tables": [TableSnapshotV2.model_validate(table.model_dump() if isinstance(table, TableSnapshot) else table) for table in data["tables"]],
            }
        return data

    @model_validator(mode="after")
    def complete_scan(self):
        expected = TABLES_V1 if self.transform_version == "feedback-v1" else TABLES
        if len(self.tables) != len(expected) or {table.table_id for table in self.tables} != {table.table_id for table in expected}:
            raise ValueError(f"反馈必须包含{len(expected)}张完整数据表")
        if self.scan_completed_at < self.scan_started_at:
            raise ValueError("扫描时间倒置")
        return self

    @property
    def source_quality(self) -> Quality:
        qualities = {table.source_quality for table in self.tables}
        return "partial" if "partial" in qualities else "unknown" if "unknown" in qualities else "complete"

    def content_hash(self) -> str:
        tables = []
        for table in sorted(self.tables, key=lambda item: item.table_id):
            tables.append(
                {
                    "table_id": table.table_id,
                    "fields": [field.model_dump(mode="json") for field in sorted(table.fields, key=lambda item: item.field_id)],
                    "records": [record.model_dump(mode="json") for record in sorted(table.records, key=lambda item: item.record_id)],
                    "source_quality": table.source_quality,
                }
            )
        body = json.dumps(
            {"transform_version": self.transform_version, "tables": tables}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(body.encode()).hexdigest()


class EvidenceRef(StrictInput):
    table_id: Identifier
    record_id: Identifier
    field_ids: list[Identifier] = Field(default_factory=list)
    metric_as_of: str | None = Field(default=None, max_length=40)
    grain: Literal["drama", "post", "account", "platform", "aggregate", "unknown"] = "unknown"
    attribution: Literal["confirmed", "ambiguous", "unmatched"] = "unmatched"
    source_lane: str = Field(max_length=100)


class FeedbackCoverage(StrictInput):
    dramas: Count = 0
    posts: Count = 0
    measured_posts: Count = 0
    unmatched_posts: Count = 0
    missing_posts: Count = 0

    @model_validator(mode="after")
    def valid_counts(self):
        if max(self.measured_posts, self.unmatched_posts, self.missing_posts) > self.posts:
            raise ValueError("覆盖数量超过帖子总数")
        return self


class RevenueAggregate(StrictInput):
    source_lane: Literal["cps_auto", "cps_manual", "post_rs"]
    grain: Literal["drama", "post", "account", "platform", "unknown"]
    currency: Annotated[str, Field(min_length=1, max_length=12)]
    metric: Literal["order_amount", "refund", "commission", "advertising", "brokerage", "bonus", "orders"]
    amount: Decimal | None
    amount_basis: str | None = Field(default=None, max_length=100)
    records: Count
    missing_records: Count

    @model_validator(mode="after")
    def valid_counts(self):
        if self.missing_records > self.records:
            raise ValueError("缺失收益记录数超过总数")
        return self


class FeedbackItem(StrictInput):
    key: Annotated[str, Field(min_length=1, max_length=512)]
    evidence_kind: Literal["direct", "cohort", "unknown"]
    metrics: dict[str, StrictInt | str | None] = Field(default_factory=dict)
    revenue: list[RevenueAggregate] = Field(default_factory=list)
    coverage: FeedbackCoverage = Field(default_factory=FeedbackCoverage)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FeedbackReply(StrictInput):
    status: FeedbackStatus
    contract_version: Literal["feedback-v1"] = CONTRACT_VERSION
    notice: str = Field(default="", max_length=1000)
    feedback_version_id: Identifier | None = None
    scan_started_at: AwareDatetime | None = None
    scan_completed_at: AwareDatetime | None = None
    last_verified_at: AwareDatetime | None = None
    freshness: Literal["fresh_scan", "historical", "stale", "unavailable"] = "unavailable"
    source_quality: Quality = "unknown"
    query_scope: dict[str, JsonValue] = Field(default_factory=dict)
    items: list[FeedbackItem] = Field(default_factory=list)
    coverage: FeedbackCoverage = Field(default_factory=FeedbackCoverage)
    warnings: list[str] = Field(default_factory=list)
    total_groups: Count = 0
    has_more: StrictBool = False

    @model_validator(mode="after")
    def provenance(self):
        if self.status == "ok" and (
            not self.feedback_version_id or self.scan_started_at is None or self.scan_completed_at is None or self.freshness == "unavailable"
        ):
            raise ValueError("成功反馈必须有数据版本及扫描时间")
        if self.status != "ok" and (self.items or self.freshness == "fresh_scan"):
            raise ValueError("未完成反馈不能伪装最新事实")
        return self
