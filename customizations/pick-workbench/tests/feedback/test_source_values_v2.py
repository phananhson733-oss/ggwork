"""Exact v2 source values survive the actual adapter/scan/persistence boundaries."""

import json

import pytest
from pydantic import ValidationError

from ggwork_pick.feedback.contracts import BASE_TOKEN, TABLE_BY_KEY, FeedbackSnapshot, SourceField, SourceRecord
from ggwork_pick.feedback.feishu import FeishuFeedbackSource, parse_export
from ggwork_pick.feedback.source import read_snapshot

from .test_schema_v2 import install_schema, schema_fixture


@pytest.mark.parametrize("value", [" 001Ab ", [" 001Ab "], {" label ": [" 001Ab ", 0, None]}])
def test_v2_record_values_are_exact_while_legacy_v1_still_strips(value):
    from ggwork_pick.feedback.contracts import SourceRecordV2

    payload = {"record_id": " rec ", "values": {"field": value}}
    v2 = SourceRecordV2.model_validate(payload)
    assert v2.record_id == "rec"
    assert v2.values["field"] == value
    assert SourceRecordV2.model_validate_json(v2.model_dump_json()).values == v2.values
    assert SourceRecord.model_validate(payload).values != v2.values


@pytest.mark.parametrize("value", ["bad\x00value", "bad\ud800value", float("nan"), object()])
def test_v2_exact_values_retain_storage_and_json_validation(value):
    from ggwork_pick.feedback.contracts import SourceRecordV2

    with pytest.raises(ValidationError):
        SourceRecordV2(record_id="rec", values={"field": value})


def test_parse_export_defaults_to_v1_and_explicit_v2_keeps_raw_text():
    table = TABLE_BY_KEY["external_ids"]
    fields = [SourceField(field_id="fldExternal", name="外部ID", field_type="text")]
    manifest = {
        "base_token": BASE_TOKEN,
        "table_id": table.table_id,
        "records_count": 1,
        "has_more": False,
        "query_context": {"record_scope": "all_records"},
        "columns": {"外部ID": {"field_id": "fldExternal", "field_type": "text"}},
    }
    records = json.dumps({"record_id": "mapping-a", "外部ID": " 001Ab "})
    assert parse_export(table, fields, records, manifest).records[0].values["fldExternal"] == "001Ab"
    assert parse_export(table, fields, records, manifest, transform_version="feedback-v2").records[0].values["fldExternal"] == " 001Ab "


@pytest.mark.asyncio
async def test_real_adapter_pages_scan_publish_and_rebuild_keep_exact_v2_values(pick_db_url, tmp_path, monkeypatch):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import SourcePageV2
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.service import PickService

    _, native = schema_fixture()
    install_schema(monkeypatch, native)
    table = TABLE_BY_KEY["external_ids"]
    by_name = {field["name"]: field["id"] for field in native[table.table_id]}
    external_value = " 001Ab "
    observed_offsets = []

    def export(_owner, table_id, field_ids, offset):
        records = []
        has_more = False
        if table_id == table.table_id:
            observed_offsets.append(offset)
            records = [
                {"record_id": f"mapping-{offset}", "外部ID": external_value, "适用范围": "账号: 007 ", "确认人": [{"id": " user ", "name": " Auditor "}]}
            ]
            has_more = offset == 0
        manifest = {
            "base_token": BASE_TOKEN,
            "table_id": table_id,
            "records_count": len(records),
            "has_more": has_more,
            "next_offset": 1 if has_more else None,
            "query_context": {"record_scope": "all_records"},
            "columns": {field["name"]: {"field_id": field["id"], "field_type": field["type"]} for field in native[table_id] if field["id"] in field_ids},
        }
        return lark_runner.FeedbackExport(lark_runner.Completed(0, '{"ok":true}', ""), "\n".join(json.dumps(row) for row in records), json.dumps(manifest))

    monkeypatch.setattr(lark_runner, "run_feedback_export", export)
    source = FeishuFeedbackSource("alice")
    fields = await source.fields(table)
    page = await source.page(table, fields, 0)
    assert page.records[0].values[by_name["外部ID"]] == external_value
    assert SourcePageV2.model_validate_json(page.model_dump_json()).records[0].values == page.records[0].values
    scanned = await read_snapshot(source)
    scanned_table = next(item for item in scanned.tables if item.table_id == table.table_id)
    assert len(scanned_table.records) == 2
    assert observed_offsets == [0, 0, 1, 0, 1]
    assert all(record.values[by_name["外部ID"]] == external_value for record in scanned_table.records)
    assert scanned_table.records[0].values[by_name["适用范围"]] == "账号: 007 "
    assert scanned_table.records[0].values[by_name["确认人"]] == [{"id": " user ", "name": " Auditor "}]
    assert FeedbackSnapshot.model_validate_json(scanned.model_dump_json()).model_dump() == scanned.model_dump()
    engine = host_engine(pick_db_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await PickService(tmp_path / "files").initialize(factory)
        repo = FeedbackRepository(factory, "alice")
        run = await repo.claim("manual")
        version = await repo.publish(run["id"], scanned)
        rebuilt = await repo.snapshot(version["id"])
        assert rebuilt.model_dump(mode="json") == scanned.model_dump(mode="json")
        assert rebuilt.content_hash() == scanned.content_hash()
        external_value = "001Ab"
        changed = await read_snapshot(source)
        assert changed.content_hash() != scanned.content_hash()
        assert (await repo.snapshot(version["id"])).model_dump(mode="json") == scanned.model_dump(mode="json")
    finally:
        await engine.dispose()
