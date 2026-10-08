"""Synthetic snapshots: publication requires all tables and explicit provenance."""

import copy

import pytest
from pydantic import ValidationError


def snapshot_payload():
    from ggwork_pick.feedback.contracts import TABLES_V1 as TABLES

    return {
        "scan_started_at": "2026-10-07T12:00:00Z",
        "scan_completed_at": "2026-10-07T12:00:08Z",
        "consistency": "bounded_scan",
        "tables": [
            {
                "table_id": table.table_id,
                "fields": [{"field_id": "fldSynthetic", "name": "合成字段", "field_type": "text"}],
                "records": [{"record_id": f"synthetic-{table.key}", "values": {"fldSynthetic": "synthetic"}}],
                "complete": True,
                "pages": 1,
                "source_quality": "unknown",
            }
            for table in TABLES
        ],
    }


def test_snapshot_requires_fifteen_distinct_complete_tables():
    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    payload = snapshot_payload()
    assert len(FeedbackSnapshot.model_validate(payload).tables) == 15
    for mutation in ("missing", "duplicate", "incomplete"):
        changed = copy.deepcopy(payload)
        if mutation == "missing":
            changed["tables"].pop()
        elif mutation == "duplicate":
            changed["tables"][-1] = changed["tables"][0]
        else:
            changed["tables"][0]["complete"] = False
        with pytest.raises(ValidationError):
            FeedbackSnapshot.model_validate(changed)


def test_snapshot_rejects_duplicate_records_unknown_fields_and_naive_scan_time():
    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    payload = snapshot_payload()
    duplicate = copy.deepcopy(payload)
    duplicate["tables"][0]["records"] *= 2
    unknown = copy.deepcopy(payload)
    unknown["tables"][0]["records"][0]["values"]["fldUnlisted"] = "secret"
    naive = copy.deepcopy(payload)
    naive["scan_started_at"] = "2026-10-07T12:00:00"
    for changed in (duplicate, unknown, naive):
        with pytest.raises(ValidationError):
            FeedbackSnapshot.model_validate(changed)


def test_scan_and_source_completeness_are_independent():
    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    payload = snapshot_payload()
    payload["tables"][0]["source_quality"] = "partial"
    snap = FeedbackSnapshot.model_validate(payload)
    assert snap.tables[0].complete is True
    assert snap.source_quality == "partial"


def test_content_hash_ignores_scan_clock_and_record_order_but_not_field_schema():
    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    payload = snapshot_payload()
    snap = FeedbackSnapshot.model_validate(payload)
    payload["scan_started_at"] = "2026-10-07T13:00:00Z"
    payload["scan_completed_at"] = "2026-10-07T13:00:09Z"
    payload["tables"].reverse()
    later = FeedbackSnapshot.model_validate(payload)
    assert snap.content_hash() == later.content_hash()
    payload["tables"][0]["fields"][0]["name"] = "新名"
    assert FeedbackSnapshot.model_validate(payload).content_hash() != snap.content_hash()


def test_output_requires_version_for_success_and_no_fake_freshness_on_pending():
    from ggwork_pick.feedback.contracts import FeedbackReply

    pending = FeedbackReply(status="refresh_pending", notice="刷新中")
    assert pending.feedback_version_id is None
    assert pending.freshness == "unavailable"
    with pytest.raises(ValidationError):
        FeedbackReply(status="ok", notice="not actually versioned")
    with pytest.raises(ValidationError):
        FeedbackReply(status="ok", feedback_version_id="v1", freshness="fresh_scan", scan_started_at="2026-10-07T12:00:00Z", unknown="ignored?")


def test_literal_historical_v1_snapshot_preserves_its_original_hash_and_shape():
    import json
    from pathlib import Path

    from ggwork_pick.feedback.contracts import FeedbackSnapshot

    payload = json.loads((Path(__file__).parent / "fixtures/snapshot_v1.json").read_text(encoding="utf-8"))
    snapshot = FeedbackSnapshot.model_validate(payload)
    assert snapshot.transform_version == "feedback-v1"
    assert snapshot.content_hash() == "d73053f123e751adaab8fc92c89072a6aa2cb9ba8324a5f9a78a009a88322719"
    assert set(snapshot.tables[0].fields[0].model_dump()) == {"field_id", "name", "field_type", "semantic_name", "properties"}
    assert set(snapshot.tables[0].records[0].model_dump()) == {"record_id", "values"}
