"""2026-10-09: the Base owner retired three revenue tables and four columns on purpose. Every refresh then failed
with schema_changed and blocked the owner's candidate queries. feedback-v3 is that reviewed reorganisation: the
scan reads the thirteen remaining tables, a v2 baseline may lose exactly the reviewed columns once, and history
keeps its own table set. Synthetic schemas only; no provider calls or real source records."""

import dataclasses
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from feedback.fakes import snapshot_from_rows
from feedback.test_snapshot_contracts import snapshot_payload

NOW = datetime(2026, 10, 10, tzinfo=UTC)
IDENTITY = '["synthetic","001","en"]'
CATALOG = {"identity": IDENTITY, "language": "en", "theater": "ReelShort"}
RETIRED_TABLES = {"cps_auto", "theater_revenue", "revenue_totals"}
REMOVED = {"accounts": {"表现形式", "所属组"}, "posts": {"剧ID（RS Boost）"}, "cps_manual": {"收益汇总表"}}


def v2_fixture():
    """A complete v2 baseline, and the same Base after the reviewed reorganisation."""
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES, SourceField
    from ggwork_pick.feedback.fields import REQUIRED_FIELDS

    baseline, native = {}, {}
    for table in TABLES:
        fields = [
            SourceField(
                field_id=f"fld{table.key}{i}",
                name=name,
                semantic_name=name,
                field_type=sorted(types)[0],
                properties={"link_table": TABLE_BY_KEY["dramas"].table_id} if name == "关联剧集" else {},
            )
            for i, (name, types) in enumerate(REQUIRED_FIELDS[table.key].items())
        ]
        fields += [
            SourceField(field_id=f"fld{table.key}Old{i}", name=name, semantic_name=name, field_type="text")
            for i, name in enumerate(sorted(REMOVED.get(table.key, ())))
        ]
        baseline[table.table_id] = fields
        if table.key not in RETIRED_TABLES:
            native[table.table_id] = [
                {"id": f.field_id, "name": f.name, "type": f.field_type, "property": f.properties} for f in fields if f.name not in REMOVED.get(table.key, ())
            ]
    return baseline, native


def install_schema(monkeypatch, native):
    from ggwork_pick import lark_runner

    def run(_owner, args):
        table_id = args[args.index("--table-id") + 1]
        if table_id not in native:
            return lark_runner.Completed(1, json.dumps({"ok": False, "error": {"type": "api", "message": "not_found"}}), "")
        fields = native[table_id]
        return lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": fields, "total": len(fields)}}), "")

    monkeypatch.setattr(lark_runner, "command_risk", lambda *_a, **_k: "read")
    monkeypatch.setattr(lark_runner, "run_for_user", run)


def test_v3_scans_thirteen_tables_and_history_keeps_its_own_set():
    from ggwork_pick.feedback.contracts import TABLE_BY_ID, TABLE_BY_KEY, TABLES, TABLES_V3, TRANSFORM_VERSION, FeedbackSnapshot, RevenueAggregate

    assert TRANSFORM_VERSION == "feedback-v3"
    assert len(TABLES_V3) == 13
    assert {table.key for table in TABLES} - {table.key for table in TABLES_V3} == RETIRED_TABLES
    # Stored v2 versions and frozen candidate evidence still name the retired tables and lane.
    assert all(TABLE_BY_KEY[key].table_id in TABLE_BY_ID for key in RETIRED_TABLES)
    assert RevenueAggregate(source_lane="cps_auto", grain="drama", currency="USD", metric="commission", amount=None, records=1, missing_records=1)

    payload = snapshot_payload()
    payload["tables"].append(dict(table_id=TABLE_BY_KEY["external_ids"].table_id, fields=[], records=[], complete=True, pages=1))
    assert len(FeedbackSnapshot.model_validate({**payload, "transform_version": "feedback-v2"}).tables) == 16
    with pytest.raises(ValidationError):
        FeedbackSnapshot.model_validate({**payload, "transform_version": "feedback-v3"})
    retired_ids = {TABLE_BY_KEY[key].table_id for key in RETIRED_TABLES}
    current = [table for table in payload["tables"] if table["table_id"] not in retired_ids]
    assert len(FeedbackSnapshot.model_validate({**payload, "tables": current, "transform_version": "feedback-v3"}).tables) == 13
    with pytest.raises(ValidationError):
        FeedbackSnapshot.model_validate({**payload, "tables": current, "transform_version": "feedback-v2"})
    with pytest.raises(ValidationError):
        FeedbackSnapshot.model_validate({**payload, "tables": current[:-1] + [payload["tables"][9]], "transform_version": "feedback-v3"})


def test_v2_baseline_adopts_the_reviewed_removals_once(monkeypatch):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES_V3
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, native = v2_fixture()
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v2")
    upgraded = {table.table_id: source._fields(table) for table in TABLES_V3}
    for table in TABLES_V3:
        kept = [field for field in baseline[table.table_id] if field.name not in REMOVED.get(table.key, ())]
        assert upgraded[table.table_id] == kept

    accounts = TABLE_BY_KEY["accounts"]
    native[accounts.table_id].pop(0)
    # The later reads of the same scan are strict already, and so is every scan after v3 publishes.
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        source._fields(accounts)
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        FeishuFeedbackSource("alice", baseline=upgraded, baseline_transform_version="feedback-v3")._fields(accounts)


def test_a_reviewed_column_that_is_still_there_stays_bound(monkeypatch):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource

    baseline, native = v2_fixture()
    posts = TABLE_BY_KEY["posts"]
    kept = next(field for field in baseline[posts.table_id] if field.name == "剧ID（RS Boost）")
    native[posts.table_id].append({"id": kept.field_id, "name": kept.name, "type": kept.field_type, "property": {}})
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v2")
    assert kept in source._fields(posts)


@pytest.mark.parametrize("mutation", ["other_deleted", "type", "properties", "rebound", "unexpected", "recreated_v2_addition", "name_collision"])
def test_v3_transition_refuses_anything_but_the_reviewed_removals(monkeypatch, mutation):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, native = v2_fixture()
    table = TABLE_BY_KEY["cps_manual"]
    fields = native[table.table_id]
    link = next(field for field in fields if field["name"] == "关联剧集")
    if mutation == "other_deleted":
        fields.pop(0)
    elif mutation == "type":
        fields[0]["type"] = "user"
    elif mutation == "properties":
        fields[0]["property"] = {"changed": True}
    elif mutation == "rebound":
        fields[0]["id"] = "fldReplacement"
    elif mutation == "unexpected":
        fields.append({"id": "fldUnexpected", "name": "备注", "type": "text"})
    elif mutation == "recreated_v2_addition":
        link["id"] = "fldRecreated"
    else:
        fields[0]["name"] = "关联剧集"
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v2")
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        source._fields(table)


def test_a_retired_name_added_back_later_is_not_tracked(monkeypatch):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES_V3
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource

    baseline, native = v2_fixture()
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v2")
    upgraded = {table.table_id: source._fields(table) for table in TABLES_V3}
    accounts = TABLE_BY_KEY["accounts"]
    native[accounts.table_id].append({"id": "fldAgain", "name": "所属组", "type": "select"})
    strict = FeishuFeedbackSource("alice", baseline=upgraded, baseline_transform_version="feedback-v3")
    assert strict._fields(accounts) == upgraded[accounts.table_id]


@pytest.mark.parametrize("problem", ["v2_missing_table", "v3_with_retired_table", "v3_missing_table", "unknown_version"])
def test_v3_transition_requires_a_complete_trusted_baseline(problem):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, _ = v2_fixture()
    version = "feedback-v2"
    if problem == "v2_missing_table":
        baseline.pop(TABLE_BY_KEY["cps_auto"].table_id)
    elif problem == "v3_with_retired_table":
        version = "feedback-v3"
    elif problem == "v3_missing_table":
        version = "feedback-v3"
        for key in (*RETIRED_TABLES, "accounts"):
            baseline.pop(TABLE_BY_KEY[key].table_id)
    else:
        version = "feedback-v4"
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version=version)


def mapped_rows():
    return {
        "dramas": [
            {
                "record_id": "drama-a",
                "剧ID": "SD-A",
                "剧名": "Synthetic Wolf",
                "语言": ["英语"],
                "平台": ["ReelShort"],
                "剧分类": ["狼人"],
                "选剧台剧集ID": IDENTITY,
                "选剧台对应状态": "已确认",
            }
        ],
        "posts": [
            {
                "record_id": "release-0",
                "发布ID": "SR-0",
                "剧": [{"id": "drama-a"}],
                "Post ID": "post-0",
                "视频链接": "https://www.tiktok.com/@synthetic/video/post-0",
                "RS收益": "1.50",
            }
        ],
        "cps_manual": [
            {
                "record_id": "manual-0",
                "关联剧集": [{"id": "drama-a"}],
                "剧场": "ReelShort",
                "数据粒度": "单剧",
                "币种": ["USD"],
                "订单金额": "20.00",
                "分成收益": "12.34",
                "日期": "2026-10-03",
            }
        ],
    }


def test_v3_revenue_is_the_manual_table_and_post_rs_only():
    from ggwork_pick.feedback.normalize import normalize

    data = normalize(snapshot_from_rows(mapped_rows(), transform_version="feedback-v3"))
    assert data.transform_version == "feedback-v3"
    assert {entry.source_lane for entry in data.revenue} == {"cps_manual", "post_rs"}
    assert "cps_manual:manual-0" in data.revenue_resolutions
    assert not [key for key in data.evidence if key.startswith("cps_auto:")]


def test_v3_reads_the_remaining_tables_exactly_like_v2():
    from ggwork_pick.feedback.analytics import analyze_feedback, drama_feedback
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery
    from ggwork_pick.feedback.normalize import normalize

    v2 = snapshot_from_rows(mapped_rows(), transform_version="feedback-v2")
    v3 = snapshot_from_rows(mapped_rows(), transform_version="feedback-v3")
    assert dataclasses.replace(normalize(v3), transform_version="feedback-v2") == normalize(v2)
    replies = [analyze_feedback(snapshot, FeedbackAnalysisQuery(), "fv_synthetic", now=NOW) for snapshot in (v2, v3)]
    assert replies[0].model_dump() == replies[1].model_dump()
    direct = [drama_feedback(snapshot, [CATALOG], "fv_synthetic", now=NOW) for snapshot in (v2, v3)]
    assert direct[0].model_dump() == direct[1].model_dump()
    item = direct[1].items[0]
    assert item.evidence_kind == "direct" and item.metrics["identity_method"] == "confirmed_master"
    assert {(entry.source_lane, str(entry.amount)) for entry in item.revenue if entry.metric == "commission"} == {("cps_manual", "12.34")}


def test_a_stored_v2_version_still_yields_its_auto_cps_lane():
    from ggwork_pick.feedback.normalize import normalize

    rows = mapped_rows()
    rows["cps_auto"] = [
        {"record_id": "auto-0", "关联剧集": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "币种": ["USD"], "分成收益": "9.00", "日期": "2026-10-03"}
    ]
    data = normalize(snapshot_from_rows(rows, transform_version="feedback-v2"))
    assert {entry.source_lane for entry in data.revenue} == {"cps_auto", "cps_manual", "post_rs"}
    assert "cps_auto:auto-0" in data.evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "unreviewed_removal"])
async def test_v3_publication_is_atomic_and_preserves_v2_history(pick_db_url, tmp_path, monkeypatch, mode):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES, FeedbackSnapshot, SourcePage, SourceRecord
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.service import PickService

    baseline, native = v2_fixture()
    if mode == "unreviewed_removal":
        native[TABLE_BY_KEY["commission_rules"].table_id].pop(0)
    install_schema(monkeypatch, native)
    historical = FeedbackSnapshot.model_validate(
        {
            "scan_started_at": "2026-10-09T02:44:15Z",
            "scan_completed_at": "2026-10-09T02:46:39Z",
            "consistency": "bounded_scan",
            "transform_version": "feedback-v2",
            "tables": [
                {
                    "table_id": table.table_id,
                    "fields": baseline[table.table_id],
                    "records": [{"record_id": f"synthetic-{table.key}", "values": {baseline[table.table_id][0].field_id: "synthetic"}}],
                    "complete": True,
                    "pages": 1,
                }
                for table in TABLES
            ],
        }
    )
    historical_hash = historical.content_hash()
    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    constructions = []

    class Source(FeishuFeedbackSource):
        async def fields(self, table):
            return self._fields(table)

        async def page(self, table, fields, offset):
            record = SourceRecord(record_id=f"synthetic-{table.key}", values={fields[0].field_id: "synthetic"})
            return SourcePage(table_id=table.table_id, records=[record], has_more=False)

    def source_factory(owner, stored_baseline, version):
        constructions.append(version)
        return Source(owner, baseline=stored_baseline, baseline_transform_version=version)

    service = FeedbackSyncService(factory, owner_id="alice", enabled=True, source_factory=source_factory)
    try:
        repo = service.repository("alice")
        run = await repo.claim("manual")
        old = await repo.publish(run["id"], historical)
        outcome = await service.refresh("alice", wait_seconds=2)
        assert constructions == ["feedback-v2"]
        current = await repo.current()
        if mode == "ok":
            assert outcome.status == "ok"
            assert current["id"] != old["id"]
            assert current["manifest_json"]["transform_version"] == "feedback-v3"
            published = await repo.snapshot(current["id"])
            assert {table.table_id for table in published.tables} == set(native)
            removed = {name for names in REMOVED.values() for name in names}
            assert not [field.name for table in published.tables for field in table.fields if field.name in removed]
            assert (await service.refresh("alice", wait_seconds=2)).version_id == current["id"]
            assert constructions == ["feedback-v2", "feedback-v3"]
        else:
            assert outcome.status == "schema_changed"
            assert current["id"] == old["id"]
        restored = await repo.snapshot(old["id"])
        assert restored.transform_version == "feedback-v2"
        assert len(restored.tables) == 16
        assert restored.content_hash() == historical_hash
    finally:
        await service.stop()
        await engine.dispose()
