"""Documented NDJSON/manifest shapes, all with synthetic fields and records."""

import json

import pytest


def artifact():
    from ggwork_pick.feedback.contracts import BASE_TOKEN, TABLES

    return {
        "base_token": BASE_TOKEN,
        "table_id": TABLES[0].table_id,
        "records_count": 1,
        "has_more": False,
        "rev": "synthetic-r1",
        "query_context": {"record_scope": "all_records"},
        "columns": {"账号ID": {"field_id": "fldSynthetic", "field_type": "text"}},
    }


def test_manifest_scope_count_and_columns_are_checked():
    from ggwork_pick.feedback.contracts import TABLES, SourceField
    from ggwork_pick.feedback.feishu import parse_export

    fields = [SourceField(field_id="fldSynthetic", name="账号ID", field_type="text")]
    page = parse_export(TABLES[0], fields, '{"record_id":"synthetic-account","账号ID":"synthetic"}\n', artifact())
    assert page.records[0].values == {"fldSynthetic": "synthetic"}
    assert page.revision == "synthetic-r1"


@pytest.mark.parametrize(
    "change", [{"records_count": 2}, {"base_token": "wrong"}, {"query_context": {"record_scope": "view_filtered_records"}}, {"has_more": "false"}]
)
def test_export_cannot_publish_wrong_scope_or_truncated_rows(change):
    from ggwork_pick.feedback.contracts import TABLES, SourceField
    from ggwork_pick.feedback.feishu import parse_export
    from ggwork_pick.feedback.source import FeedbackSourceError

    fields = [SourceField(field_id="fldSynthetic", name="账号ID", field_type="text")]
    with pytest.raises(FeedbackSourceError):
        parse_export(TABLES[0], fields, '{"record_id":"synthetic-account","账号ID":"synthetic"}\n', {**artifact(), **change})


def test_export_preserves_missing_values_and_refuses_unprojected_field():
    from ggwork_pick.feedback.contracts import TABLES, SourceField
    from ggwork_pick.feedback.feishu import parse_export
    from ggwork_pick.feedback.source import FeedbackSourceError

    fields = [SourceField(field_id="fldSynthetic", name="账号ID", field_type="text")]
    page = parse_export(TABLES[0], fields, '{"record_id":"synthetic-account","账号ID":null}\n', artifact())
    assert page.records[0].values["fldSynthetic"] is None
    with pytest.raises(FeedbackSourceError):
        parse_export(TABLES[0], fields, '{"record_id":"synthetic-account","secret":"oops"}\n', artifact())


@pytest.mark.parametrize("stderr_only", [False, True])
def test_auth_error_has_safe_code_and_no_raw_cli_hint(stderr_only):
    from ggwork_pick.feedback.feishu import envelope
    from ggwork_pick.feedback.source import FeedbackSourceError
    from ggwork_pick.lark_runner import Completed

    payload = json.dumps({"ok": False, "error": {"type": "authentication", "message": "private account secret"}})
    completed = Completed(3, "" if stderr_only else payload, payload if stderr_only else "")
    with pytest.raises(FeedbackSourceError) as error:
        envelope(completed)
    assert error.value.code == "auth_required"
    assert "private" not in str(error.value)


def test_field_allowlist_covers_all_sixteen_tables_without_credential_fields():
    from ggwork_pick.feedback.contracts import TABLES
    from ggwork_pick.feedback.fields import FIELD_NAMES

    assert set(FIELD_NAMES) == {table.key for table in TABLES}
    assert "出单剧名（仅统计出单）" in FIELD_NAMES["cps_manual"]
    assert all("密码" not in name and "token" not in name.lower() for names in FIELD_NAMES.values() for name in names)


def test_initial_schema_rejects_a_cps_table_with_only_notes(monkeypatch):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    monkeypatch.setattr(lark_runner, "command_risk", lambda *_args, **_kwargs: "read")
    monkeypatch.setattr(
        lark_runner,
        "run_for_user",
        lambda *_args, **_kwargs: lark_runner.Completed(
            0, json.dumps({"ok": True, "data": {"fields": [{"field_id": "fldNote", "name": "备注", "type": "text"}], "has_more": False}}), ""
        ),
    )
    with pytest.raises(FeedbackSourceError) as error:
        FeishuFeedbackSource("alice")._fields(TABLE_BY_KEY["cps_auto"])
    assert error.value.code == "schema_changed"


@pytest.mark.parametrize("change", ["renamed", "deleted", "type_changed"])
def test_schema_baseline_survives_a_new_source_instance(monkeypatch, change):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, SourceField
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    table = TABLE_BY_KEY["cps_auto"]
    baseline = [SourceField(field_id="fldAmount", name="订单金额", field_type="number")]
    fields = [{"field_id": "fldAmount", "name": "订单金额", "type": "number"}]
    if change == "renamed":
        fields[0]["name"] = "用户充值金额"
    elif change == "deleted":
        fields = []
    else:
        fields[0]["type"] = "text"
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_args, **_kwargs: "read")
    monkeypatch.setattr(
        lark_runner,
        "run_for_user",
        lambda *_args, **_kwargs: lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": fields, "has_more": False}}), ""),
    )
    source = FeishuFeedbackSource("alice", baseline={table.table_id: baseline})
    if change == "renamed":
        result = source._fields(table)
        assert result[0].name == "用户充值金额"
        assert result[0].semantic_name == "订单金额"
    else:
        with pytest.raises(FeedbackSourceError) as error:
            source._fields(table)
        assert error.value.code == "schema_changed"


@pytest.mark.parametrize("totals,expected", [([2, 2], None), ([2, 3], "source_changed"), ([0], "incomplete"), ([True], "incomplete")])
def test_field_total_pagination(monkeypatch, totals, expected):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, SourceField
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    table = TABLE_BY_KEY["accounts"]
    baseline = [SourceField(field_id="fldA", name="账号ID", field_type="text")]
    calls = []

    def run(_owner, args):
        offset = int(args[args.index("--offset") + 1])
        calls.append(offset)
        field = {"id": "fldA" if offset == 0 else "fldB", "name": "账号ID" if offset == 0 else "其他", "type": "text"}
        return lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": [field], "total": totals[len(calls) - 1]}}), "")

    monkeypatch.setattr(lark_runner, "command_risk", lambda *_a, **_k: "read")
    monkeypatch.setattr(lark_runner, "run_for_user", run)
    source = FeishuFeedbackSource("alice", baseline={table.table_id: baseline})
    if expected:
        with pytest.raises(FeedbackSourceError) as error:
            source._fields(table)
        assert error.value.code == expected
    else:
        assert source._fields(table)[0].field_id == "fldA"
        assert calls == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_successful_export_uses_bare_manifest_stdout(monkeypatch, mismatch):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLES, SourceField
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource

    manifest = {**artifact(), "manifest_version": "v1", "format": "ndjson"}
    stdout = {**manifest, "records_count": 2} if mismatch else manifest
    monkeypatch.setattr(
        lark_runner,
        "run_feedback_export",
        lambda *_a: lark_runner.FeedbackExport(
            lark_runner.Completed(0, json.dumps(stdout), ""), '{"record_id":"synthetic-account","账号ID":"synthetic"}\n', json.dumps(manifest)
        ),
    )
    source = FeishuFeedbackSource("alice")
    fields = [SourceField(field_id="fldSynthetic", name="账号ID", field_type="text")]
    if mismatch:
        from ggwork_pick.feedback.source import FeedbackSourceError

        with pytest.raises(FeedbackSourceError, match="incomplete"):
            await source.page(TABLES[0], fields, 0)
    else:
        page = await source.page(TABLES[0], fields, 0)
        assert len(page.records) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_native_link_target_metadata_is_preserved_and_pinned(monkeypatch, changed):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, SourceField
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    table = TABLE_BY_KEY["posts"]
    target = TABLE_BY_KEY["dramas"].table_id
    baseline = [SourceField(field_id="fldDrama", name="剧", field_type="link", properties={"link_table": target})]
    field = {"id": "fldDrama", "name": "剧", "type": "link", "link_table": TABLE_BY_KEY["accounts"].table_id if changed else target}
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_a, **_k: "read")
    monkeypatch.setattr(
        lark_runner, "run_for_user", lambda *_a: lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": [field], "total": 1}}), "")
    )
    source = FeishuFeedbackSource("alice", baseline={table.table_id: baseline})
    if changed:
        with pytest.raises(FeedbackSourceError, match="schema_changed"):
            source._fields(table)
    else:
        assert source._fields(table)[0].properties == {"link_table": target}


@pytest.mark.parametrize("change", ["quality_flag", "renamed", "unrelated"])
def test_bound_schema_detects_new_allowlisted_semantics(monkeypatch, change):
    from ggwork_pick import lark_runner
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    table = TABLE_BY_KEY["observations"]
    fields = [
        {"field_id": "fldPost", "name": "Post ID", "type": "text"},
        {"field_id": "fldDate", "name": "快照日期", "type": "datetime"},
        {"field_id": "fldViews", "name": "播放量", "type": "number"},
    ]
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_a, **_k: "read")
    monkeypatch.setattr(
        lark_runner, "run_for_user", lambda *_a: lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": fields, "total": len(fields)}}), "")
    )
    baseline = FeishuFeedbackSource("alice")._fields(table)
    source = FeishuFeedbackSource("alice", baseline={table.table_id: baseline})
    if change == "quality_flag":
        fields.append({"field_id": "fldMissing", "name": "缺失字段", "type": "text"})
        with pytest.raises(FeedbackSourceError, match="schema_changed"):
            source._fields(table)
        assert set(source.bound_fields[table.table_id]) == {field.field_id for field in baseline}
    else:
        if change == "renamed":
            fields[2]["name"] = "新的播放标题"
        else:
            fields.append({"field_id": "fldSecret", "name": "密码", "type": "text"})
        selected = source._fields(table)
        assert {field.field_id for field in selected} == {field.field_id for field in baseline}
        assert next(field for field in selected if field.field_id == "fldViews").semantic_name == "播放量"
