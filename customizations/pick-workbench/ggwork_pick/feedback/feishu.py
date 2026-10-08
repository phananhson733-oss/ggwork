"""Owner-isolated CLI adapter. Reject unknown export envelopes rather than guess facts."""

import json

from pydantic import ValidationError

from ggwork_pick import lark_runner
from ggwork_pick.feedback.contracts import BASE_TOKEN, SourceField, SourcePage, SourceRecord, SourceTable
from ggwork_pick.feedback.fields import FIELD_NAMES, REQUIRED_FIELDS
from ggwork_pick.feedback.source import FeedbackSourceError


def envelope(completed: lark_runner.Completed) -> dict:
    if completed.truncated:
        raise FeedbackSourceError("incomplete")
    try:
        value = json.loads(completed.stdout or (completed.stderr if completed.exit_code != 0 else ""))
    except (ValueError, TypeError):
        raise FeedbackSourceError("refresh_failed") from None
    if not isinstance(value, dict):
        raise FeedbackSourceError("incomplete")
    if completed.exit_code != 0 or value.get("ok") is not True:
        error = value.get("error")
        kind = error.get("type") if isinstance(error, dict) else None
        raise FeedbackSourceError("auth_required" if kind in {"authentication", "authorization", "auth", "config", "permission"} else "refresh_failed")
    return value


def parse_export(table: SourceTable, fields: list[SourceField], ndjson: str, manifest: dict) -> SourcePage:
    try:
        if manifest.get("base_token") != BASE_TOKEN or manifest.get("table_id") != table.table_id:
            raise FeedbackSourceError("incomplete")
        context = manifest.get("query_context", {})
        if not isinstance(context, dict) or context.get("record_scope") != "all_records" or context.get("view_id"):
            raise FeedbackSourceError("incomplete")
        columns = manifest.get("columns")
        if not isinstance(columns, dict):
            raise FeedbackSourceError("schema_changed")
        names = {field.name: field for field in fields}
        if len(names) != len(fields):
            raise FeedbackSourceError("schema_changed")
        for name, field in names.items():
            column = columns.get(name)
            if not isinstance(column, dict) or column.get("field_id") != field.field_id or column.get("field_type") != field.field_type:
                raise FeedbackSourceError("schema_changed")
        result = []
        for line in ndjson.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not set(row).issubset({"record_id", *names}):
                raise FeedbackSourceError("incomplete")
            result.append(SourceRecord(record_id=row.get("record_id"), values={field.field_id: row.get(name) for name, field in names.items()}))
        count = manifest.get("records_count")
        if type(count) is not int or count != len(result):
            raise FeedbackSourceError("incomplete")
        revision = manifest.get("rev")
        return SourcePage(
            table_id=table.table_id,
            records=result,
            has_more=manifest.get("has_more"),
            next_offset=manifest.get("next_offset"),
            revision=str(revision) if revision is not None else None,
        )
    except (ValueError, TypeError, AttributeError, ValidationError) as exc:
        if isinstance(exc, FeedbackSourceError):
            raise
        raise FeedbackSourceError("incomplete") from None


class FeishuFeedbackSource:
    def __init__(self, owner_id: str, *, baseline: dict[str, list[SourceField]] | None = None):
        if not owner_id or owner_id in ("default", "system:shared"):
            raise ValueError("缺少反馈用户身份")
        self.owner_id = owner_id
        # Only server-loaded last-published schema supplies the baseline, never model parameters.
        self.bound_fields = {table_id: {field.field_id: field for field in fields} for table_id, fields in (baseline or {}).items()}

    async def fields(self, table: SourceTable) -> list[SourceField]:
        try:
            return await lark_runner.in_lark_thread(self._fields, table)
        except (lark_runner.LarkUnavailable, TimeoutError):
            raise FeedbackSourceError("unavailable") from None

    def _fields(self, table: SourceTable) -> list[SourceField]:
        if lark_runner.command_risk(("base", "+field-list")) != "read":
            raise FeedbackSourceError("unavailable")
        offset, seen, all_fields = 0, set(), []
        schema_total = None
        for _ in range(100):
            args = (
                "base",
                "+field-list",
                "--base-token",
                BASE_TOKEN,
                "--table-id",
                table.table_id,
                "--offset",
                str(offset),
                "--limit",
                "200",
                "--format",
                "json",
                "--as",
                "user",
            )
            data = envelope(lark_runner.run_for_user(self.owner_id, args)).get("data")
            if not isinstance(data, dict):
                raise FeedbackSourceError("schema_changed")
            items = data.get("fields", data.get("items"))
            if not isinstance(items, list):
                raise FeedbackSourceError("schema_changed")
            if "total" in data:
                total = data["total"]
                if type(total) is not int or total < offset + len(items):
                    raise FeedbackSourceError("incomplete")
                if schema_total is not None and total != schema_total:
                    raise FeedbackSourceError("source_changed")
                schema_total = total
                has_more = offset + len(items) < total
                next_offset = offset + len(items)
                if "has_more" in data and (type(data["has_more"]) is not bool or data["has_more"] != has_more):
                    raise FeedbackSourceError("incomplete")
                if "next_offset" in data and has_more and data["next_offset"] != next_offset:
                    raise FeedbackSourceError("incomplete")
            else:
                if schema_total is not None or type(data.get("has_more")) is not bool:
                    raise FeedbackSourceError("schema_changed")
                has_more, next_offset = data["has_more"], data.get("next_offset")
            for item in items:
                if not isinstance(item, dict):
                    raise FeedbackSourceError("schema_changed")
                try:
                    field = SourceField(
                        field_id=item.get("field_id", item.get("id")),
                        name=item.get("name", item.get("field_name")),
                        field_type=item.get("type", item.get("field_type")),
                        properties=(item.get("property", item.get("properties", {})) or {})
                        | {key: item[key] for key in ("link_table", "expression", "from", "select", "where", "aggregate") if key in item},
                    )
                except (ValidationError, TypeError):
                    raise FeedbackSourceError("schema_changed") from None
                if field.field_id in seen:
                    raise FeedbackSourceError("incomplete")
                seen.add(field.field_id)
                all_fields.append(field)
            if not has_more:
                break
            if type(next_offset) is not int or next_offset != offset + len(items) or not items:
                raise FeedbackSourceError("incomplete")
            offset = next_offset
        else:
            raise FeedbackSourceError("capacity")
        bound = self.bound_fields.get(table.table_id)
        if bound is None:
            selected = [field for field in all_fields if field.name in FIELD_NAMES[table.key]]
            by_name = {field.name: field for field in selected}
            required = REQUIRED_FIELDS[table.key]
            if len(by_name) != len(selected) or any(name not in by_name or by_name[name].field_type not in types for name, types in required.items()):
                raise FeedbackSourceError("schema_changed")
            selected = [field.model_copy(update={"semantic_name": field.name}) for field in selected]
            self.bound_fields[table.table_id] = {field.field_id: field for field in selected}
        else:
            if any(field.field_id not in bound and field.name in FIELD_NAMES[table.key] for field in all_fields):
                raise FeedbackSourceError("schema_changed")
            selected = [field for field in all_fields if field.field_id in bound]
            if len(selected) != len(bound) or any(
                field.field_type != bound[field.field_id].field_type or field.properties != bound[field.field_id].properties for field in selected
            ):
                raise FeedbackSourceError("schema_changed")
            selected = [field.model_copy(update={"semantic_name": bound[field.field_id].semantic_name or bound[field.field_id].name}) for field in selected]
        return selected

    async def page(self, table: SourceTable, fields: list[SourceField], offset: int) -> SourcePage:
        try:
            result = await lark_runner.in_lark_thread(
                lark_runner.run_feedback_export, self.owner_id, table.table_id, tuple(field.field_id for field in fields), offset
            )
            if result.completed.truncated:
                raise FeedbackSourceError("incomplete")
            if result.completed.exit_code != 0:
                envelope(result.completed)
            manifest = json.loads(result.manifest)
            if not isinstance(manifest, dict):
                raise FeedbackSourceError("incomplete")
            stdout = json.loads(result.completed.stdout)
            if not isinstance(stdout, dict):
                raise FeedbackSourceError("incomplete")
            if "ok" in stdout:
                envelope(result.completed)
            elif stdout.get("manifest_version") != "v1" or stdout.get("format") != "ndjson":
                raise FeedbackSourceError("incomplete")
            elif any(
                stdout.get(key) != manifest.get(key) for key in ("base_token", "table_id", "rev", "query_context", "records_count", "has_more", "next_offset")
            ):
                raise FeedbackSourceError("incomplete")
            return parse_export(table, fields, result.records, manifest)
        except (lark_runner.LarkUnavailable, TimeoutError):
            raise FeedbackSourceError("unavailable") from None
        except (ValueError, TypeError) as exc:
            if isinstance(exc, FeedbackSourceError):
                raise
            raise FeedbackSourceError("incomplete") from None
