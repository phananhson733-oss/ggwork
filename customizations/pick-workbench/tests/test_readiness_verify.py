"""Synthetic, hand-calculated acceptance oracle contracts; no product imports."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("readiness_verify", ROOT / "scripts/pick-readiness-verify.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
FIXTURE = Path(__file__).parent / "fixtures/readiness"


def put(root, name, value):
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    for path in FIXTURE.glob("*.json"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    manifest = json.loads((tmp_path / "expectations.json").read_text())
    captures = json.loads((tmp_path / "captures.json").read_text())
    return tmp_path, manifest, captures


def run(bundle):
    root, manifest, captures = bundle
    captures["expectations_sha256"] = put(root, "expectations.json", manifest)
    put(root, "captures.json", captures)
    return checker.verify(root / "expectations.json", root / "captures.json")


def test_hand_calculated_positive(bundle):
    assert run(bundle)["exit_code"] == 0


@pytest.mark.parametrize("mutation", ["language", "extra_filter", "selected", "ancestor", "total", "batch", "missing_failure"])
def test_required_counterexamples_fail(bundle, mutation):
    _, _, cap = bundle
    record = cap["records"][0]
    if mutation == "language":
        record["actual_conditions"]["language"] = "ko"
    elif mutation == "extra_filter":
        record["actual_conditions"]["theater"] = "Synthetic Studio"
    elif mutation in ("selected", "ancestor"):
        record["response"]["items"][0]["identity"] = "selected" if mutation == "selected" else "grandparent"
    elif mutation == "total":
        record["response"]["matched_total"] = 99
    elif mutation == "batch":
        record["source"]["catalog_batch_id"] = "other-batch"
    else:
        cap["attempts"].insert(0, {**cap["attempts"][0], "attempt_id": "failed-first", "run_id": "run-failed", "status": "failed"})
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("mutation", ["missing_state", "bad_hash", "no_semantics", "self_review", "empty_cases", "partial_expected", "unknown_condition"])
def test_incomplete_evidence_cannot_pass(bundle, mutation):
    root, exp, cap = bundle
    if mutation == "missing_state":
        (root / "selections.json").unlink()
    elif mutation == "bad_hash":
        exp["states"]["before"]["selections_before_sha256"] = "0" * 64
    elif mutation == "no_semantics":
        cap["records"][0].pop("semantic_review")
    elif mutation == "self_review":
        cap["records"][0]["semantic_review"]["reviewer"] = "answer-model"
    elif mutation == "empty_cases":
        exp["cases"] = []
    elif mutation == "partial_expected":
        del exp["cases"][0]["expected"]["allowed_condition_sets"][0]["hot_only"]
    else:
        cap["records"][0]["actual_conditions"]["geo"] = "US"
    assert run(bundle)["exit_code"] != 0


def test_empty_selection_is_valid_not_missing(bundle):
    root, exp, cap = bundle
    exp["states"]["before"]["selections_before_sha256"] = put(root, "selections.json", [])
    cap["records"][0]["response"]["matched_total"] = 3
    cap["records"][0]["response"]["items"] = [{"identity": x} for x in ["selected", "a"]]
    assert run(bundle)["exit_code"] == 0


def test_failure_remains_when_retry_passes(bundle):
    root, _, cap = bundle
    failed = copy.deepcopy(cap["records"][0])
    failed.update(attempt_id="first", run_id="first-run", outcome="error")
    cap["records"].insert(0, failed)
    cap["attempts"].insert(0, {**cap["attempts"][0], "attempt_id": "first", "run_id": "first-run", "status": "failed"})
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    failed.pop("browser_evidence")
    failed.pop("performance_evidence")
    assert run(bundle)["exit_code"] == 1


def test_independent_oracle_sort_hot_channel_posted():
    rows = json.loads((FIXTURE / "rows.json").read_text())
    c = {**checker.DEFAULTS, "language": "en", "exclude_previous": False}
    assert [r["identity"] for r in checker.match_rows(rows, c, set())] == ["selected", "grandparent", "parent", "a", "b"]
    assert [r["identity"] for r in checker.match_rows(rows, {**c, "sort": "rank", "signal_kind": "kd"}, set())] == ["b", "a"]
    assert [r["identity"] for r in checker.match_rows(rows, {**c, "channel": "youtube"}, set())] == ["a"]
    assert [r["identity"] for r in checker.match_rows(rows, {**c, "hot_only": True}, set())] == ["a", "b"]
    assert [r["identity"] for r in checker.match_rows(rows, {**c, "exclude_posted": True}, set())] == ["grandparent", "parent", "a", "b"]


def set_type(bundle, kind, expected, response):
    _, exp, cap = bundle
    case = exp["cases"][0]
    case["case_type"] = kind
    case["expected"].update(expected)
    case["expected"]["allowed_actions"] = [kind]
    case["expected"]["expected_outcome"] = kind + "_success"
    record = cap["records"][0]
    record.update(tool_name=kind, outcome=kind + "_success", response=response)
    return case, record


@pytest.mark.parametrize("bad", [False, True])
def test_count_discriminated_contract(bundle, bad):
    _, rec = set_type(bundle, "count", {}, dict(total=2, by_theater={"Synthetic Studio": 2}, by_language={"en": 3 if bad else 2}))
    assert run(bundle)["exit_code"] == (1 if bad else 0)


@pytest.mark.parametrize("bad", [False, True])
def test_detail_frozen_binding(bundle, bad):
    set_type(
        bundle, "detail", dict(result_id="p", item_id="p1", fact_fields=["identity"]), dict(result_id="p", item_id="p1", identity="other" if bad else "parent")
    )
    assert run(bundle)["exit_code"] == (1 if bad else 0)


@pytest.mark.parametrize(
    "kind,expectation,response",
    [
        (
            "clarification",
            dict(missing_parameters=["signal_kind"], allowed_branches=["ask_board"]),
            dict(missing_parameters=["signal_kind"], branch="ask_board"),
        ),
        ("refusal", dict(reason_codes=["RANK_UNAVAILABLE"]), dict(status="refused", reason_code="RANK_UNAVAILABLE")),
    ],
)
def test_clarification_refusal_are_not_empty_queries(bundle, kind, expectation, response):
    _, rec = set_type(bundle, kind, expectation, response)
    assert run(bundle)["exit_code"] == 0
    rec["response"] = {"items": []}
    assert run(bundle)["exit_code"] == 2


@pytest.mark.parametrize("bad", [False, True])
def test_recovery_requires_authoritative_terminal(bundle, bad):
    root, _, _ = bundle
    _, rec = set_type(
        bundle, "recovery", dict(terminal_status="cancelled", saved_result_ids=[]), dict(status="success" if bad else "cancelled", saved_result_ids=[])
    )
    rec["terminal_status"] = "cancelled"
    rec["authoritative_terminal_file"] = "terminal.json"
    rec["authoritative_terminal_sha256"] = put(root, "terminal.json", dict(run_id="synthetic-run", status="cancelled", saved_result_ids=[]))
    assert run(bundle)["exit_code"] == (1 if bad else 0)


@pytest.mark.parametrize("bad", [False, True])
def test_knowledge_citations_bound_to_independent_source(bundle, bad):
    root, exp, _ = bundle
    citation = dict(citation_id="synthetic-cite", source_ref="synthetic:rules", checked_at="2026-10-05", text="Synthetic restriction unknown")
    source = exp["sources"]["catalog"]
    source.update(knowledge_batch_id="synthetic-knowledge", knowledge_file="knowledge.json", knowledge_sha256=put(root, "knowledge.json", [citation]))
    _, rec = set_type(bundle, "knowledge", dict(citation_ids=["synthetic-cite"]), dict(citations=[copy.deepcopy(citation)]))
    rec["source"]["knowledge_batch_id"] = "synthetic-knowledge"
    if bad:
        rec["response"]["citations"][0]["text"] = "Fabricated permission"
    assert run(bundle)["exit_code"] == (1 if bad else 0)


@pytest.mark.parametrize("bad", [False, True])
def test_save_preparation_and_retry_receipts(bundle, bad):
    root, _, _ = bundle
    wanted = dict(result_id="p", item_ids=["p1"], request_id="synthetic-request", note="synthetic note")
    _, rec = set_type(bundle, "prepare_save", wanted, dict(requires_confirmation=True, **{k: v for k, v in wanted.items() if k != "request_id"}))
    for prefix, value in [
        ("selections_after_prepare", [{"identity": "selected"}]),
        ("selections_after", [{"identity": "selected"}, {"identity": "parent"}] + ([{"identity": "parent"}] if bad else [])),
        ("receipts", [{**wanted, "selection_ids": ["synthetic-selection"]}] * 2),
    ]:
        rec[prefix + "_file"] = prefix + ".json"
        rec[prefix + "_sha256"] = put(root, prefix + ".json", value)
    assert run(bundle)["exit_code"] == (1 if bad else 0)


def test_multistep_each_uses_own_state(bundle):
    root, exp, cap = bundle
    first = exp["cases"][0]
    first["steps"] = [{k: copy.deepcopy(v) for k, v in first.items() if k not in ("case_id", "planned_max_runs")}]
    first["steps"][0]["step_id"] = "first"
    first["steps"].append({**copy.deepcopy(first["steps"][0]), "step_id": "second", "state_key": "empty"})
    exp["states"]["empty"] = copy.deepcopy(exp["states"]["before"])
    exp["states"]["empty"]["selections_before_file"] = "empty.json"
    exp["states"]["empty"]["selections_before_sha256"] = put(root, "empty.json", [])
    cap["records"][0]["step_id"] = "first"
    cap["attempts"][0]["step_id"] = "first"
    cap["records"].append({**copy.deepcopy(cap["records"][0]), "step_id": "second", "attempt_id": "second", "tool_call_id": "second"})
    cap["records"][1]["response"].update(items=[{"identity": "selected"}, {"identity": "a"}], matched_total=3)
    cap["attempts"].append({**cap["attempts"][0], "step_id": "second", "attempt_id": "second"})
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    assert run(bundle)["exit_code"] == 0


def test_source_metadata_hash_is_independent(bundle):
    root, _, _ = bundle
    put(root, "metadata.json", {})
    assert run(bundle)["exit_code"] == 2


def test_missing_date_sorts_last():
    rows = json.loads((FIXTURE / "rows.json").read_text())
    rows[0]["signals"] = []
    assert checker.match_rows(rows, checker.DEFAULTS, set())[-1]["identity"] == "selected"


def test_zero_diagnosis_is_recomputed(bundle):
    _, exp, cap = bundle
    c = exp["cases"][0]["expected"]["allowed_condition_sets"][0]
    c["query"] = "no such synthetic title"
    cap["records"][0]["actual_conditions"] = copy.deepcopy(c)
    cap["records"][0]["raw_arguments"]["filters"] = copy.deepcopy(c)
    cap["records"][0]["response"].update(
        items=[],
        matched_total=0,
        zero_diagnosis={
            "catalog_rows": 5,
            "delisted_rows": 0,
            "without_each": [
                {"condition": "language", "value": "en", "matched_total": 0},
                {"condition": "query", "value": "no such synthetic title", "matched_total": 2},
                {"condition": "excluded", "value": 3, "matched_total": 0},
            ],
        },
    )
    assert run(bundle)["exit_code"] == 0
    cap["records"][0]["response"]["zero_diagnosis"]["without_each"][1]["matched_total"] = 3
    assert run(bundle)["exit_code"] == 1


def test_failed_outcome_with_missing_response_still_fails(bundle):
    _, _, cap = bundle
    cap["records"][0].update(outcome="error", response=None)
    assert run(bundle)["exit_code"] == 1


def test_wrong_capture_rows_hash_cannot_pass(bundle):
    _, _, cap = bundle
    cap["records"][0]["source"]["rows_sha256"] = "0" * 64
    assert run(bundle)["exit_code"] == 2


def test_dynamic_state_is_sealed_before_its_step(bundle):
    _, exp, cap = bundle
    cap["states"] = {"before": exp["states"]["before"]}
    exp["states"]["before"] = {"owner_id": "synthetic-owner", "capture_before_step": {"case_id": "SYNTH-Q14", "step_id": "main"}}
    assert run(bundle)["exit_code"] == 0
    cap["states"]["before"]["captured_at"] = "2026-10-05T00:00:03Z"
    assert run(bundle)["exit_code"] == 2


def test_expectations_locked_before_capture(bundle):
    _, exp, _ = bundle
    exp["locked_at"] = "2026-10-06T00:00:00Z"
    assert run(bundle)["exit_code"] == 2


def test_raw_wrong_language_fails_even_with_correct_effective_capture(bundle):
    _, _, cap = bundle
    cap["records"][0]["raw_arguments"]["filters"]["language"] = "ko"
    assert run(bundle)["exit_code"] == 1
