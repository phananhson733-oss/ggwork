#!/usr/bin/env python3
"""Offline independent readiness oracle. All fixture/example data is synthetic.

CLI: --expectations manifest.json --captures captures.json --out report.json.
Hashes cover raw UTF-8 file bytes. Expectations must be independently reviewed
and locked before captures.started_at. State snapshots may be sealed between
steps, but must precede the corresponding record.started_at. This checker cannot
prove an exporter omitted no events: the separately hashed run ledger is required.
"""

import argparse
import copy
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
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
    "prepare_only",
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


def nonnegative_int(value):
    require(type(value) is int and value >= 0, "count must be a nonnegative integer")


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
    excluded = (
        {x["identity"] for x in selected if x.get("state", "selected") == "selected"}
        if c["exclude_selected"]
        else set()
    )
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
    if c["exclude_previous"]:
        require(
            bool(chain) and record.get("bound_result_id") == chain[0]["id"],
            "missing bound parent",
        )
        excluded.update(x["identity"] for parent in chain for x in parent["items"])
    return excluded, selected, chain


def check_external(record, expected, base, captures):
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
                expected.get("browser_assertions"), "missing locked browser assertions"
            )
            require(
                [x["name"] for x in document["assertions"]]
                == expected["browser_assertions"],
                "browser assertion inventory mismatch",
            )
            require(
                document["source"] == record.get("source"), "browser source mismatch"
            )
            result_id = record.get("response", {}).get(
                "result_id", record.get("response", {}).get("id")
            )
            require(document["result_id"] == result_id, "browser result mismatch")
            require(document["artifact_refs"], "missing browser artifact")
            for artifact in document["artifact_refs"]:
                require(
                    digest(base / artifact["artifact_file"])
                    == artifact["artifact_sha256"],
                    "browser artifact hash mismatch",
                )
            require(
                all(type(x["passed"]) is bool for x in document["assertions"]),
                "invalid browser assertion",
            )
            status = (
                "PASS" if all(x["passed"] for x in document["assertions"]) else "FAIL"
            )
        else:
            require(
                isinstance(document.get("sample_conditions"), str)
                and document["sample_conditions"].strip(),
                "missing sample conditions",
            )
            unknown = False
            for key in (
                "input_tokens",
                "output_tokens",
                "elapsed_seconds",
                "tool_calls",
                "model_calls",
            ):
                value = document[key]
                if value is None or value == "unknown":
                    unknown = True
                    continue
                require(
                    type(value) in (int, float) and math.isfinite(value) and value >= 0,
                    "invalid numeric performance metric",
                )
                if key != "elapsed_seconds":
                    require(type(value) is int, "count metric must be integer")
            require(document.get("scope") == "run", "performance scope must be run")
            inventory = {
                call
                for attempt in captures["attempts"]
                if attempt["run_id"] == record["run_id"]
                for call in attempt["tool_call_ids"]
            }
            if document["tool_calls"] not in (None, "unknown") and document[
                "tool_calls"
            ] != len(inventory):
                status = "FAIL"
            else:
                status = "UNVERIFIED" if unknown else "PASS"
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


def captured_state(manifest, captures, step, record):
    state = manifest["states"][step["state_key"]]
    if "capture_before_step" not in state:
        return state, False
    require(
        state["capture_before_step"]
        == {"case_id": record["case_id"], "step_id": record["step_id"]},
        "dynamic state bound to wrong step",
    )
    key = record.get("state_capture_key", step["state_key"])
    if "state_capture_key" in record:
        require(
            key == step["state_key"] + ":" + record["attempt_id"],
            "dynamic state capture key mismatch",
        )
    attempts = {
        a["attempt_id"]
        for a in captures["attempts"]
        if a["case_id"] == record["case_id"] and a["step_id"] == record["step_id"]
    }
    require(
        len(attempts) <= 1 or "state_capture_key" in record,
        "retry requires attempt-specific state",
    )
    actual = captures["states"][key]
    require(actual["owner_id"] == state["owner_id"], "dynamic state owner mismatch")
    return actual, True


def attempt_status(step, attempt, ledger):
    if "attempt_terminal_statuses" not in step:
        return (
            step.get("terminal_contract", step)
            .get("expected", {})
            .get("terminal_status", "success")
        )
    statuses = step["attempt_terminal_statuses"]
    require(
        isinstance(statuses, list)
        and statuses
        and all(
            x in ("success", "failed", "cancelled", "timeout", "interrupted")
            for x in statuses
        ),
        "invalid planned attempt statuses",
    )
    attempts = [
        a
        for a in ledger
        if (a["case_id"], a["step_id"]) == (attempt["case_id"], attempt["step_id"])
    ]
    ordinal = attempts.index(attempt)
    require(ordinal < len(statuses), "unplanned attempt ordinal")
    return statuses[ordinal]


def typed_step(step, record, ledger, captures, all_steps=None):
    kind = record.get("record_kind", "tool")
    require(kind in ("tool", "terminal"), "invalid record kind")
    if kind == "terminal":
        contract = step.get("terminal_contract", step)
        require(
            contract["case_type"] in ("clarification", "recovery"),
            "unplanned terminal capture",
        )
    elif "tool_contracts" in step:
        contract = next(
            (
                c
                for c in step["tool_contracts"]
                if c["tool_name"] == record["tool_name"]
            ),
            None,
        )
        require(contract is not None, "unplanned tool call")
    else:
        contract = step
    resolved = {
        **step,
        "case_type": contract["case_type"],
        "expected": dict(contract["expected"]),
    }
    expected = resolved["expected"]
    reference = expected.get("result_id")
    if isinstance(reference, dict):
        require(
            set(reference)
            in ({"from_tool", "occurrence"}, {"from_step", "from_tool", "occurrence"})
            and type(reference["occurrence"]) is int
            and reference["occurrence"] >= 1,
            "invalid locked result reference",
        )
        source_step = step
        from_step = reference.get("from_step")
        if from_step is not None:
            require(
                isinstance(from_step, str) and all_steps is not None,
                "invalid cross-step reference",
            )
            source_key = (record["case_id"], from_step)
            current_key = (record["case_id"], record["step_id"])
            require(
                source_key in all_steps
                and list(all_steps).index(source_key)
                < list(all_steps).index(current_key),
                "producer step must precede consumer",
            )
            source_step = all_steps[source_key]
        contracts = source_step.get("tool_contracts", [source_step])
        require(
            any(
                c["case_type"] == "query"
                and (
                    c.get("tool_name") == reference["from_tool"]
                    or reference["from_tool"] in c["expected"]["allowed_actions"]
                )
                for c in contracts
            ),
            "result producer is not a locked query tool",
        )
        prior = captures["records"][: captures["records"].index(record)]
        producers = [
            r
            for r in prior
            if r.get("record_kind", "tool") == "tool"
            and r["case_id"] == record["case_id"]
            and r["owner_id"] == record["owner_id"]
            and r["thread_id"] == record["thread_id"]
            and (
                r["step_id"] == from_step
                if from_step is not None
                else r["run_id"] == record["run_id"]
            )
            and r.get("terminal_status") == "success"
            and r["tool_name"] == reference["from_tool"]
        ]
        require(
            len(producers) >= reference["occurrence"],
            "locked producer occurrence absent",
        )
        produced = producers[reference["occurrence"] - 1]["response"]
        expected["result_id"] = produced["id"]
        if from_step is not None:
            require(
                record.get("bound_result_id") == produced["id"],
                "cross-step active reference mismatch",
            )

        def item_at(position):
            require(
                type(position) is int and 1 <= position <= len(produced["items"]),
                "locked position outside frozen result",
            )
            return produced["items"][position - 1]["item_id"]

        if isinstance(expected.get("item_id"), dict):
            require(
                set(expected["item_id"]) == {"position"},
                "invalid locked detail position",
            )
            expected["item_id"] = item_at(expected["item_id"]["position"])
        if isinstance(expected.get("item_ids"), dict):
            require(
                set(expected["item_ids"]) == {"positions"},
                "invalid locked save positions",
            )
            positions = expected["item_ids"]["positions"]
            require(
                isinstance(positions, list) and positions,
                "missing locked save positions",
            )
            chosen = {item_at(position) for position in positions}
            expected["item_ids"] = [
                item["item_id"]
                for item in produced["items"]
                if item["item_id"] in chosen
            ]
    if "attempt_terminal_statuses" in step:
        attempt = next(
            a
            for a in ledger
            if all(
                a[k] == record[k]
                for k in ("case_id", "step_id", "attempt_id", "run_id")
            )
        )
        resolved["expected"]["terminal_status"] = attempt_status(step, attempt, ledger)
    return resolved


def check_terminal(step, record, manifest, exp_base, cap_base, captures):
    expected = step["expected"]
    require(
        not any(k in record for k in ("tool_call_id", "tool_name", "raw_arguments")),
        "terminal must not impersonate a tool",
    )
    require(
        expected["allowed_actions"] == ["terminal"]
        and expected["expected_outcome"] == "terminal",
        "terminal action not prelocked",
    )
    require(
        all(record.get(k) for k in ("owner_id", "thread_id", "generated_by"))
        and isinstance(record["answer"], str),
        "missing terminal identity/answer",
    )
    state, dynamic = captured_state(manifest, captures, step, record)
    exclusions(state, cap_base if dynamic else exp_base, record, DEFAULTS)
    authority = file_ref(cap_base, record, "authoritative_terminal")
    status = expected.get("terminal_status", "success")
    valid = (
        authority["run_id"] == record["run_id"]
        and authority["status"] == record["terminal_status"] == status
        and authority["answer_sha256"]
        == hashlib.sha256(record["answer"].encode()).hexdigest()
    )
    if step["case_type"] == "clarification":
        require(
            isinstance(expected["missing_parameters"], list)
            and expected["missing_parameters"],
            "missing locked clarification parameters",
        )
        require(
            expected["clarification_rubric"] in expected["semantic_rubric"],
            "clarification requires independent text rubric",
        )
    else:
        valid = valid and authority["saved_result_ids"] == expected["saved_result_ids"]
    layers = {
        name: {
            "status": "PASS" if valid else "FAIL",
            "reason_code": "AUTHORITATIVE_TERMINAL",
        }
        for name in ("intent", "data_state")
    }
    try:
        layers.update(check_external(record, expected, cap_base, captures))
    except (
        Invalid,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        AttributeError,
    ):
        layers.update(
            {
                name: {
                    "status": "UNVERIFIED",
                    "reason_code": "INVALID_EXTERNAL_EVIDENCE",
                }
                for name in ("browser", "model_explanation", "performance_cost")
            }
        )
    return layers


def save_request_id(expected, record, base):
    request_id = expected["request_id"]
    if not isinstance(request_id, dict):
        require(
            isinstance(request_id, str) and request_id, "invalid expected request ID"
        )
        return request_id
    require(
        request_id == {"capture_before_dispatch": True},
        "unknown request ID placeholder",
    )
    sealed = file_ref(base, record, "save_dispatch")
    requests = file_ref(base, record, "save_requests")
    require(
        isinstance(requests, list) and len(requests) >= 2,
        "missing actual save and retry requests",
    )
    request = sealed["request"]
    require(
        set(request) == {"request_id", "result_id", "item_ids", "note"},
        "invalid raw save payload",
    )
    require(
        isinstance(request["request_id"], str) and request["request_id"],
        "missing actual first request ID",
    )
    require(
        all(request[k] == expected[k] for k in ("result_id", "item_ids", "note")),
        "first save request contradicts locked intent",
    )
    previous = timestamp(sealed["captured_at"])
    require(
        timestamp(record["started_at"]) <= previous,
        "save request sealed before its action",
    )
    for dispatch in requests:
        require(
            previous <= timestamp(dispatch["dispatched_at"]),
            "save request sealed after dispatch or retry out of order",
        )
        require(dispatch["request"] == request, "retry changed original raw request")
        previous = timestamp(dispatch["dispatched_at"])
    return request["request_id"]


def json_equal(left, right):
    """JSON number equality survives JS serialization (1.0 -> 1), never bool -> 0/1."""
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def audit_model_payload(value):
    """Independent QA-only decoding. Never imported from the product or sent to a model."""
    require(isinstance(value, dict), "model payload must be an object")
    result = copy.deepcopy(value)
    encoding = result.get("evidence_encoding")
    if "evidence_encoding" not in result:
        return result
    if encoding == "inline-v1":
        require(
            set(result) <= {"id", "result_id", "evidence_encoding", "inline_payload"},
            "invalid inline envelope",
        )
        inner = result.get("inline_payload")
        require(isinstance(inner, dict), "missing inline payload")
        for key in ("id", "result_id"):
            require(
                (key in result) == (key in inner)
                and json_equal(result.get(key), inner.get(key)),
                "inline locator mismatch",
            )
        return inner  # Exactly once: marker-looking legacy content remains literal.
    require(encoding == "facts-ref-v1", "unknown evidence encoding")
    table = result.pop("evidence_facts", None)
    require(isinstance(table, dict) and table, "missing evidence dictionary")
    allowed = {"kind", "label", "observed_at", "rank", "value", "grade", "note"}
    require(
        all(
            isinstance(key, str) and isinstance(facts, dict) and set(facts) <= allowed
            for key, facts in table.items()
        ),
        "invalid dictionary entry",
    )
    result.pop("evidence_encoding")
    items = result.get("items") if "items" in result else [result.get("item")]
    require(
        isinstance(items, list) and all(isinstance(item, dict) for item in items),
        "missing encoded items",
    )
    for item in items:
        require(isinstance(item.get("evidence"), list), "missing encoded evidence")
        expanded = []
        for evidence in item["evidence"]:
            require(isinstance(evidence, dict), "invalid evidence")
            if "facts_ref" not in evidence:
                expanded.append(evidence)
                continue
            reference = evidence["facts_ref"]
            require(
                isinstance(reference, str) and reference in table,
                "invalid evidence reference",
            )
            inline = {key: val for key, val in evidence.items() if key != "facts_ref"}
            facts = table[reference]
            require(not set(inline) & set(facts), "dictionary/inline conflict")
            merged = {**copy.deepcopy(facts), **inline}
            require(
                not str(merged.get("kind", "")).startswith("obs_"),
                "observation evidence cannot use dictionary",
            )
            expanded.append(merged)
        item["evidence"] = expanded
    return result


def verify_model_audit(record):
    if "model_response" not in record:
        raw = record.get("raw_response")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return  # Legacy captures predate explicit model response provenance.
        require(
            not isinstance(parsed, dict) or "evidence_encoding" not in parsed,
            "encoded response lacks raw audit provenance",
        )
        return
    raw = record["raw_response"]
    require(isinstance(raw, str), "raw encoded response must be text")
    require(
        json_equal(json.loads(raw), record["model_response"]),
        "model response differs from raw tool bytes",
    )
    require(
        record["model_response_bytes"] == len(raw.encode("utf-8")),
        "incorrect model byte measurement",
    )
    require(
        record["model_response_sha256"]
        == hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "incorrect model response hash",
    )
    audited = audit_model_payload(record["model_response"])
    tool = record["tool_name"]
    if tool == "pick_get_drama_detail" and "item" in audited:
        audited = {**audited, **audited["item"]}
    elif tool == "pick_search_knowledge" and "documents" in audited:
        audited = {**audited, "citations": audited["documents"]}
    elif audited.get("status") in (
        "rejected",
        "posted_unavailable",
        "catalog_unavailable",
    ):
        audited = {**audited, "status": "refused", "reason_code": audited["status"]}
    require(
        json_equal(audited, record["response"]),
        "audit view differs from original model payload",
    )


def verify_evidence(item, source_row, *, projected=False):
    """Stored evidence is the complete ordered signal list with item-local citations."""
    require(
        isinstance(item["item_id"], str) and item["item_id"], "missing evidence item ID"
    )
    evidence = item["evidence"]
    require(
        isinstance(evidence, list) and len(evidence) == len(source_row["signals"]),
        "evidence signal count mismatch",
    )
    for index, (actual, signal) in enumerate(zip(evidence, source_row["signals"]), 1):
        expected = {**signal, "citation_id": f"{item['item_id']}:{index}"}
        # RD03 removes only a duplicate row link from cited, non-observation
        # model evidence. Independent references and persisted evidence retain it.
        if (
            projected
            and "source_ref" not in actual
            and source_row.get("detail_url")
            and signal.get("source_ref") == source_row["detail_url"]
            and actual.get("citation_id")
            and not signal.get("kind", "").startswith("obs_")
        ):
            expected.pop("source_ref", None)
        require(
            json_equal(actual, expected),
            "evidence disagrees with independent signal facts or citation",
        )


def frozen_asof_equal(actual, frozen):
    """An explicitly frozen nullable mirror version retains key presence and type."""
    return actual == frozen and (
        "mirror_version" not in frozen
        or (
            "mirror_version" in actual
            and type(actual["mirror_version"]) is type(frozen["mirror_version"])
        )
    )


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
    if kind == "refusal" and "allowed_condition_sets" in expected:
        allowed = [conditions(x, full=True) for x in expected["allowed_condition_sets"]]
        require(allowed, "refusal condition contract must not be empty")
        requested = conditions(record["raw_arguments"]["filters"])
        intent = intent and requested in allowed
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
    for field in ("source_as_of", "published_at"):
        value = metadata[field]
        require(
            isinstance(value, str) and value not in ("", "unknown"),
            "unknown source date",
        )
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(
        isinstance(metadata["freshness"], dict) and metadata["freshness"],
        "unknown freshness",
    )
    for field in ("catalogImportedAt", "reelshortSyncedAt"):
        require(
            isinstance(metadata["freshness"].get(field), str),
            "unknown upstream freshness",
        )
        timestamp(metadata["freshness"][field])
    require(metadata["rule_version"] == "pick-rules-v1", "unsupported rule version")
    require(
        metadata["ranking_version"]
        in ("evidence-date-v1", "signal-rank-v1", "hot-evidence-date-v1"),
        "unsupported ranking version",
    )
    require(
        isinstance(metadata["frozen_data_as_of"], dict)
        and metadata["frozen_data_as_of"],
        "unknown frozen data_as_of",
    )
    frozen = metadata["frozen_data_as_of"]
    require(
        set(frozen)
        in (
            {"source_as_of", "published_at", "freshness", "scope", "shared"},
            {
                "source_as_of",
                "published_at",
                "freshness",
                "scope",
                "shared",
                "mirror_version",
            },
        ),
        "invalid frozen data_as_of schema",
    )
    require(
        all(
            frozen[k] == metadata[k]
            for k in ("source_as_of", "published_at", "freshness")
        ),
        "independent metadata disagrees with frozen metadata",
    )
    require(
        type(frozen["shared"]) is bool
        and frozen["shared"] == source["shared"]
        and isinstance(frozen["scope"], str)
        and frozen["scope"],
        "unknown data scope",
    )
    if "mirror_version" in frozen:
        require(
            frozen["mirror_version"] is None
            or (type(frozen["mirror_version"]) is int and frozen["mirror_version"] >= 1),
            "unknown mirror version",
        )
    version_fields = (
        ("knowledge_batch_id", "source_type", "shared", "knowledge_sha256")
        if kind == "knowledge"
        else ("catalog_batch_id", "source_type", "shared", "rows_sha256")
    )
    version_ok = all(record["source"].get(key) == source[key] for key in version_fields)
    try:
        verify_model_audit(record)
    except (Invalid, ValueError, TypeError, KeyError):
        layers["data_state"] = {"status": "FAIL", "reason_code": "MODEL_AUDIT_MISMATCH"}
        return layers
    response = record["response"]
    require(isinstance(response, dict), "missing typed response")
    state, dynamic = captured_state(manifest, captures, step, record)
    state_base = cap_base if dynamic else exp_base
    excluded, selected, chain = exclusions(state, state_base, record, c or DEFAULTS)
    if (
        kind in ("detail", "prepare_save", "prepare_only")
        and "bound_result_file" in record
    ):
        exported = file_ref(cap_base, record, "bound_result")
        bound = exported["result"]
        require(
            bound["id"] == expected["result_id"]
            and bound["owner_id"] == record["owner_id"]
            and bound["thread_id"] == record["thread_id"],
            "new frozen result identity mismatch",
        )
        require(
            timestamp(bound["created_at"]) <= timestamp(record["started_at"])
            and timestamp(bound["created_at"]) <= timestamp(exported["captured_at"]),
            "invalid frozen output chronology",
        )
        sealed_parent = next(
            (parent for parent in chain if parent["id"] == bound["id"]), None
        )
        if sealed_parent is not None:
            require(
                timestamp(bound["created_at"])
                <= timestamp(state["captured_at"])
                <= timestamp(exported["captured_at"]),
                "existing result predates sealed state and refreshed export",
            )
            # GET /results omits the parent-chain edge. Its independently sealed
            # value stays authoritative; every field the GET does carry must
            # match, and no other missing/additional fields are tolerated.
            refreshed = dict(bound)
            if (
                "parent_result_id" not in refreshed
                and "parent_result_id" in sealed_parent
            ):
                refreshed["parent_result_id"] = sealed_parent["parent_result_id"]
            require(
                json.dumps(refreshed, sort_keys=True, ensure_ascii=False)
                == json.dumps(sealed_parent, sort_keys=True, ensure_ascii=False),
                "refreshed result disagrees with sealed parent",
            )
        else:
            prior = captures["records"][: captures["records"].index(record)]
            producers = [
                r
                for r in prior
                if r.get("record_kind", "tool") == "tool"
                and r["run_id"] == record["run_id"]
                and r["owner_id"] == record["owner_id"]
                and r["thread_id"] == record["thread_id"]
                and r.get("response", {}).get("id") == bound["id"]
            ]
            require(len(producers) == 1, "new frozen result needs one prior producer")
            require(
                timestamp(producers[0]["started_at"])
                <= timestamp(bound["created_at"])
                <= timestamp(record["started_at"]),
                "producer result consumer chronology contradiction",
            )
            produced = producers[0]["response"]
            require(
                [(i["item_id"], i["identity"]) for i in bound["items"]]
                == [(i["item_id"], i["identity"]) for i in produced["items"]],
                "frozen output item binding mismatch",
            )
            require(
                conditions(bound["conditions"]) == conditions(produced["conditions"]),
                "frozen output condition mismatch",
            )
            by_identity = {r["identity"]: r for r in rows}
            for item, projected_item in zip(bound["items"], produced["items"]):
                source_row = by_identity[item["identity"]]
                verify_evidence(item, source_row)
                verify_evidence(projected_item, source_row, projected=True)
                require(
                    all(
                        item[field] == source_row[field]
                        for field in set(item) & set(source_row)
                    ),
                    "frozen output fact disagrees with independent catalog",
                )
            chain = [bound]
    if kind in ("detail", "prepare_save", "prepare_only"):
        bound_parent = next(
            (parent for parent in chain if parent["id"] == expected["result_id"]), None
        )
        require(bound_parent is not None, "missing frozen bound parent")
        source_rows = {row["identity"]: row for row in rows}
        for frozen_item in bound_parent["items"]:
            if "evidence" in frozen_item:
                verify_evidence(frozen_item, source_rows[frozen_item["identity"]])
        version_ok = (
            version_ok
            and bound_parent["catalog_batch_id"] == source["catalog_batch_id"]
            and all(
                bound_parent["source"].get(key) == source[key]
                for key in ("catalog_batch_id", "source_type", "shared", "rows_sha256")
            )
        )
        require(
            bound_parent.get("data_as_of")
            and bound_parent.get("rule_version")
            and bound_parent.get("ranking_version"),
            "missing parent frozen metadata",
        )
        version_ok = (
            version_ok
            and frozen_asof_equal(bound_parent["data_as_of"], frozen)
            and all(
                bound_parent[k] == metadata[k]
                for k in ("rule_version", "ranking_version")
            )
        )
    ok = True
    if kind in ("query", "count", "detail") or "data_as_of" in response:
        require(
            isinstance(response.get("data_as_of"), dict), "missing frozen data_as_of"
        )
        ok = frozen_asof_equal(response["data_as_of"], frozen)
    if kind == "query":
        require(
            response.get("rule_version") and response.get("ranking_version"),
            "missing result versions",
        )
        ok = (
            ok
            and response["rule_version"] == metadata["rule_version"]
            and response["ranking_version"] == metadata["ranking_version"]
        )
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
        returned_conditions = conditions(response["conditions"])
        if (
            returned_conditions not in allowed
            or returned_conditions != actual
            or returned_conditions != requested
        ):
            layers["intent"] = {
                "status": "FAIL",
                "reason_code": "RESPONSE_CONDITIONS_CONTRADICTION",
            }
        matches = match_rows(rows, c, excluded)
        if kind == "query":
            nonnegative_int(response["matched_total"])
            require(
                response.get("id") and isinstance(response.get("items"), list),
                "missing query result",
            )
            ok = (
                ok
                and [x["identity"] for x in response["items"]]
                == [x["identity"] for x in matches[: c["limit"]]]
                and response["matched_total"] == len(matches)
            )
        else:
            nonnegative_int(response["total"])
            ok = ok and response["total"] == len(matches)
            for field in ("theater", "language"):
                require(
                    isinstance(response["by_" + field], dict), "invalid count groups"
                )
                for count in response["by_" + field].values():
                    nonnegative_int(count)
                ok = ok and response["by_" + field] == dict(
                    Counter(r[field] for r in matches)
                )
        if not matches:
            actual_diagnosis = response["zero_diagnosis"]
            nonnegative_int(actual_diagnosis["catalog_rows"])
            nonnegative_int(actual_diagnosis["delisted_rows"])
            for relaxation in actual_diagnosis["without_each"]:
                if relaxation["matched_total"] is not None:
                    nonnegative_int(relaxation["matched_total"])
                if relaxation["condition"] == "excluded":
                    nonnegative_int(relaxation["value"])
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
        raw = record["raw_arguments"]
        if (
            raw.get("result_id") != expected["result_id"]
            or raw.get("item_id") != expected["item_id"]
        ):
            layers["intent"] = {"status": "FAIL", "reason_code": "DETAIL_RAW_BINDING"}
        ok = ok and (
            response["result_id"] == expected["result_id"]
            and response["item_id"] == expected["item_id"]
            and response["identity"] == item["identity"]
        )
        source_row = next(row for row in rows if row["identity"] == item["identity"])
        if "evidence" in item:
            verify_evidence(item, source_row)
        if "evidence" in response:
            verify_evidence(response, source_row, projected=True)
        for key in expected["fact_fields"]:
            if key == "evidence":
                require(
                    "evidence" in item and "evidence" in response,
                    "missing declared evidence",
                )
            else:
                ok = ok and response[key] == item[key]
    elif kind in ("prepare_save", "prepare_only"):
        require(
            expected.get("result_id")
            and expected.get("item_ids")
            and (kind == "prepare_only" or expected.get("request_id"))
            and "note" in expected,
            "missing save expectation",
        )
        require(
            chain and chain[0]["id"] == expected["result_id"],
            "save requires frozen result",
        )
        raw = record["raw_arguments"]
        target = raw.get("result_id") or record.get("bound_result_id")
        chosen = raw.get("item_ids")
        if raw.get("positions") is not None:
            require(chosen is None, "positions and item_ids conflict")
            positions = raw["positions"]
            require(
                positions
                and all(
                    type(i) is int and 1 <= i <= len(chain[0]["items"])
                    for i in positions
                ),
                "invalid positions",
            )
            chosen = [chain[0]["items"][i - 1]["item_id"] for i in positions]
        elif chosen is None:
            require(target == record.get("bound_result_id"), "unbound UI selection")
            chosen = state["selected_item_ids"]
        require(
            isinstance(chosen, list)
            and chosen
            and all(isinstance(item_id, str) for item_id in chosen),
            "invalid chosen item IDs",
        )
        require(
            set(chosen).issubset({item["item_id"] for item in chain[0]["items"]}),
            "unknown chosen item ID",
        )
        # Product preserves frozen item order and deduplicates requested IDs.
        ordered_chosen = [
            i["item_id"] for i in chain[0]["items"] if i["item_id"] in chosen
        ]
        if (
            target != expected["result_id"]
            or ordered_chosen != expected["item_ids"]
            or raw.get("note", "") != expected["note"]
        ):
            layers["intent"] = {"status": "FAIL", "reason_code": "SAVE_RAW_BINDING"}
        ok = (
            response["requires_confirmation"] is True
            and response["result_id"] == expected["result_id"]
            and response["item_ids"] == expected["item_ids"]
            and response["note"] == expected["note"]
        )
        after_prepare = file_ref(cap_base, record, "selections_after_prepare")
        after = file_ref(cap_base, record, "selections_after")
        if kind == "prepare_only":
            require(
                expected.get("requires_confirmation") is True
                and "request_id" not in expected
                and not step.get("save"),
                "prepare-only must prelock no confirmation/write",
            )
            observation = file_ref(cap_base, record, "prepare_only_observation")
            require(
                all(
                    observation.get(key) == record[key]
                    for key in ("owner_id", "thread_id", "run_id")
                )
                and observation.get("all_business_mutations_blocked") is True,
                "prepare-only mutation observation missing",
            )
            require(
                timestamp(observation["started_at"])
                <= timestamp(record["started_at"])
                <= timestamp(observation["finished_at"]),
                "prepare-only observation does not cover tool call",
            )
            requests = file_ref(cap_base, record, "save_requests")
            receipts = file_ref(cap_base, record, "receipts")
            ok = (
                ok
                and json_equal(after_prepare, selected)
                and json_equal(after, selected)
                and requests == []
                and receipts == []
            )
            ok = ok and not any(
                key in response or key in record
                for key in ("request_id", "saved", "receipt")
            )
        else:
            receipts = file_ref(cap_base, record, "receipts")
            request_id = save_request_id(expected, record, cap_base)
            ok = ok and after_prepare == selected and len(receipts) >= 2
            items = [
                i for i in chain[0]["items"] if i["item_id"] in expected["item_ids"]
            ]
            require(
                len(items) == len(expected["item_ids"]), "save items not in snapshot"
            )

            def state_map(values):
                require(
                    len({x["id"] for x in values}) == len(values)
                    and len({x["identity"] for x in values}) == len(values),
                    "duplicate selections",
                )
                for row in values:
                    require(
                        row["owner_id"] == record["owner_id"]
                        and row["state"] in ("selected", "removed")
                        and type(row["version"]) is int
                        and row["version"] >= 1,
                        "invalid selection state",
                    )
                    require(
                        all(
                            k in row
                            for k in (
                                "source_result_id",
                                "source_item_id",
                                "note",
                                "snapshot_json",
                            )
                        ),
                        "incomplete selection state",
                    )
                return {x["identity"]: x for x in values}

            before_map, after_map = state_map(selected), state_map(after)
            ok = ok and set(after_map) == set(before_map) | {
                i["identity"] for i in items
            }
            saved = receipts[0]["saved"]
            ok = ok and all(
                receipt == receipts[0] and receipt["request_id"] == request_id
                for receipt in receipts
            )
            ok = ok and [r["identity"] for r in saved] == [i["identity"] for i in items]
            for item, receipt in zip(items, saved):
                current, previous = (
                    after_map.get(item["identity"]),
                    before_map.get(item["identity"]),
                )
                require(current is not None, "saved row missing")
                expected_status = (
                    "created"
                    if previous is None
                    else "existing"
                    if previous["state"] == "selected"
                    else "restored"
                )
                version = (
                    1
                    if previous is None
                    else previous["version"] + (expected_status == "restored")
                )
                ok = (
                    ok
                    and receipt
                    == {
                        "id": current["id"],
                        "identity": item["identity"],
                        "status": expected_status,
                        "version": version,
                    }
                    and current["version"] == version
                )
                if previous:
                    ok = ok and current["id"] == previous["id"]
                if expected_status == "existing":
                    ok = ok and current == previous
                else:
                    ok = (
                        ok
                        and current["state"] == "selected"
                        and current["source_result_id"] == expected["result_id"]
                        and current["source_item_id"] == item["item_id"]
                        and current["note"] == expected["note"]
                        and current["snapshot_json"] == item
                    )
            for identity in set(before_map) - {i["identity"] for i in items}:
                ok = ok and before_map[identity] == after_map[identity]
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
        require(
            all(x["batch_id"] == source["knowledge_batch_id"] for x in knowledge),
            "independent knowledge batch mismatch",
        )
        by_id = {x["citation_id"]: x for x in knowledge}
        require(len(by_id) == len(knowledge), "duplicate independent citation")
        ok = (
            ok
            and [x["citation_id"] for x in response["citations"]]
            == expected["citation_ids"]
        )
        ok = ok and all(x == by_id.get(x["citation_id"]) for x in response["citations"])
    layers["data_state"] = {
        "status": ("PASS" if ok else "FAIL") if version_ok else "UNVERIFIED",
        "reason_code": "INDEPENDENT_ORACLE"
        if version_ok
        else "UNVERIFIED_DATA_VERSION",
    }
    try:
        layers.update(check_external(record, expected, cap_base, captures))
    except (
        Invalid,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        AttributeError,
    ):
        for name in ("browser", "model_explanation", "performance_cost"):
            layers[name] = {
                "status": "UNVERIFIED",
                "reason_code": "INVALID_EXTERNAL_EVIDENCE",
            }
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
        require(
            len(
                {
                    (a["case_id"], a["step_id"], a["attempt_id"], a["run_id"])
                    for a in ledger
                }
            )
            == len(ledger),
            "duplicate ledger entry",
        )
        for attempt in ledger:
            key = (attempt["case_id"], attempt["step_id"])
            require(key in steps, "unplanned ledger step")
            start, end = (
                timestamp(attempt["started_at"]),
                timestamp(attempt["ended_at"]),
            )
            require(
                timestamp(manifest["locked_at"])
                <= timestamp(captures["started_at"])
                <= start
                <= end,
                "invalid ledger chronology",
            )
            calls = [
                r
                for r in records
                if all(
                    r[k] == attempt[k]
                    for k in ("case_id", "step_id", "attempt_id", "run_id")
                )
            ]
            call_times = [
                timestamp(r["started_at"])
                for r in calls
                if r.get("record_kind", "tool") == "tool"
            ]
            require(
                call_times == sorted(call_times),
                "tool chronology contradicts authoritative inventory",
            )
            actual_ids = [
                r["tool_call_id"]
                for r in calls
                if r.get("record_kind", "tool") == "tool"
            ]
            inventory = attempt["tool_call_ids"]
            require(
                isinstance(inventory, list) and len(inventory) == len(set(inventory)),
                "invalid tool call inventory",
            )
            if actual_ids != inventory:
                report["checks"].append(
                    {
                        "case_id": key[0],
                        "step_id": key[1],
                        "status": "UNVERIFIED",
                        "reason_code": "INCOMPLETE_TOOL_CALL_CAPTURE",
                    }
                )
            terminals = [r for r in calls if r.get("record_kind") == "terminal"]
            if (
                not inventory
                or "terminal_contract" in steps[key]
                or "attempt_terminal_statuses" in steps[key]
            ):
                if len(terminals) != 1:
                    report["checks"].append(
                        {
                            "case_id": key[0],
                            "step_id": key[1],
                            "status": "UNVERIFIED",
                            "reason_code": "MISSING_UNIQUE_TERMINAL_CAPTURE",
                        }
                    )
            expected_status = attempt_status(steps[key], attempt, ledger)
            if attempt["status"] != expected_status or any(
                r["terminal_status"] != attempt["status"] for r in calls
            ):
                report["checks"].append(
                    {
                        "case_id": key[0],
                        "step_id": key[1],
                        "status": "FAIL",
                        "reason_code": "AUTHORITATIVE_RUN_STATUS",
                    }
                )
            for record in calls:
                require(
                    start <= timestamp(record["started_at"]) <= end,
                    "record outside authoritative run interval",
                )
                state, _ = captured_state(manifest, captures, steps[key], record)
                require(
                    timestamp(state["captured_at"]) <= start,
                    "pre-state captured after execution started",
                )
        seen_calls = set()
        for record in records:
            key = (record["case_id"], record.get("step_id", "main"))
            require(key in steps, "unexpected capture step")
            call_key = (
                record["run_id"],
                record.get("record_kind", "tool"),
                record.get("tool_call_id", "terminal"),
            )
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
            resolved = steps[key]
            try:
                resolved = typed_step(steps[key], record, ledger, captures, steps)
                check = (
                    check_terminal
                    if record.get("record_kind") == "terminal"
                    else check_record
                )
                layers = check(
                    resolved,
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
                expected = resolved.get("expected", {})
                wrong_outcome = record.get(
                    "record_kind", "tool"
                ) == "tool" and record.get("outcome") != expected.get(
                    "expected_outcome"
                )
                if (
                    str(error) == "unplanned tool call"
                    or wrong_outcome
                    or record.get("terminal_status")
                    != expected.get("terminal_status", "success")
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
            step = steps[key]
            if "attempt_terminal_statuses" in step and len(attempts) != len(
                step["attempt_terminal_statuses"]
            ):
                report["checks"].append(
                    {
                        "case_id": key[0],
                        "step_id": key[1],
                        "status": "NOT_RUN",
                        "reason_code": "MISSING_PLANNED_ATTEMPT",
                    }
                )
            if "tool_contracts" in step:
                contracts = step["tool_contracts"]
                names = [contract["tool_name"] for contract in contracts]
                require(
                    names and len(names) == len(set(names)),
                    "tool contracts need unique names",
                )
                require(
                    all(
                        contract["expected"]["allowed_actions"]
                        == [contract["tool_name"]]
                        for contract in contracts
                    ),
                    "tool contract action mismatch",
                )
                emitted = [
                    r["tool_name"]
                    for r in records
                    if (r["case_id"], r["step_id"]) == key
                    and r.get("record_kind", "tool") == "tool"
                ]
                for contract in contracts:
                    nonnegative_int(contract["min_occurrences"])
                    if (
                        emitted.count(contract["tool_name"])
                        < contract["min_occurrences"]
                    ):
                        report["checks"].append(
                            {
                                "case_id": key[0],
                                "step_id": key[1],
                                "status": "NOT_RUN",
                                "reason_code": "MISSING_REQUIRED_TOOL",
                            }
                        )
                wrong_order = any(name not in names for name in emitted)
                for attempt in attempts:
                    sequence = [
                        names.index(r["tool_name"])
                        for r in records
                        if r.get("record_kind", "tool") == "tool"
                        and r["tool_name"] in names
                        and all(
                            r[k] == attempt[k]
                            for k in ("case_id", "step_id", "attempt_id", "run_id")
                        )
                    ]
                    wrong_order = wrong_order or sequence != sorted(sequence)
                if wrong_order:
                    report["checks"].append(
                        {
                            "case_id": key[0],
                            "step_id": key[1],
                            "status": "FAIL",
                            "reason_code": "LOCKED_TOOL_ORDER",
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
