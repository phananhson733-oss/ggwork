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


def refresh_browser(bundle):
    root, _, cap = bundle
    rec = cap["records"][0]
    browser = json.loads((root / "browser.json").read_text())
    browser.update(result_id=rec["response"].get("result_id", rec["response"].get("id")), source=rec["source"])
    rec["browser_evidence"]["evidence_sha256"] = put(root, "browser.json", browser)


def set_type(bundle, kind, expected, response):
    root, exp, cap = bundle
    case = exp["cases"][0]
    case["case_type"] = kind
    case["expected"].update(expected)
    case["expected"]["allowed_actions"] = [kind]
    case["expected"]["expected_outcome"] = kind + "_success"
    record = cap["records"][0]
    response = {**{k: v for k, v in record["response"].items() if k in ("data_as_of", "rule_version", "ranking_version")}, **response}
    record.update(tool_name=kind, outcome=kind + "_success", response=response)
    if kind == "count":
        record["response"]["conditions"] = copy.deepcopy(record["actual_conditions"])
    if kind == "detail":
        record["raw_arguments"] = {k: expected[k] for k in ("result_id", "item_id")}
    refresh_browser(bundle)

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
    bundle[2]["attempts"][0]["status"] = "cancelled"
    bundle[2]["run_ledger_sha256"] = put(root, "run-ledger.json", bundle[2]["attempts"])
    rec["authoritative_terminal_file"] = "terminal.json"
    rec["authoritative_terminal_sha256"] = put(root, "terminal.json", dict(run_id="synthetic-run", status="cancelled", saved_result_ids=[]))
    assert run(bundle)["exit_code"] == (1 if bad else 0)


@pytest.mark.parametrize("bad", [False, True])
def test_knowledge_citations_bound_to_independent_source(bundle, bad):
    root, exp, _ = bundle
    citation = dict(
        citation_id="synthetic-cite",
        batch_id="synthetic-knowledge",
        source_ref="synthetic:rules",
        checked_at="2026-10-05",
        text="Synthetic restriction unknown",
    )
    source = exp["sources"]["catalog"]
    source.update(knowledge_batch_id="synthetic-knowledge", knowledge_file="knowledge.json", knowledge_sha256=put(root, "knowledge.json", [citation]))
    _, rec = set_type(bundle, "knowledge", dict(citation_ids=["synthetic-cite"]), dict(citations=[copy.deepcopy(citation)]))
    rec["source"]["knowledge_batch_id"] = "synthetic-knowledge"
    rec["source"]["knowledge_sha256"] = source["knowledge_sha256"]
    refresh_browser(bundle)
    if bad:
        rec["response"]["citations"][0]["text"] = "Fabricated permission"
    assert run(bundle)["exit_code"] == (1 if bad else 0)


def save_bundle(bundle, previous_state=None):
    root, exp, cap = bundle
    wanted = dict(result_id="p", item_ids=["p1"], request_id="synthetic-request", note="synthetic note")
    _, rec = set_type(bundle, "prepare_save", wanted, dict(requires_confirmation=True, **{k: v for k, v in wanted.items() if k != "request_id"}))
    rec["raw_arguments"] = {"positions": [1], "note": "synthetic note"}
    item = json.loads((root / "parents.json").read_text())[0]["items"][0]
    saved = dict(
        id="saved-parent",
        owner_id="synthetic-owner",
        identity="parent",
        state="selected",
        version=1,
        source_result_id="p",
        source_item_id="p1",
        note="synthetic note",
        snapshot_json=item,
    )
    previous = []
    status = "created"
    if previous_state:
        old = {**saved, "state": previous_state, "version": 4, "note": "old note", "source_result_id": "old-result"}
        previous = [old]
        status = "existing" if previous_state == "selected" else "restored"
        saved = old if status == "existing" else {**saved, "version": 5}
    exp["states"]["before"]["selections_before_sha256"] = put(root, "selections.json", previous)
    for prefix, value in [
        ("selections_after_prepare", previous),
        ("selections_after", [saved]),
        (
            "receipts",
            [{"request_id": "synthetic-request", "saved": [{"id": saved["id"], "identity": "parent", "status": status, "version": saved["version"]}]}] * 2,
        ),
    ]:
        rec[prefix + "_file"] = prefix + ".json"
        rec[prefix + "_sha256"] = put(root, prefix + ".json", value)
    return rec


@pytest.mark.parametrize("state", [None, "selected", "removed"])
def test_save_created_existing_restored_and_retry(bundle, state):
    save_bundle(bundle, state)
    assert run(bundle)["exit_code"] == 0


@pytest.mark.parametrize(
    "defect", ["note", "receipt_id", "source_result", "source_item", "version", "snapshot", "foreign_owner", "duplicate", "positions", "wrong_result"]
)
def test_save_facts_and_real_receipts_bound(bundle, defect):
    root, _, _ = bundle
    rec = save_bundle(bundle)
    rows = json.loads((root / "selections_after.json").read_text())
    if defect == "receipt_id":
        receipts = json.loads((root / "receipts.json").read_text())
        for receipt in receipts:
            receipt["saved"][0]["id"] = "NONEXISTENT"
        rec["receipts_sha256"] = put(root, "receipts.json", receipts)
    elif defect == "positions":
        rec["raw_arguments"]["positions"] = [2]
    elif defect == "wrong_result":
        rec["raw_arguments"]["result_id"] = "foreign"
    else:
        if defect == "note":
            rows[0]["note"] = "WRONG NOTE"
        elif defect == "source_result":
            rows[0]["source_result_id"] = "wrong"
        elif defect == "source_item":
            rows[0]["source_item_id"] = "wrong"
        elif defect == "version":
            rows[0]["version"] = 99
        elif defect == "snapshot":
            rows[0]["snapshot_json"] = {}
        elif defect == "foreign_owner":
            rows[0]["owner_id"] = "foreign"
        else:
            rows.append(copy.deepcopy(rows[0]))
        rec["selections_after_sha256"] = put(root, "selections_after.json", rows)
    assert run(bundle)["exit_code"] != 0


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
    cap["attempts"].append({**cap["attempts"][0], "step_id": "second", "attempt_id": "second", "tool_call_ids": ["second"]})
    metrics = json.loads((root / "performance.json").read_text())
    metrics["tool_calls"] = 2
    metric_hash = put(root, "performance.json", metrics)
    for record in cap["records"]:
        record["performance_evidence"]["evidence_sha256"] = metric_hash
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
    cap["records"][0]["response"]["conditions"] = copy.deepcopy(c)
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


@pytest.mark.parametrize(
    "defect",
    [
        "ledger_failed",
        "missing_call",
        "foreign_detail",
        "wrong_detail_args",
        "early_record",
        "null_metadata",
        "wrong_frozen_metadata",
        "unrelated_browser",
        "string_metrics",
    ],
)
def test_review_false_pass_regressions(bundle, defect):
    root, exp, cap = bundle
    rec = cap["records"][0]
    if defect == "ledger_failed":
        cap["attempts"][0]["status"] = "failed"
        cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    elif defect == "missing_call":
        cap["attempts"][0]["tool_call_ids"] = ["failed-first-call", "synthetic-call"]
        cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    elif defect in ("foreign_detail", "wrong_detail_args"):
        _, rec = set_type(bundle, "detail", dict(result_id="p", item_id="p1", fact_fields=["identity"]), dict(result_id="p", item_id="p1", identity="parent"))
        rec["raw_arguments"] = dict(result_id="other" if defect == "wrong_detail_args" else "p", item_id="other" if defect == "wrong_detail_args" else "p1")
        if defect == "foreign_detail":
            parents = json.loads((root / "parents.json").read_text())
            parents[0].update(owner_id="foreign-owner", thread_id="foreign-thread")
            exp["states"]["before"]["parent_chain_sha256"] = put(root, "parents.json", parents)
    elif defect == "early_record":
        rec["started_at"] = "2026-10-04T00:00:00Z"
        exp["states"]["before"]["captured_at"] = "2026-10-03T00:00:00Z"
    elif defect == "null_metadata":
        metadata = json.loads((root / "metadata.json").read_text())
        exp["sources"]["catalog"]["metadata_sha256"] = put(root, "metadata.json", {k: None for k in metadata})
    elif defect == "wrong_frozen_metadata":
        rec["response"].update(data_as_of={"source_as_of": "2099-01-01"}, ranking_version="wrong")
    elif defect == "unrelated_browser":
        browser = json.loads((root / "browser.json").read_text())
        browser.update(assertions=[{"name": "unrelated", "passed": True}], artifact_refs=["nonexistent"])
        rec["browser_evidence"]["evidence_sha256"] = put(root, "browser.json", browser)
    else:
        performance = json.loads((root / "performance.json").read_text())
        for key in ("input_tokens", "output_tokens", "elapsed_seconds", "tool_calls", "model_calls"):
            performance[key] = "unknown"
        rec["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", performance)
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("value", [-1, True, "12", {}, None, "unknown"])
def test_performance_metrics_are_numbers_or_explicit_unknown(bundle, value):
    root, _, cap = bundle
    metrics = json.loads((root / "performance.json").read_text())
    metrics["input_tokens"] = value
    cap["records"][0]["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", metrics)
    assert run(bundle)["exit_code"] == 2


def test_browser_missing_artifact_even_with_matching_assertions(bundle):
    root, _, _ = bundle
    (root / "browser-artifact.json").unlink()
    assert run(bundle)["exit_code"] == 2


def test_known_data_failure_not_erased_by_invalid_external_evidence(bundle):
    root, _, cap = bundle
    cap["records"][0]["response"]["matched_total"] = 999
    (root / "browser-artifact.json").unlink()
    assert run(bundle)["exit_code"] == 1


def test_ledger_failed_status_is_fail_not_only_incomplete(bundle):
    root, _, cap = bundle
    cap["attempts"][0]["status"] = "failed"
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    assert run(bundle)["exit_code"] == 1


def test_dynamic_retry_keeps_both_independent_prestates(bundle):
    root, exp, cap = bundle
    state = exp["states"]["before"]
    exp["states"]["before"] = {"owner_id": "synthetic-owner", "capture_before_step": {"case_id": "SYNTH-Q14", "step_id": "main"}}
    rec = cap["records"][0]
    rec["state_capture_key"] = "before:attempt-1"
    second = {**copy.deepcopy(rec), "attempt_id": "attempt-2", "run_id": "second-run", "tool_call_id": "second-call", "state_capture_key": "before:attempt-2"}
    second.pop("browser_evidence")
    second.pop("performance_evidence")
    cap["records"].append(second)
    cap["states"] = {"before:attempt-1": copy.deepcopy(state), "before:attempt-2": copy.deepcopy(state)}
    cap["attempts"].append({**cap["attempts"][0], "attempt_id": "attempt-2", "run_id": "second-run", "tool_call_ids": ["second-call"]})
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    report = run(bundle)
    assert report["exit_code"] == 2  # second attempt has no fabricated browser/performance proof
    assert report["checks"][0]["status"] == "PASS"
    assert report["checks"][1]["layers"]["data_state"]["status"] == "PASS"
    second.pop("state_capture_key")
    assert run(bundle)["exit_code"] == 2


@pytest.mark.parametrize("field", ["data_as_of", "rule_version", "ranking_version"])
def test_missing_actual_metadata_is_unverified(bundle, field):
    _, _, cap = bundle
    cap["records"][0]["response"].pop(field)
    assert run(bundle)["exit_code"] == 2


def test_data_as_of_contradiction_is_failure(bundle):
    _, _, cap = bundle
    cap["records"][0]["response"]["data_as_of"]["source_as_of"] = "2099-01-01"
    assert run(bundle)["exit_code"] == 1


def test_browser_result_binding_is_checked(bundle):
    root, _, cap = bundle
    browser = json.loads((root / "browser.json").read_text())
    browser["result_id"] = "different-result"
    cap["records"][0]["browser_evidence"]["evidence_sha256"] = put(root, "browser.json", browser)
    assert run(bundle)["exit_code"] == 2


@pytest.mark.parametrize("kind", ["detail", "prepare_save"])
def test_bound_parent_other_batch_rejected(bundle, kind):
    root, exp, _ = bundle
    if kind == "detail":
        set_type(bundle, "detail", dict(result_id="p", item_id="p1", fact_fields=["identity"]), dict(result_id="p", item_id="p1", identity="parent"))
    else:
        save_bundle(bundle)
    parents = json.loads((root / "parents.json").read_text())
    parents[0]["catalog_batch_id"] = "OTHER-BATCH"
    exp["states"]["before"]["parent_chain_sha256"] = put(root, "parents.json", parents)
    assert run(bundle)["exit_code"] != 0


def test_response_conditions_contradiction_rejected(bundle):
    _, _, cap = bundle
    cap["records"][0]["response"]["conditions"] = {"language": "ko"}
    assert run(bundle)["exit_code"] != 0


def test_performance_tool_calls_match_inventory(bundle):
    root, _, cap = bundle
    metrics = json.loads((root / "performance.json").read_text())
    metrics["tool_calls"] = 0
    cap["records"][0]["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", metrics)
    assert run(bundle)["exit_code"] != 0


def test_save_unknown_raw_item_cannot_be_filtered_away(bundle):
    rec = save_bundle(bundle)
    rec["raw_arguments"].pop("positions")
    rec["raw_arguments"]["item_ids"] = ["p1", "NONEXISTENT"]
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("value", [2.0, True])
def test_matched_total_strict_int(bundle, value):
    _, _, cap = bundle
    cap["records"][0]["response"]["matched_total"] = value
    if value is True:
        # One-item successful response makes bool/int equality the only defect.
        cap["records"][0]["response"]["items"] = [{"identity": "a"}]
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("field", ["total", "by_theater", "by_language"])
@pytest.mark.parametrize("value", [2.0, True, -1])
def test_count_fields_strict_nonnegative_integers(bundle, field, value):
    _, rec = set_type(bundle, "count", {}, dict(total=2, by_theater={"Synthetic Studio": 2}, by_language={"en": 2}))
    if field == "total":
        rec["response"][field] = value
    else:
        rec["response"][field][next(iter(rec["response"][field]))] = value
    report = run(bundle)
    assert report["exit_code"] == 2
    assert any("nonnegative integer" in error for error in report["errors"])


@pytest.mark.parametrize("field", ["catalog_rows", "delisted_rows", "matched_total", "excluded_value"])
def test_zero_diagnosis_strict_counts(bundle, field):
    _, exp, cap = bundle
    c = exp["cases"][0]["expected"]["allowed_condition_sets"][0]
    c["query"] = "no such synthetic title"
    rec = cap["records"][0]
    rec["actual_conditions"] = copy.deepcopy(c)
    rec["raw_arguments"]["filters"] = copy.deepcopy(c)
    rec["response"].update(
        conditions=copy.deepcopy(c),
        items=[],
        matched_total=0,
        zero_diagnosis={
            "catalog_rows": 5,
            "delisted_rows": 0,
            "without_each": [
                {"condition": "language", "value": "en", "matched_total": 0},
                {"condition": "query", "value": c["query"], "matched_total": 2},
                {"condition": "excluded", "value": 3, "matched_total": 0},
            ],
        },
    )
    diagnosis = rec["response"]["zero_diagnosis"]
    if field in ("catalog_rows", "delisted_rows"):
        diagnosis[field] = float(diagnosis[field])
    elif field == "matched_total":
        diagnosis["without_each"][0]["matched_total"] = False
    else:
        diagnosis["without_each"][2]["value"] = 3.0
    assert run(bundle)["exit_code"] == 2


@pytest.mark.parametrize("field", ["data_as_of", "rule_version", "ranking_version", "source"])
def test_detail_parent_metadata_cannot_disagree(bundle, field):
    root, exp, _ = bundle
    set_type(bundle, "detail", dict(result_id="p", item_id="p1", fact_fields=["identity"]), dict(result_id="p", item_id="p1", identity="parent"))
    parents = json.loads((root / "parents.json").read_text())
    if field == "data_as_of":
        parents[0][field]["source_as_of"] = "2099-01-01"
    elif field == "source":
        parents[0][field]["rows_sha256"] = "0" * 64
    else:
        parents[0][field] = "unsupported"
    exp["states"]["before"]["parent_chain_sha256"] = put(root, "parents.json", parents)
    assert run(bundle)["exit_code"] == 2


def test_save_known_duplicate_raw_ids_follow_product_deduplication(bundle):
    rec = save_bundle(bundle)
    rec["raw_arguments"].pop("positions")
    rec["raw_arguments"]["item_ids"] = ["p1", "p1"]
    assert run(bundle)["exit_code"] == 0


def test_performance_unknown_scope_cannot_pass(bundle):
    root, _, cap = bundle
    metrics = json.loads((root / "performance.json").read_text())
    metrics["scope"] = "step"
    cap["records"][0]["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", metrics)
    assert run(bundle)["exit_code"] == 2


def compound_bundle(bundle):
    root, exp, cap = bundle
    step = exp["cases"][0]
    query = cap["records"][0]
    count = copy.deepcopy(query)
    count.update(tool_name="pick_count_candidates", tool_call_id="count-call", outcome="count_success")
    count["response"] = {k: v for k, v in query["response"].items() if k in ("conditions", "data_as_of")}
    count["response"].update(total=2, by_theater={"Synthetic Studio": 2}, by_language={"en": 2})
    count_expected = {**copy.deepcopy(step["expected"]), "allowed_actions": ["pick_count_candidates"], "expected_outcome": "count_success"}
    step["tool_contracts"] = [
        {"tool_name": "pick_count_candidates", "case_type": "count", "expected": count_expected, "min_occurrences": 1},
        {"tool_name": "pick_query_candidates", "case_type": "query", "expected": copy.deepcopy(step["expected"]), "min_occurrences": 1},
    ]
    browser = json.loads((root / "browser.json").read_text())
    browser["result_id"] = None
    count["browser_evidence"] = {"evidence_file": "count-browser.json", "evidence_sha256": put(root, "count-browser.json", browser)}
    perf = json.loads((root / "performance.json").read_text())
    perf["tool_calls"] = 2
    perf_hash = put(root, "performance.json", perf)
    query["performance_evidence"]["evidence_sha256"] = perf_hash
    count["performance_evidence"]["evidence_sha256"] = perf_hash
    cap["records"] = [count, query]
    cap["attempts"][0]["tool_call_ids"] = ["count-call", "synthetic-call"]
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    return step


def terminal_bundle(bundle, kind="clarification"):
    root, exp, cap = bundle
    step = exp["cases"][0]
    step["case_type"] = kind
    rubric = "Ask for signal_kind before rank." if kind == "clarification" else "Report the authoritative terminal without claiming a save."
    step["expected"].update(
        allowed_actions=["terminal"],
        expected_outcome="terminal",
        semantic_rubric=[rubric],
        missing_parameters=["signal_kind"],
        clarification_rubric=rubric,
        saved_result_ids=[],
    )
    rec = cap["records"][0]
    for field in ("tool_call_id", "tool_name", "raw_arguments", "actual_conditions", "response", "source", "outcome", "bound_result_id"):
        rec.pop(field, None)
    rec.update(record_kind="terminal", answer="Which board?" if kind == "clarification" else "Run completed without a save.")
    answer_hash = hashlib.sha256(rec["answer"].encode()).hexdigest()
    rec["semantic_review"].update(
        answer_sha256=answer_hash,
        judgments=[
            {
                "rubric": rubric,
                "status": "PASS",
                "reason": "Synthetic independent reading agrees with the locked criterion.",
                "fact_refs": ["terminal-authority.json"],
            }
        ],
    )
    rec["authoritative_terminal_file"] = "terminal-authority.json"
    rec["authoritative_terminal_sha256"] = put(
        root, "terminal-authority.json", dict(run_id=rec["run_id"], status="success", answer_sha256=answer_hash, saved_result_ids=[])
    )
    browser = json.loads((root / "browser.json").read_text())
    browser.update(result_id=None, source=None)
    rec["browser_evidence"]["evidence_sha256"] = put(root, "browser.json", browser)
    perf = json.loads((root / "performance.json").read_text())
    perf["tool_calls"] = 0
    rec["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", perf)
    cap["attempts"][0]["tool_call_ids"] = []
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    return step, rec


def test_compound_count_then_query_uses_locked_types(bundle):
    compound_bundle(bundle)
    assert run(bundle)["exit_code"] == 0


@pytest.mark.parametrize("kind", ["clarification", "recovery"])
def test_terminal_only_has_no_fake_tool_message(bundle, kind):
    terminal_bundle(bundle, kind)
    assert run(bundle)["exit_code"] == 0


def test_save_first_real_request_id_sealed_without_rewrite(bundle):
    root, exp, _ = bundle
    rec = save_bundle(bundle)
    exp["cases"][0]["expected"]["request_id"] = {"capture_before_dispatch": True}
    request = {"request_id": "synthetic-request", "result_id": "p", "item_ids": ["p1"], "note": "synthetic note"}
    for key, value in [
        ("save_dispatch", {"captured_at": "2026-10-05T00:00:02Z", "request": request}),
        ("save_requests", [{"dispatched_at": "2026-10-05T00:00:03Z", "request": request}, {"dispatched_at": "2026-10-05T00:00:04Z", "request": request}]),
    ]:
        rec[key + "_file"] = key + ".json"
        rec[key + "_sha256"] = put(root, key + ".json", value)
    assert run(bundle)["exit_code"] == 0


def query_detail_bundle(bundle):
    root, exp, cap = bundle
    step = compound_bundle(bundle)
    query = cap["records"][1]
    query["response"]["items"] = [{"identity": "a", "item_id": "qa"}, {"identity": "b", "item_id": "qb"}]
    source_rows = {row["identity"]: row for row in json.loads((root / "rows.json").read_text())}
    for item in query["response"]["items"]:
        item["evidence"] = [{**signal, "citation_id": f"{item['item_id']}:{index}"} for index, signal in enumerate(source_rows[item["identity"]]["signals"], 1)]
    detail = copy.deepcopy(query)
    detail.update(
        tool_name="pick_get_drama_detail",
        tool_call_id="detail-call",
        outcome="detail_success",
        raw_arguments={"result_id": "synthetic-result", "item_id": "qa"},
    )
    detail["response"] = {"result_id": "synthetic-result", "item_id": "qa", "identity": "a", "data_as_of": query["response"]["data_as_of"]}
    detail_expected = {
        **copy.deepcopy(step["expected"]),
        "allowed_actions": ["pick_get_drama_detail"],
        "expected_outcome": "detail_success",
        "result_id": "synthetic-result",
        "item_id": "qa",
        "fact_fields": ["identity"],
    }
    step["tool_contracts"] = [
        step["tool_contracts"][1],
        {"tool_name": "pick_get_drama_detail", "case_type": "detail", "expected": detail_expected, "min_occurrences": 1},
    ]
    parent = json.loads((root / "parents.json").read_text())[0]
    parent.update(id="synthetic-result", parent_result_id=None, created_at=query["started_at"], items=copy.deepcopy(query["response"]["items"]))
    detail["bound_result_file"] = "new-bound-result.json"
    detail["bound_result_sha256"] = put(root, "new-bound-result.json", {"captured_at": "2026-10-05T00:00:04Z", "result": parent})
    cap["records"] = [query, detail]
    cap["attempts"][0]["tool_call_ids"] = ["synthetic-call", "detail-call"]
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    return step, detail


def test_query_then_detail_binds_new_immutable_output(bundle):
    query_detail_bundle(bundle)
    assert run(bundle)["exit_code"] == 0


def test_symbolic_binding_is_prelocked_by_producer_and_position(bundle):
    step, detail = query_detail_bundle(bundle)
    expected = step["tool_contracts"][1]["expected"]
    expected.update(result_id={"from_tool": "pick_query_candidates", "occurrence": 1}, item_id={"position": 1})
    assert run(bundle)["exit_code"] == 0
    detail["raw_arguments"]["item_id"] = "qb"
    assert run(bundle)["exit_code"] == 1


def test_expected_interruption_then_success_keeps_both_terminals(bundle):
    root, _, cap = bundle
    step, first = terminal_bundle(bundle, "recovery")
    step["attempt_terminal_statuses"] = ["cancelled", "success"]
    first["terminal_status"] = "cancelled"
    authority = json.loads((root / "terminal-authority.json").read_text())
    authority["status"] = "cancelled"
    first["authoritative_terminal_sha256"] = put(root, "terminal-authority.json", authority)
    cap["attempts"][0]["status"] = "cancelled"
    second = copy.deepcopy(first)
    second.update(attempt_id="attempt-2", run_id="run-2", terminal_status="success")
    authority.update(run_id="run-2", status="success")
    second["authoritative_terminal_file"] = "terminal-2.json"
    second["authoritative_terminal_sha256"] = put(root, "terminal-2.json", authority)
    for field, name in [("browser_evidence", "browser"), ("performance_evidence", "performance")]:
        doc = json.loads((root / (name + ".json")).read_text())
        doc["run_id"] = "run-2"
        second[field] = {"evidence_file": name + "-2.json", "evidence_sha256": put(root, name + "-2.json", doc)}
    cap["records"].append(second)
    cap["attempts"].append({**cap["attempts"][0], "attempt_id": "attempt-2", "run_id": "run-2", "status": "success"})
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    assert run(bundle)["exit_code"] == 0
    step.pop("attempt_terminal_statuses")
    assert run(bundle)["exit_code"] == 1


@pytest.mark.parametrize("defect", ["unplanned", "reorder", "missing_required"])
def test_compound_contract_does_not_drop_or_retype_calls(bundle, defect):
    root, _, cap = bundle
    compound_bundle(bundle)
    if defect == "unplanned":
        cap["records"][0]["tool_name"] = "not_locked"
    elif defect == "reorder":
        cap["records"].reverse()
        cap["attempts"][0]["tool_call_ids"].reverse()
    else:
        cap["records"] = cap["records"][1:]
        cap["attempts"][0]["tool_call_ids"] = ["synthetic-call"]
        metrics = json.loads((root / "performance.json").read_text())
        metrics["tool_calls"] = 1
        cap["records"][0]["performance_evidence"]["evidence_sha256"] = put(root, "performance.json", metrics)
    cap["run_ledger_sha256"] = put(root, "run-ledger.json", cap["attempts"])
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("defect", ["fake_tool", "wrong_answer_hash", "no_review", "no_clarification_rubric", "no_terminal", "saved_after_cancel"])
def test_terminal_record_must_be_real_and_independently_reviewed(bundle, defect):
    root, _, cap = bundle
    step, rec = terminal_bundle(bundle, "recovery" if defect == "saved_after_cancel" else "clarification")
    if defect == "fake_tool":
        rec["tool_call_id"] = "fake"
    elif defect == "no_review":
        rec.pop("semantic_review")
    elif defect == "no_clarification_rubric":
        step["expected"].pop("clarification_rubric")
    elif defect == "no_terminal":
        cap["records"] = []
    else:
        authority = json.loads((root / "terminal-authority.json").read_text())
        if defect == "wrong_answer_hash":
            authority["answer_sha256"] = "0" * 64
        else:
            authority["saved_result_ids"] = ["unconfirmed-save"]
        rec["authoritative_terminal_sha256"] = put(root, "terminal-authority.json", authority)
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("defect", ["retry_id", "retry_note", "late_seal", "wrong_first_result"])
def test_dynamic_request_seal_retains_real_first_payload(bundle, defect):
    root, exp, _ = bundle
    rec = save_bundle(bundle)
    exp["cases"][0]["expected"]["request_id"] = {"capture_before_dispatch": True}
    request = {"request_id": "synthetic-request", "result_id": "p", "item_ids": ["p1"], "note": "synthetic note"}
    sealed = {"captured_at": "2026-10-05T00:00:02Z", "request": copy.deepcopy(request)}
    requests = [
        {"dispatched_at": "2026-10-05T00:00:03Z", "request": copy.deepcopy(request)},
        {"dispatched_at": "2026-10-05T00:00:04Z", "request": copy.deepcopy(request)},
    ]
    if defect == "retry_id":
        requests[1]["request"]["request_id"] = "rewritten-id"
    elif defect == "retry_note":
        requests[1]["request"]["note"] = "rewritten-note"
    elif defect == "late_seal":
        sealed["captured_at"] = "2026-10-05T00:00:05Z"
    else:
        sealed["request"]["result_id"] = "wrong-result"
    for key, value in [("save_dispatch", sealed), ("save_requests", requests)]:
        rec[key + "_file"] = key + ".json"
        rec[key + "_sha256"] = put(root, key + ".json", value)
    assert run(bundle)["exit_code"] != 0


@pytest.mark.parametrize("defect", ["missing_producer", "wrong_item", "wrong_owner", "future_created"])
def test_new_frozen_binding_requires_prior_producer(bundle, defect):
    root, _, cap = bundle
    _, detail = query_detail_bundle(bundle)
    exported = json.loads((root / "new-bound-result.json").read_text())
    if defect == "missing_producer":
        cap["records"][0]["response"]["id"] = "other-result"
    elif defect == "wrong_item":
        exported["result"]["items"][0]["item_id"] = "fabricated"
    elif defect == "wrong_owner":
        exported["result"]["owner_id"] = "foreign"
    else:
        exported["result"]["created_at"] = "2099-01-01T00:00:00Z"
    detail["bound_result_sha256"] = put(root, "new-bound-result.json", exported)
    assert run(bundle)["exit_code"] != 0


def test_compound_run_with_required_terminal_capture(bundle):
    root, _, cap = bundle
    step = compound_bundle(bundle)
    query = cap["records"][-1]
    terminal = {
        k: copy.deepcopy(v)
        for k, v in query.items()
        if k not in ("tool_call_id", "tool_name", "raw_arguments", "actual_conditions", "response", "source", "outcome", "bound_result_id")
    }
    terminal["record_kind"] = "terminal"
    expected = {**copy.deepcopy(step["expected"]), "allowed_actions": ["terminal"], "expected_outcome": "terminal", "saved_result_ids": []}
    step["terminal_contract"] = {"case_type": "recovery", "expected": expected}
    terminal["authoritative_terminal_file"] = "terminal-authority.json"
    terminal["authoritative_terminal_sha256"] = put(
        root,
        "terminal-authority.json",
        {"run_id": terminal["run_id"], "status": "success", "answer_sha256": hashlib.sha256(terminal["answer"].encode()).hexdigest(), "saved_result_ids": []},
    )
    browser = json.loads((root / "browser.json").read_text())
    browser.update(result_id=None, source=None)
    terminal["browser_evidence"] = {"evidence_file": "terminal-browser.json", "evidence_sha256": put(root, "terminal-browser.json", browser)}
    cap["records"].append(terminal)
    assert run(bundle)["exit_code"] == 0
    cap["records"].pop()
    assert run(bundle)["exit_code"] == 2


def test_producing_query_cannot_start_after_consumed_detail(bundle):
    _, _, cap = bundle
    query_detail_bundle(bundle)
    cap["records"][0]["started_at"] = "2026-10-05T00:00:03Z"
    assert run(bundle)["exit_code"] != 0


def test_invented_evidence_not_authorized_by_renamed_source_field(bundle):
    root, _, _ = bundle
    step, detail = query_detail_bundle(bundle)
    exported = json.loads((root / "new-bound-result.json").read_text())
    fabricated = [{"kind": "kd", "source_ref": "forged", "grade": "S", "note": "invented authorization"}]
    exported["result"]["items"][0]["evidence"] = fabricated
    detail["response"]["evidence"] = fabricated
    step["tool_contracts"][1]["expected"]["fact_fields"] = ["identity", "evidence"]
    detail["bound_result_sha256"] = put(root, "new-bound-result.json", exported)
    assert run(bundle)["exit_code"] != 0


def rich_evidence_bundle(bundle):
    root, exp, cap = bundle
    rows = json.loads((root / "rows.json").read_text())
    row = next(row for row in rows if row["identity"] == "a")
    row["signals"][0].update(source_ref="synthetic:board", label="Synthetic board", grade="S", note="Historical evidence only", value=0, unit="count")
    rows_hash = put(root, "rows.json", rows)
    exp["sources"]["catalog"]["rows_sha256"] = rows_hash
    cap["records"][0]["source"]["rows_sha256"] = rows_hash
    parents = json.loads((root / "parents.json").read_text())
    for parent in parents:
        parent["source"]["rows_sha256"] = rows_hash
    exp["states"]["before"]["parent_chain_sha256"] = put(root, "parents.json", parents)
    refresh_browser(bundle)
    step, detail = query_detail_bundle(bundle)
    exported = json.loads((root / "new-bound-result.json").read_text())
    detail["response"]["evidence"] = copy.deepcopy(exported["result"]["items"][0]["evidence"])
    step["tool_contracts"][1]["expected"]["fact_fields"] = ["identity", "evidence"]
    return step, detail, exported


def test_projection_may_only_omit_evidence_source_ref(bundle):
    _, _, cap = bundle
    _, detail, _ = rich_evidence_bundle(bundle)
    for item in cap["records"][0]["response"]["items"]:
        for evidence in item["evidence"]:
            evidence.pop("source_ref", None)
    for evidence in detail["response"]["evidence"]:
        evidence.pop("source_ref", None)
    assert run(bundle)["exit_code"] == 0


@pytest.mark.parametrize("defect", ["grade", "note", "source_ref", "citation", "missing_grade", "zero_bool", "unit", "new_field", "reorder", "missing_signal"])
def test_evidence_mapping_preserves_all_signal_facts(bundle, defect):
    root, _, cap = bundle
    _, detail, exported = rich_evidence_bundle(bundle)
    evidence = exported["result"]["items"][0]["evidence"]
    if defect == "grade":
        evidence[0]["grade"] = "A"
    elif defect == "note":
        evidence[0]["note"] = "Invented authorization"
    elif defect == "source_ref":
        evidence[0]["source_ref"] = "forged"
    elif defect == "citation":
        evidence[0]["citation_id"] = "qb:1"
    elif defect == "missing_grade":
        evidence[0].pop("grade")
    elif defect == "zero_bool":
        evidence[0]["value"] = False
    elif defect == "unit":
        evidence[0]["unit"] = "dollars"
    elif defect == "new_field":
        evidence[0]["permission"] = "allowed"
    elif defect == "reorder":
        evidence.reverse()
    else:
        evidence.pop()
    detail["response"]["evidence"] = copy.deepcopy(evidence)
    cap["records"][0]["response"]["items"][0]["evidence"] = copy.deepcopy(evidence)
    detail["bound_result_sha256"] = put(root, "new-bound-result.json", exported)
    assert run(bundle)["exit_code"] != 0


def test_created_time_cannot_precede_query_even_when_call_times_tie(bundle):
    _, _, cap = bundle
    query_detail_bundle(bundle)
    for record in cap["records"]:
        record["started_at"] = "2026-10-05T00:00:03Z"
    assert run(bundle)["exit_code"] != 0


def test_existing_bound_save_evidence_also_uses_independent_signals(bundle):
    root, exp, _ = bundle
    save_bundle(bundle)
    parents = json.loads((root / "parents.json").read_text())
    parents[0]["items"][0]["evidence"] = [{"citation_id": "p1:1", "kind": "kd", "grade": "S", "note": "invented"}]
    exp["states"]["before"]["parent_chain_sha256"] = put(root, "parents.json", parents)
    assert run(bundle)["exit_code"] != 0
