"""Bounded, all-table scans. No read failure can be mistaken for an empty source."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from ggwork_pick.feedback.contracts import TABLES, FeedbackSnapshot, SourceField, SourcePage, SourceTable, TableSnapshot

MAX_RECORDS = 50_000
MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 10_000


class FeedbackSourceError(ValueError):
    def __init__(self, code: str):
        # Safe code only: never echo a CLI payload, token or raw financial row.
        if code not in {"incomplete", "source_changed", "schema_changed", "capacity", "auth_required", "unavailable", "refresh_failed"}:
            code = "refresh_failed"
        self.code = code
        super().__init__(code)


class FeedbackSource(Protocol):
    async def fields(self, table: SourceTable) -> list[SourceField]: ...

    async def page(self, table: SourceTable, fields: list[SourceField], offset: int) -> SourcePage: ...


def _quality(fields, records):
    statuses = {field.field_id for field in fields if (field.semantic_name or field.name) in {"采集状态", "同步状态", "数据完整性"}}
    missing = {field.field_id for field in fields if (field.semantic_name or field.name) == "缺失字段"}
    partial = {"partial", "部分", "部分覆盖", "部分缺失", "失败", "异常", "不完整"}
    for record in records:
        if any(record.values.get(field_id) for field_id in missing):
            return "partial"
        for field_id in statuses:
            value = record.values.get(field_id)
            values = value if isinstance(value, list) else [value]
            if any(isinstance(item, str) and item.strip().lower() in partial for item in values):
                return "partial"
    # Absence of a known failure is not evidence of complete upstream collection.
    return "unknown"


async def _scan(source: FeedbackSource) -> FeedbackSnapshot:
    started = datetime.now(UTC)
    tables = []
    total_records, total_bytes = 0, 0
    for table in TABLES:
        fields = await source.fields(table)
        records, seen, offset, revision, total = [], set(), 0, None, None
        for page_number in range(1, MAX_PAGES + 1):
            page = await source.page(table, fields, offset)
            if page.table_id != table.table_id:
                raise FeedbackSourceError("incomplete")
            if page_number == 1:
                revision, total = page.revision, page.total
            elif page.revision != revision or page.total != total:
                raise FeedbackSourceError("source_changed")
            for record in page.records:
                if record.record_id in seen:
                    raise FeedbackSourceError("incomplete")
                seen.add(record.record_id)
                records.append(record)
                total_records += 1
                total_bytes += len(json.dumps(record.model_dump(mode="json"), ensure_ascii=False).encode())
                if total_records > MAX_RECORDS or total_bytes > MAX_BYTES:
                    raise FeedbackSourceError("capacity")
            if not page.has_more:
                if total is not None and len(records) != total:
                    raise FeedbackSourceError("incomplete")
                break
            if not page.records or page.next_offset is None or page.next_offset != offset + len(page.records):
                raise FeedbackSourceError("incomplete")
            offset = page.next_offset
        else:
            raise FeedbackSourceError("capacity")
        after = await source.fields(table)
        before_schema = sorted((field.model_dump(mode="json") for field in fields), key=lambda field: field["field_id"])
        after_schema = sorted((field.model_dump(mode="json") for field in after), key=lambda field: field["field_id"])
        if before_schema != after_schema:
            raise FeedbackSourceError("schema_changed")
        tables.append(
            TableSnapshot(
                table_id=table.table_id,
                fields=fields,
                records=records,
                complete=True,
                pages=page_number,
                revision=revision,
                source_quality=_quality(fields, records),
            )
        )
    return FeedbackSnapshot(scan_started_at=started, scan_completed_at=datetime.now(UTC), consistency="bounded_scan", tables=tables)


def _matching_snapshots(first: FeedbackSnapshot, second: FeedbackSnapshot) -> bool:
    return first.content_hash() == second.content_hash()


async def read_snapshot(source: FeedbackSource) -> FeedbackSnapshot:
    """Two matching complete reads, with at most one retry after concurrent source edits.

    This is a bounded stable scan, not a provider-guaranteed point-in-time transaction.
    Clock, page counts and revisions are excluded from content equality; schemas and facts are not.
    """
    for attempt in range(2):
        try:
            first, second = await _scan(source), await _scan(source)
            if not await asyncio.to_thread(_matching_snapshots, first, second):
                raise FeedbackSourceError("source_changed")
            return second.model_copy(update={"scan_started_at": first.scan_started_at})
        except FeedbackSourceError as exc:
            if exc.code != "source_changed" or attempt == 1:
                raise
        except ValidationError:
            raise FeedbackSourceError("incomplete") from None
    raise FeedbackSourceError("source_changed")
