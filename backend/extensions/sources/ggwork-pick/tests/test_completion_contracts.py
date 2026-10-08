"""Public completion DTO boundary; fixtures are synthetic, never live sources."""

import pytest
from pydantic import ValidationError


def test_query_rejects_authority_and_accepts_explicit_historical_scope():
    from ggwork_pick.completion_contracts import CommonQuery

    request = {"domain": "rankings", "scope": "full_catalog", "period": {"kind": "daily", "value": "2026-01-01"}, "signal_kind": "kd"}
    parsed = CommonQuery.model_validate(request)
    assert parsed.period.value == "2026-01-01"
    assert parsed.budget_ms == 10000
    with pytest.raises(ValidationError):
        CommonQuery.model_validate({**request, "owner_id": "alice"})
    with pytest.raises(ValidationError):
        CommonQuery.model_validate({**request, "budget_ms": 10001})


def test_checked_final_cannot_claim_success_without_evidence():
    from ggwork_pick.completion_contracts import CheckedPublication

    publication = {
        "thread_id": "t",
        "run_id": "r",
        "message_id": "m",
        "status": "confirmed",
        "content": "80集",
        "facts": [{"claim": "80集", "status": "unknown", "evidence_refs": [], "reason": "缺来源"}],
        "references": [],
        "checker_version": "check-v1",
        "correction_count": 0,
        "checked_at": "2026-10-08T12:00:00+00:00",
    }
    with pytest.raises(ValidationError):
        CheckedPublication.model_validate(publication)
    publication["status"] = "partial"
    assert CheckedPublication.model_validate(publication).facts[0].status == "unknown"


def test_draft_round_trip_keeps_missing_schedule_and_rejects_unknown_authority():
    from ggwork_pick.completion_contracts import PlanCreate

    draft = {
        "request_id": "cmd1",
        "title": "下周",
        "timezone": "America/Chicago",
        "rows": [{"row_id": "row1", "identity": '["synthetic","A","en"]', "source_result_id": "r1", "source_item_id": "i1"}],
    }
    parsed = PlanCreate.model_validate(draft)
    assert parsed.rows[0].local_time is None
    assert parsed.rows[0].account is None
    with pytest.raises(ValidationError):
        PlanCreate.model_validate({**draft, "owner_id": "other"})
    with pytest.raises(ValidationError):
        PlanCreate.model_validate({**draft, "rows": draft["rows"] * 2})


def test_shared_wire_examples_parse_without_losing_fields():
    import json
    from pathlib import Path

    from ggwork_pick import completion_contracts as c

    path = Path(__file__).resolve().parents[3] / "frontend/tests/unit/core/pick/fixtures/completion-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    models = {
        "query": c.CommonQuery,
        "response": c.QueryResponse,
        "publication": c.CheckedPublication,
        "draft": c.PlanCreate,
        "plan": c.Plan,
        "preview": c.PlanPreview,
        "export": c.PlanExport,
        "link": c.PlanLink,
        "error": c.CompletionError,
        "review": c.ReviewPosts,
    }
    for name, model in models.items():
        assert model.model_validate(payload[name]).model_dump(mode="json") == payload[name]
        with pytest.raises(ValidationError):
            model.model_validate({**payload[name], "owner_id": "intruder"})


def test_board_query_preserves_archived_and_unmatched_publication_distinctions():
    from ggwork_pick.completion_contracts import CommonQuery, QueryBoardData

    for state in ("pub", "sched", "none", "nomatch"):
        assert CommonQuery(domain="posted", scope="full_catalog", posted_state=state).posted_state == state
    assert CommonQuery(domain="rankings", scope="full_catalog", rank="rs_ledger", limit=200).rank == "rs_ledger"
    # Reusing the source model means its original archived/publication fields are mandatory, not silently stripped.
    with pytest.raises(ValidationError):
        QueryBoardData(posted=[{"sd": "SD-1", "post_count": 1, "archived": True}])


def test_locked_model_inputs_are_complete_and_fingerprint_matches():
    import hashlib
    import json
    from pathlib import Path

    from ggwork_pick.contracts import DramaInput

    root = Path(__file__).resolve().parents[3] / "docs/pick-workbench/completion"
    raw = (root / "model-acceptance-v1.json").read_bytes()
    cases = json.loads(raw)["cases"]
    fingerprint = json.loads((root / "model-config-fingerprint.json").read_text(encoding="utf-8"))
    assert fingerprint["cases_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(cases) == len({case["id"] for case in cases}) == 20
    for case in cases:
        assert case["prompt"] and case["expected"]
        assert case["setup"]["owner"] == "QA-A"
        for row in case["setup"]["catalog"]:
            DramaInput.model_validate(row)
    assert [period["period"] for period in cases[4]["setup"]["rank_history"]] == ["2026-01-01"]
    next_batch = {row["source_id"]: row["channel_rules"]["youtube"] for row in cases[14]["setup"]["catalog"]}
    assert next_batch["D"] == next_batch["E"] == next_batch["F"] == "allowed"


def test_time_input_rejects_boolean_fold_and_nonexistent_calendar_day():
    from ggwork_pick.completion_contracts import PlanRowInput, QueryPeriod

    with pytest.raises(ValidationError):
        PlanRowInput(row_id="r", identity="i", source_result_id="s", source_item_id="it", fold=True)
    with pytest.raises(ValidationError):
        QueryPeriod(kind="daily", value="2026-99-99")
