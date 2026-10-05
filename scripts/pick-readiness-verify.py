#!/usr/bin/env python3
"""Offline independent readiness oracle. All fixture/example data is synthetic.

CLI: --expectations manifest.json --captures captures.json --out report.json.
Hashes cover raw UTF-8 file bytes. Expectations must be independently reviewed
and locked before captures.started_at. State snapshots may be sealed between
steps, but must precede the corresponding record.started_at. This checker cannot
prove an exporter omitted no events: the separately hashed run ledger is required.
"""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

VERSION = "pick-readiness-v1.1"
DEFAULTS = dict(
    theater=None,
    language=None,
    channel=None,
    query=None,
    tags=[],
    limit=5,
    exclude_selected=True,
    confirmed_eligible_only=True,
    exclude_previous=False,
    signal_kind=None,
    sort="evidence_date",
    exclude_posted=False,
    posted_account=None,
    hot_only=False,
)
HOT = {"kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn"}
TYPES = {
    "query",
    "count",
    "detail",
    "prepare_save",
    "clarification",
    "refusal",
    "recovery",
    "knowledge",
}
LAYERS = ("intent", "data_state", "browser", "model_explanation", "performance_cost")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(Invalid("non-finite JSON")),
    )


def file_ref(base, obj, prefix):
    path = base / obj[prefix + "_file"]
    require(digest(path) == obj[prefix + "_sha256"], prefix + " hash mismatch")
    return read(path)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp needs timezone")
    return result


def conditions(value, full=False):
    require(
        isinstance(value, dict) and not (set(value) - set(DEFAULTS)),
        "unknown condition",
    )
    if full:
        require(
            set(value) == set(DEFAULTS), "expected conditions must state every default"
        )
    c = {**DEFAULTS, **value}
    for key in (
        "exclude_selected",
        "confirmed_eligible_only",
        "exclude_previous",
        "exclude_posted",
        "hot_only",
    ):
        require(type(c[key]) is bool, "invalid boolean condition")
    require(type(c["limit"]) is int and 1 <= c["limit"] <= 20, "invalid limit")
    require(c["sort"] in ("evidence_date", "rank"), "invalid sort")
    require(c["channel"] in (None, "youtube", "tiktok", "facebook"), "invalid channel")
    require(
        isinstance(c["tags"], list) and all(isinstance(x, str) for x in c["tags"]),
        "invalid tags",
    )
    for key in ("theater", "language", "query", "signal_kind", "posted_account"):
        require(c[key] is None or isinstance(c[key], str), "invalid text condition")
    return c


def match_rows(rows, c, excluded):
    """Independent implementation from the written filtering/order contract."""
    kind = c["signal_kind"]
    boards = [s for row in rows for s in row["signals"] if s["kind"] == kind]
    if c["sort"] == "rank":
        require(
            kind and boards and any(s.get("rank") is not None for s in boards),
            "rank unavailable",
        )
    latest_board = max(
        (s["observed_at"] for s in boards if s.get("observed_at")), default=None
    )
    result = []
    for row in rows:
        if row["identity"] in excluded or row["availability"] == "delisted":
            continue
        if any(
            c[k] and row[k].casefold() != c[k].casefold()
            for k in ("language", "theater")
        ):
            continue
        if not set(c["tags"]).issubset(row["tags"]):
            continue
        if (
            c["query"]
            and c["query"].casefold()
            not in (row["title"] + " " + " ".join(row["tags"])).casefold()
        ):
            continue
        signals = row["signals"]
        selected = sorted(
            (s for s in signals if s["kind"] == kind),
            key=lambda s: (
                s.get("observed_at") or "",
                s.get("rank") is not None,
                -(s.get("rank") or 0),
            ),
            reverse=True,
        )
        if kind and not selected:
            continue
        if c["hot_only"] and not any(s["kind"] in HOT for s in signals):
            continue
        if (
            c["sort"] == "rank"
            and latest_board is not None
            and selected[0].get("observed_at") != latest_board
        ):
            continue
        permission = row["channel_rules"].get(c["channel"], "unknown")
        if c["channel"] and (
            permission == "denied"
            or c["confirmed_eligible_only"]
            and (permission != "allowed" or row["availability"] != "active")
        ):
            continue
        if c["exclude_posted"] or c["posted_account"]:
            posted = row.get("posted")
            require(posted is not None, "publication state unavailable")
            if c["exclude_posted"] and posted["post_count"] > 0:
                continue
            if c["posted_account"] and c["posted_account"].strip().casefold() in {
                a.casefold() for a in posted["accounts"]
            }:
                continue
        if c["sort"] == "rank":
            rank = selected[0].get("rank")
            order = (rank is None, rank or 0)
        else:
            dates = [
                s["observed_at"][:10]
                for s in signals
                if s.get("observed_at") and (not c["hot_only"] or s["kind"] in HOT)
            ]
            # ISO date digits reverse lexicographically, identity always ascending.
            order = (not dates, tuple(-ord(char) for char in max(dates, default="")))
        result.append((order, row["identity"], row))
    return [r for _, _, r in sorted(result, key=lambda x: (x[0], x[1]))]


def diagnosis(rows, c, excluded):
    relaxations = []
    for field in (
        "theater",
        "language",
        "channel",
        "confirmed_eligible_only",
        "query",
        "tags",
        "signal_kind",
        "sort",
        "hot_only",
        "exclude_posted",
        "posted_account",
    ):
        if field == "confirmed_eligible_only" and not c["channel"]:
            continue
        if not c[field] or field == "sort" and c[field] != "rank":
            continue
        value = False if field == "confirmed_eligible_only" else DEFAULTS[field]
        relaxed = {**c, field: value}
        extra = {}
        if field == "signal_kind" and c["sort"] == "rank":
            relaxed["sort"] = "evidence_date"
            extra["also_removed"] = ["sort"]
        relaxations.append((field, c[field], relaxed, excluded, extra))
    if excluded:
        relaxations.append(("excluded", len(excluded), c, set(), {}))
    counts = []
    for field, value, relaxed, remaining, extra in relaxations:
        try:
            total = len(match_rows(rows, relaxed, remaining))
        except Invalid as error:
            if str(error) != "publication state unavailable":
                raise
            total = None
        counts.append(
            {"condition": field, "value": value, "matched_total": total, **extra}
        )
    return {
        "catalog_rows": len(rows),
        "delisted_rows": sum(r["availability"] == "delisted" for r in rows),
        "without_each": counts,
    }


def exclusions(state, base, record, c):
    require(
        timestamp(state["captured_at"]) <= timestamp(record["started_at"]),
        "state captured after step",
    )
    require(state["owner_id"] == record["owner_id"], "state owner mismatch")
    selected = file_ref(base, state, "selections_before")
    chain = file_ref(base, state, "parent_chain")
    require(
        isinstance(selected, list) and isinstance(chain, list),
        "state must be arrays, empty explicitly",
    )
    excluded = {x["identity"] for x in selected} if c["exclude_selected"] else set()
    if c["exclude_previous"]:
        parent_id = record.get("bound_result_id")
        require(bool(chain) and parent_id == chain[0]["id"], "missing bound parent")
        seen = set()
        for index, parent in enumerate(chain):
            require(parent["id"] not in seen, "parent cycle")
            seen.add(parent["id"])
            require(
                parent["owner_id"] == record["owner_id"]
                and parent["thread_id"] == record["thread_id"],
                "parent ownership mismatch",
            )
            require(
                parent.get("parent_result_id")
                == (chain[index + 1]["id"] if index + 1 < len(chain) else None),
                "incomplete parent chain",
            )
            excluded.update(x["identity"] for x in parent["items"])
    return excluded, selected, chain


def check_external(record, expected, base):
    layers = {}
    for layer, field in [
        ("browser", "browser_evidence"),
        ("performance_cost", "performance_evidence"),
    ]:
        evidence = record.get(field)
        if not evidence:
            layers[layer] = {
                "status": "NOT_RUN",
                "reason_code": "MISSING_" + field.upper(),
            }
            continue
        document = file_ref(base, evidence, "evidence")
        require(document["run_id"] == record["run_id"], "evidence run mismatch")
        if layer == "browser":
            require(
                document.get("assertions") and document.get("artifact_refs"),
                "browser assertions/artifact required",
            )
            status = (
                "PASS"
                if all(x["passed"] is True for x in document["assertions"])
                else "FAIL"
            )
        else:
            require(
                all(
                    k in document
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "elapsed_seconds",
                        "tool_calls",
                        "model_calls",
                        "sample_conditions",
                    )
                ),
                "performance metrics missing",
            )
            status = (
                "UNVERIFIED"
                if any(
                    document[k] is None
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "elapsed_seconds",
                        "tool_calls",
                        "model_calls",
                    )
                )
                else "PASS"
            )
        layers[layer] = {"status": status, "reason_code": "EXTERNAL_EVIDENCE"}
    review = record.get("semantic_review")
    if not review:
        layers["model_explanation"] = {
            "status": "NOT_RUN",
            "reason_code": "MISSING_INDEPENDENT_REVIEW",
        }
    else:
        require(
            review["reviewer"] and review["reviewer"] != record["generated_by"],
            "reviewer generated answer",
        )
        require(
            review["answer_sha256"]
            == hashlib.sha256(record["answer"].encode()).hexdigest(),
            "review answer hash mismatch",
        )
        judgments = review["judgments"]
        require(
            [x["rubric"] for x in judgments] == expected["semantic_rubric"]
            and judgments,
            "incomplete semantic rubric",
        )
        require(
            all(
                x.get("reason")
                and x.get("fact_refs")
                and x["status"] in ("PASS", "FAIL", "UNVERIFIED", "NOT_RUN")
                for x in judgments
            ),
            "missing semantic reasoning",
        )
        layers["model_explanation"] = {
            "status": overall([x["status"] for x in judgments]),
            "reason_code": "INDEPENDENT_REVIEW",
            "reviewer": review["reviewer"],
        }
    return layers


def overall(statuses):
    for status in ("FAIL", "UNVERIFIED", "NOT_RUN"):
        if status in statuses:
            return status
    return "PASS"


def check_record(step, record, manifest, exp_base, cap_base, captures):
    expected = step["expected"]
    kind = step["case_type"]
    require(kind in TYPES, "unknown case_type")
    require(
        expected.get("allowed_actions")
        and expected.get("expected_outcome")
        and expected.get("semantic_rubric"),
        "incomplete expectation",
    )
    require(
        all(
            record.get(k)
            for k in (
                "owner_id",
                "thread_id",
                "run_id",
                "tool_call_id",
                "tool_name",
                "started_at",
                "generated_by",
            )
        ),
        "missing capture identity",
    )
    require(
        isinstance(record["raw_arguments"], dict) and isinstance(record["answer"], str),
        "missing raw capture",
    )
    intent = (
        record["tool_name"] in expected["allowed_actions"]
        and record["outcome"] == expected["expected_outcome"]
    )
    c = None
    if kind in ("query", "count"):
        allowed = [conditions(x, full=True) for x in expected["allowed_condition_sets"]]
        actual = conditions(record["actual_conditions"])
        intent = intent and actual in allowed
        c = actual if actual in allowed else allowed[0]
    for key, value in expected.get("required_arguments", {}).items():
        intent = intent and record["raw_arguments"].get(key) == value
    layers = {
        "intent": {
            "status": "PASS" if intent else "FAIL",
            "reason_code": "LOCKED_INTENT",
        }
    }
    source = manifest["sources"][step["source_key"]]
    rows = file_ref(exp_base, source, "rows")
    metadata = file_ref(exp_base, source, "metadata")
    require(isinstance(rows, list), "rows not array")
    require(
        len({r["identity"] for r in rows}) == len(rows), "duplicate catalog identity"
    )
    for key in (
        "source_as_of",
        "published_at",
        "freshness",
        "rule_version",
        "ranking_version",
    ):
        require(key in metadata, "missing independent source metadata")
    version_ok = all(
        record["source"].get(k) == source[k]
        for k in ("catalog_batch_id", "source_type", "shared")
    )
    version_ok = (
        version_ok and record["source"].get("rows_sha256") == source["rows_sha256"]
    )
    response = record["response"]
    require(isinstance(response, dict), "missing typed response")
    state = manifest["states"][step["state_key"]]
    state_base = exp_base
    if "capture_before_step" in state:
        require(
            state["capture_before_step"]
            == {"case_id": record["case_id"], "step_id": record["step_id"]},
            "dynamic state bound to wrong step",
        )
        planned_owner = state["owner_id"]
        state = captures["states"][step["state_key"]]
        require(state["owner_id"] == planned_owner, "dynamic state owner mismatch")
        state_base = cap_base
    excluded, selected, chain = exclusions(state, state_base, record, c or DEFAULTS)
    ok = True
    if kind in ("query", "count"):
        raw = record["raw_arguments"]
        require(set(raw).issubset({"filters", "use_latest"}), "unknown query argument")
        raw_conditions = raw["filters"]
        inherited = {}
        if raw_conditions.get("exclude_previous") is True:
            require(chain, "raw request missing parent")
            inherited = chain[0]["conditions"]
            if not raw.get("use_latest", False):
                version_ok = (
                    version_ok
                    and chain[0]["catalog_batch_id"] == source["catalog_batch_id"]
                )
        requested = conditions({**inherited, **raw_conditions})
        if requested not in allowed or requested != actual:
            layers["intent"] = {"status": "FAIL", "reason_code": "RAW_ARGUMENT_INTENT"}
    if kind in ("query", "count"):
        matches = match_rows(rows, c, excluded)
        if kind == "query":
            require(
                response.get("id") and isinstance(response.get("items"), list),
                "missing query result",
            )
            ok = [x["identity"] for x in response["items"]] == [
                x["identity"] for x in matches[: c["limit"]]
            ] and response["matched_total"] == len(matches)
        else:
            ok = response["total"] == len(matches)
            for field in ("theater", "language"):
                ok = ok and response["by_" + field] == dict(
                    Counter(r[field] for r in matches)
                )
        if not matches:
            actual_diagnosis = response["zero_diagnosis"]
            reduced = {
                "catalog_rows": actual_diagnosis["catalog_rows"],
                "delisted_rows": actual_diagnosis["delisted_rows"],
                "without_each": [
                    {
                        k: v
                        for k, v in item.items()
                        if k in ("condition", "value", "matched_total", "also_removed")
                    }
                    for item in actual_diagnosis["without_each"]
                ],
            }
            ok = ok and reduced == diagnosis(rows, c, excluded)
        if response.get("catalog_batch_id") is not None:
            version_ok = (
                version_ok
                and response["catalog_batch_id"] == source["catalog_batch_id"]
            )
    elif kind == "detail":
        require(chain, "detail requires independently captured result")
        bound = next((p for p in chain if p["id"] == expected["result_id"]), None)
        require(bound is not None, "missing detail result")
        item = next(
            (x for x in bound["items"] if x["item_id"] == expected["item_id"]), None
        )
        require(item is not None, "missing frozen detail item")
        ok = (
            response["result_id"] == expected["result_id"]
            and response["item_id"] == expected["item_id"]
            and response["identity"] == item["identity"]
        )
        for key in expected["fact_fields"]:
            ok = ok and response[key] == item[key]
    elif kind == "prepare_save":
        require(
            expected.get("result_id")
            and expected.get("item_ids")
            and expected.get("request_id")
            and "note" in expected,
            "missing save expectation",
        )
        require(
            chain and chain[0]["id"] == expected["result_id"],
            "save requires frozen result",
        )
        ok = (
            response["requires_confirmation"] is True
            and response["result_id"] == expected["result_id"]
            and response["item_ids"] == expected["item_ids"]
            and response["note"] == expected["note"]
        )
        after_prepare = file_ref(cap_base, record, "selections_after_prepare")
        after = file_ref(cap_base, record, "selections_after")
        receipts = file_ref(cap_base, record, "receipts")
        ok = ok and after_prepare == selected and len(receipts) >= 2
        identities = {
            x["identity"]
            for x in chain[0]["items"]
            if x["item_id"] in expected["item_ids"]
        }
        require(
            len(identities) == len(expected["item_ids"]), "save items not in snapshot"
        )
        ok = ok and Counter(x["identity"] for x in after) == Counter(
            {x: 1 for x in {s["identity"] for s in selected} | identities}
        )
        ok = ok and all(
            r["request_id"] == expected["request_id"]
            and r["result_id"] == expected["result_id"]
            and r["item_ids"] == expected["item_ids"]
            and r["note"] == expected["note"]
            for r in receipts
        )
        ok = (
            ok
            and receipts[0]["selection_ids"] == receipts[1]["selection_ids"]
            and len(receipts[0]["selection_ids"]) == len(identities)
        )
    elif kind == "clarification":
        require(
            expected.get("missing_parameters") and expected.get("allowed_branches"),
            "missing clarification contract",
        )
        ok = (
            response["missing_parameters"] == expected["missing_parameters"]
            and response["branch"] in expected["allowed_branches"]
        )
    elif kind == "refusal":
        require(expected.get("reason_codes"), "missing refusal reasons")
        ok = (
            response["reason_code"] in expected["reason_codes"]
            and response["status"] == "refused"
        )
    elif kind == "recovery":
        authority = file_ref(cap_base, record, "authoritative_terminal")
        ok = (
            response["status"] == authority["status"] == expected["terminal_status"]
            and authority["run_id"] == record["run_id"]
        )
        ok = (
            ok
            and response["saved_result_ids"]
            == authority["saved_result_ids"]
            == expected["saved_result_ids"]
        )
    elif kind == "knowledge":
        knowledge = file_ref(exp_base, source, "knowledge")
        require(
            expected.get("citation_ids") and "knowledge_batch_id" in source,
            "missing knowledge contract",
        )
        version_ok = (
            version_ok
            and record["source"].get("knowledge_batch_id")
            == source["knowledge_batch_id"]
        )
        by_id = {x["citation_id"]: x for x in knowledge}
        ok = [x["citation_id"] for x in response["citations"]] == expected[
            "citation_ids"
        ]
        ok = ok and all(x == by_id.get(x["citation_id"]) for x in response["citations"])
    layers["data_state"] = {
        "status": ("PASS" if ok else "FAIL") if version_ok else "UNVERIFIED",
        "reason_code": "INDEPENDENT_ORACLE"
        if version_ok
        else "UNVERIFIED_DATA_VERSION",
    }
    layers.update(check_external(record, expected, cap_base))
    terminal = record.get("terminal_status")
    require(terminal is not None, "missing run terminal status")
    if terminal != expected.get("terminal_status", "success"):
        layers["intent"] = {"status": "FAIL", "reason_code": "WRONG_TERMINAL_STATUS"}
    return layers


def verify(expectations_path, captures_path):
    report = {"spec_version": VERSION, "checks": [], "errors": []}
    try:
        manifest, captures = read(expectations_path), read(captures_path)
        report["expectations_sha256"] = digest(expectations_path)
        report["captures_sha256"] = digest(captures_path)
        require(
            manifest["spec_version"] == captures["spec_version"] == VERSION,
            "unsupported version",
        )
        require(
            captures["expectations_sha256"] == digest(expectations_path),
            "expectations hash mismatch",
        )
        require(
            timestamp(manifest["locked_at"]) <= timestamp(captures["started_at"]),
            "expectations locked after execution",
        )
        require(
            manifest["review"]["reviewer"] and manifest["review"]["basis"],
            "expectations need independent review",
        )
        runtime = manifest["runtime"]
        config = file_ref(expectations_path.parent, runtime, "config_evidence")
        require(
            all(
                runtime[k] == config[k]
                for k in (
                    "app_sha",
                    "model",
                    "mode",
                    "run_timeout_seconds",
                    "request_timeout_seconds",
                    "stream_chunk_timeout_seconds",
                )
            ),
            "runtime configuration mismatch",
        )
        require(
            all(
                type(runtime[k]) is int and runtime[k] > 0
                for k in (
                    "run_timeout_seconds",
                    "request_timeout_seconds",
                    "stream_chunk_timeout_seconds",
                )
            ),
            "invalid runtime budget",
        )
        require(manifest["cases"], "no requested cases")
        if manifest["environment"] == "remote-qa":
            require(
                all(
                    s["source_type"] == "realshort_shared" and s["shared"] is True
                    for s in manifest["sources"].values()
                ),
                "remote QA source is not shared RealShort",
            )
        ledger = file_ref(captures_path.parent, captures, "run_ledger")
        require(
            captures["attempts"] == ledger,
            "capture attempts differ from independent ledger",
        )
        records = captures["records"]
        steps = {}
        for case in manifest["cases"]:
            for step in case.get("steps", [case]):
                key = (case["case_id"], step.get("step_id", "main"))
                require(key not in steps, "duplicate step")
                steps[key] = step
        seen_calls = set()
        for record in records:
            key = (record["case_id"], record.get("step_id", "main"))
            require(key in steps, "unexpected capture step")
            call_key = (record["run_id"], record["tool_call_id"])
            require(call_key not in seen_calls, "duplicate tool capture")
            seen_calls.add(call_key)
            require(
                any(
                    all(
                        a[k] == record[k]
                        for k in ("case_id", "step_id", "attempt_id", "run_id")
                    )
                    for a in ledger
                ),
                "capture absent from run ledger",
            )
            try:
                layers = check_record(
                    steps[key],
                    record,
                    manifest,
                    expectations_path.parent,
                    captures_path.parent,
                    captures,
                )
            except (
                Invalid,
                OSError,
                KeyError,
                TypeError,
                ValueError,
                IndexError,
                AttributeError,
            ) as error:
                layers = {
                    name: {"status": "UNVERIFIED", "reason_code": "INVALID_CAPTURE"}
                    for name in LAYERS
                }
                expected = steps[key]["expected"]
                if record.get("outcome") != expected.get(
                    "expected_outcome"
                ) or record.get("terminal_status") != expected.get(
                    "terminal_status", "success"
                ):
                    layers["intent"] = {
                        "status": "FAIL",
                        "reason_code": "OBSERVED_FAILURE",
                    }
                report["errors"].append(type(error).__name__ + ": " + str(error))
            report["checks"].append(
                {
                    "case_id": key[0],
                    "step_id": key[1],
                    "attempt_id": record["attempt_id"],
                    "layers": layers,
                    "status": overall([v["status"] for v in layers.values()]),
                }
            )
        for key in steps:
            attempts = [a for a in ledger if (a["case_id"], a["step_id"]) == key]
            if not attempts:
                report["checks"].append(
                    {
                        "case_id": key[0],
                        "step_id": key[1],
                        "status": "NOT_RUN",
                        "reason_code": "NO_ATTEMPT",
                    }
                )
            for attempt in attempts:
                if not any(
                    all(
                        r[k] == attempt[k]
                        for k in ("case_id", "step_id", "attempt_id", "run_id")
                    )
                    for r in records
                ):
                    report["checks"].append(
                        {
                            "case_id": key[0],
                            "step_id": key[1],
                            "status": "UNVERIFIED",
                            "reason_code": "MISSING_ATTEMPT_CAPTURE",
                        }
                    )
        require(len({a["run_id"] for a in ledger}) <= 40, "shared run budget exceeded")
        for case in manifest["cases"]:
            require(
                len({a["run_id"] for a in ledger if a["case_id"] == case["case_id"]})
                <= case["planned_max_runs"],
                "planned case budget exceeded",
            )
    except (
        Invalid,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        AttributeError,
    ) as error:
        report["errors"].append(type(error).__name__ + ": " + str(error))
    statuses = [x["status"] for x in report["checks"]]
    report["status"] = overall(statuses + (["UNVERIFIED"] if report["errors"] else []))
    report["exit_code"] = (
        1
        if "FAIL" in statuses
        else 2
        if report["errors"] or not statuses or any(s != "PASS" for s in statuses)
        else 0
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expectations", required=True, type=Path)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = verify(args.expectations, args.captures)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "exit_code": report["exit_code"],
                "checks": len(report["checks"]),
            }
        )
    )
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
