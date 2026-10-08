"""Synthetic approved schema transition; no provider calls or real source records."""

import json

import pytest
from pydantic import ValidationError

from feedback.test_snapshot_contracts import snapshot_payload


def schema_fixture():
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES_V1, SourceField
    from ggwork_pick.feedback.fields import REQUIRED_FIELDS_V1, V2_ADDITIONS

    baseline, native = {}, {}
    for table in TABLES_V1:
        fields = [
            SourceField(field_id=f"fldOld{i}", name=name, semantic_name=name, field_type=sorted(types)[0])
            for i, (name, types) in enumerate(REQUIRED_FIELDS_V1[table.key].items())
        ]
        baseline[table.table_id] = fields
        native[table.table_id] = [{"id": f.field_id, "name": f.name, "type": f.field_type, "property": f.properties} for f in fields]
    for key, additions in V2_ADDITIONS.items():
        table = TABLE_BY_KEY[key]
        native.setdefault(table.table_id, []).extend(
            {
                "id": f"fldNew{i}",
                "name": name,
                "type": next(iter(types)),
                **({"link_table": TABLE_BY_KEY["dramas"].table_id} if types == {"link"} else {}),
            }
            for i, (name, types) in enumerate(additions.items())
        )
    return baseline, native


def install_schema(monkeypatch, native):
    from ggwork_pick import lark_runner

    def run(_owner, args):
        fields = native[args[args.index("--table-id") + 1]]
        return lark_runner.Completed(0, json.dumps({"ok": True, "data": {"fields": fields, "total": len(fields)}}), "")

    monkeypatch.setattr(lark_runner, "command_risk", lambda *_a, **_k: "read")
    monkeypatch.setattr(lark_runner, "run_for_user", run)


def test_versioned_table_registry_and_default_v1():
    from ggwork_pick.feedback.contracts import TABLES, TABLES_V1, FeedbackReply, FeedbackSnapshot, SourcePage, TableSnapshot

    assert len(TABLES_V1) == 15
    assert TABLES[:-1] == TABLES_V1
    assert TABLES[-1].table_id == "tblEuEDLaqrcu5Ym"
    assert TABLES[-1].key == "external_ids"
    assert FeedbackSnapshot.model_validate(snapshot_payload()).transform_version == "feedback-v1"
    extra = dict(table_id=TABLES[-1].table_id, fields=[], records=[], complete=True, pages=1)
    assert TableSnapshot.model_validate(extra).table_id == TABLES[-1].table_id
    assert SourcePage(table_id=TABLES[-1].table_id, records=[], has_more=False)
    payload = snapshot_payload()
    with pytest.raises(ValidationError):
        FeedbackSnapshot.model_validate({**payload, "transform_version": "feedback-v2"})
    payload["tables"].append(extra)
    with pytest.raises(ValidationError):
        FeedbackSnapshot.model_validate(payload)
    payload["transform_version"] = "feedback-v2"
    assert len(FeedbackSnapshot.model_validate(payload).tables) == 16
    assert FeedbackReply(status="disabled").contract_version == "feedback-v1"


def test_explicit_v1_baseline_adopts_only_approved_additions_once(monkeypatch):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, native = schema_fixture()
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v1")
    upgraded = {table.table_id: source._fields(table) for table in TABLES}
    for table_id, old in baseline.items():
        by_id = {field.field_id: field for field in upgraded[table_id]}
        assert all(by_id[field.field_id] == field for field in old)
    table = TABLE_BY_KEY["dramas"]
    native[table.table_id].append({"id": "fldUnexpected", "name": "备注", "type": "text"})
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        source._fields(table)
    strict = FeishuFeedbackSource("alice", baseline=upgraded, baseline_transform_version="feedback-v2")
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        strict._fields(table)


@pytest.mark.parametrize("mutation", ["deleted", "type", "properties", "rebound", "unexpected", "wrong_target", "new_type", "missing_new", "name_collision"])
def test_transition_refuses_unapproved_or_changed_columns(monkeypatch, mutation):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, native = schema_fixture()
    table = TABLE_BY_KEY["cps_auto"]
    fields = native[table.table_id]
    if mutation == "deleted":
        fields.pop(0)
    elif mutation == "type":
        fields[0]["type"] = "user"
    elif mutation == "properties":
        fields[0]["property"] = {"changed": True}
    elif mutation == "rebound":
        fields[0]["id"] = "fldReplacement"
    elif mutation == "unexpected":
        fields.append({"id": "fldUnexpected", "name": "同步状态", "type": "text"})
    elif mutation == "wrong_target":
        fields[-1]["link_table"] = TABLE_BY_KEY["accounts"].table_id
    elif mutation == "new_type":
        fields[-1]["type"] = "text"
    elif mutation == "missing_new":
        fields.pop()
    else:
        fields[0]["name"] = "关联剧集"
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v1")
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        source._fields(table)


@pytest.mark.parametrize("key", ["external_ids", "cps_auto", "cps_manual"])
def test_bootstrap_requires_correct_master_link_before_rows(monkeypatch, key):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    _, native = schema_fixture()
    table = TABLE_BY_KEY[key]
    next(field for field in native[table.table_id] if field["name"] == "关联剧集")["link_table"] = "tblWrongTarget"
    install_schema(monkeypatch, native)
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        FeishuFeedbackSource("alice")._fields(table)


def test_v2_bound_ids_cannot_be_rebound_by_recreating_an_approved_column(monkeypatch):
    from ggwork_pick.feedback.contracts import TABLE_BY_KEY, TABLES
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, native = schema_fixture()
    install_schema(monkeypatch, native)
    source = FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v1")
    upgraded = {table.table_id: source._fields(table) for table in TABLES}
    table = TABLE_BY_KEY["dramas"]
    native[table.table_id][-1]["id"] = "fldRecreated"
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        FeishuFeedbackSource("alice", baseline=upgraded, baseline_transform_version="feedback-v2")._fields(table)


def test_transition_requires_complete_trusted_v1_baseline(monkeypatch):
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError

    baseline, _ = schema_fixture()
    baseline.pop(next(iter(baseline)))
    with pytest.raises(FeedbackSourceError, match="schema_changed"):
        FeishuFeedbackSource("alice", baseline=baseline, baseline_transform_version="feedback-v1")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "schema", "page", "second_hash", "last_table_failure"])
async def test_transition_publication_is_atomic_and_preserves_v1_history(pick_db_url, tmp_path, monkeypatch, mode):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.feedback.contracts import FeedbackSnapshot, SourcePage, SourceRecord
    from ggwork_pick.feedback.feishu import FeishuFeedbackSource
    from ggwork_pick.feedback.source import FeedbackSourceError
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.service import PickService

    baseline, native = schema_fixture()
    install_schema(monkeypatch, native)
    payload = snapshot_payload()
    for table in payload["tables"]:
        table["fields"] = baseline[table["table_id"]]
        table["records"] = []
    historical = FeedbackSnapshot.model_validate(payload)
    historical_hash = historical.content_hash()
    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    constructions = []

    class Source(FeishuFeedbackSource):
        visits = 0

        async def fields(self, table):
            if mode == "schema" and table.key == "external_ids":
                raise FeedbackSourceError("schema_changed")
            return self._fields(table)

        async def page(self, table, fields, offset):
            if table.key != "external_ids":
                return SourcePage(table_id=table.table_id, records=[], has_more=False)
            self.visits += 1
            if mode == "last_table_failure":
                raise FeedbackSourceError("unavailable")
            if mode == "page":
                return SourcePage(table_id=table.table_id, records=[], has_more=True, next_offset=1)
            value = str(self.visits) if mode == "second_hash" else "synthetic mapping"
            return SourcePage(
                table_id=table.table_id, records=[SourceRecord(record_id="synthetic-mapping", values={fields[0].field_id: value})], has_more=False
            )

    def source_factory(owner, stored_baseline, version):
        constructions.append(version)
        return Source(owner, baseline=stored_baseline, baseline_transform_version=version)

    service = FeedbackSyncService(factory, owner_id="alice", enabled=True, source_factory=source_factory)
    try:
        repo = service.repository("alice")
        run = await repo.claim("manual")
        old = await repo.publish(run["id"], historical)
        outcome = await service.refresh("alice", wait_seconds=2)
        assert constructions == ["feedback-v1"]
        current = await repo.current()
        if mode == "ok":
            assert outcome.status == "ok"
            assert current["id"] != old["id"]
            assert current["manifest_json"]["transform_version"] == "feedback-v2"
            assert len((await repo.snapshot(current["id"])).tables) == 16
            assert (await service.refresh("alice", wait_seconds=2)).version_id == current["id"]
            assert constructions == ["feedback-v1", "feedback-v2"]
        else:
            assert outcome.status != "ok"
            assert current["id"] == old["id"]
            assert current["content_hash"] == historical_hash
        restored = await repo.snapshot(old["id"])
        assert restored.transform_version == "feedback-v1"
        assert restored.content_hash() == historical_hash
        assert restored.model_dump() == historical.model_dump()
    finally:
        await service.stop()
        await engine.dispose()
