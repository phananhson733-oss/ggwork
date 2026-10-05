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
            require(document["source"] == record["source"], "browser source mismatch")
            result_id = record["response"].get(
                "result_id", record["response"].get("id")
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
            type(frozen["mirror_version"]) is int and frozen["mirror_version"] >= 1,
            "unknown mirror version",
        )
    version_fields = (
        ("knowledge_batch_id", "source_type", "shared", "knowledge_sha256")
        if kind == "knowledge"
        else ("catalog_batch_id", "source_type", "shared", "rows_sha256")
    )
    version_ok = all(record["source"].get(key) == source[key] for key in version_fields)
    response = record["response"]
    require(isinstance(response, dict), "missing typed response")
    state, dynamic = captured_state(manifest, captures, step, record)
    state_base = cap_base if dynamic else exp_base
    excluded, selected, chain = exclusions(state, state_base, record, c or DEFAULTS)
    if kind in ("detail", "prepare_save"):
        bound_parent = next(
            (parent for parent in chain if parent["id"] == expected["result_id"]), None
        )
        require(bound_parent is not None, "missing frozen bound parent")
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
            and bound_parent["data_as_of"] == frozen
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
        ok = response["data_as_of"] == metadata["frozen_data_as_of"]
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
        receipts = file_ref(cap_base, record, "receipts")
        ok = ok and after_prepare == selected and len(receipts) >= 2
        items = [i for i in chain[0]["items"] if i["item_id"] in expected["item_ids"]]
        require(len(items) == len(expected["item_ids"]), "save items not in snapshot")

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
        ok = ok and set(after_map) == set(before_map) | {i["identity"] for i in items}
        saved = receipts[0]["saved"]
        ok = ok and all(
            receipt == receipts[0] and receipt["request_id"] == expected["request_id"]
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
            actual_ids = [r["tool_call_id"] for r in calls]
            inventory = attempt["tool_call_ids"]
            require(
                isinstance(inventory, list) and len(inventory) == len(set(inventory)),
                "invalid tool call inventory",
            )
            if Counter(actual_ids) != Counter(inventory):
                report["checks"].append(
                    {
                        "case_id": key[0],
                        "step_id": key[1],
                        "status": "UNVERIFIED",
                        "reason_code": "INCOMPLETE_TOOL_CALL_CAPTURE",
                    }
                )
            expected_status = steps[key]["expected"].get("terminal_status", "success")
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
