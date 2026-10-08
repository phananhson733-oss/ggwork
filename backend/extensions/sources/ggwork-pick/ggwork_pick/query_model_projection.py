"""Project captured common-query facts for model context and the read-only card."""

import base64
import json
import re
from datetime import date

from pydantic import ValidationError

from ggwork_pick.query_evidence import _reference, bill_identity, source_key
from ggwork_pick.query_model_contracts import MODEL_BYTE_LIMIT, MODEL_PAGE_LIMIT, MODEL_SIGNAL_LIMIT, QueryModelProjection
from ggwork_pick.query_rank_metric import selected_metric
from ggwork_pick.query_reader import QueryFailure


def _date(value):
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _numeric(value):
    if type(value) in (int, float):
        return value
    if isinstance(value, str) and len(value) <= 64 and re.fullmatch(r"-?\d+(?:\.\d+)?", value, flags=re.ASCII):
        return value
    return None


def _drama(row, call_id, request):
    drama = row["drama"]
    ref = _reference(call_id, row["identity"])
    signals = list(enumerate(drama["signals"]))
    selected = request.get("signal_kind") or request.get("rank")
    if selected:
        signals.sort(key=lambda pair: pair[1]["kind"] != selected)
    projected = [
        {
            "kind": signal["kind"],
            "observed_at": signal["observed_at"],
            "value": _numeric(signal.get("value")),
            "rank": signal["rank"],
            "grade": signal["grade"],
            "reference": f"{ref}:signal:{index}",
        }
        for index, signal in signals[:MODEL_SIGNAL_LIMIT]
    ]
    fields = {key: drama[key] for key in ("source", "source_id", "title", "language", "theater", "availability", "channel_rules")}
    return {
        "kind": "drama",
        "identity": row["identity"],
        "reference": ref,
        **fields,
        "posted_status": row["posted_status"],
        "posted_scope_complete": row["posted_scope_complete"],
        "signals": projected,
        "signal_count": len(signals),
        "signals_truncated": len(signals) > len(projected),
    }


def _rows(payload, call_id):
    request, board = payload["request"], payload.get("board") or {}
    if request["domain"] == "rules":
        result = []
        for platform, rule in (board.get("rules") or {}).get("platformRules", {}).items():
            name = rule.get("name") if isinstance(rule.get("name"), str) else ""
            yt = rule.get("yt")
            result.append(
                {
                    "kind": "rule",
                    "identity": platform,
                    "reference": _reference(call_id, "platform:" + platform),
                    "platform": platform,
                    "name": name[:500],
                    "name_truncated": len(name) > 500,
                    "youtube_rule": yt if yt in {"ok", "only", "warn", "no"} else None,
                }
            )
        return result
    if request["domain"] == "posted":
        return [
            {
                "kind": "posted",
                "identity": r["sd"],
                "reference": _reference(call_id, "posted:" + r["sd"]),
                "sd": r["sd"],
                "title": r["title"][:500],
                "title_truncated": len(r["title"]) > 500,
                "archived": r["archived"],
                "post_count": r["post_count"],
                "sched_count": r["sched_count"],
                "last_post_on": _date(r["last_post_on"]),
                "accounts": [v[:200] for v in r["accounts"][:5]],
                "account_count": len(r["accounts"]),
                "accounts_truncated": len(r["accounts"]) > 5 or any(len(v) > 200 for v in r["accounts"][:5]),
            }
            for r in board.get("posted", [])
        ]
    if request.get("rank") == "rs_ledger":
        result = []
        for row in board.get("bill_rows", []):
            identity = bill_identity(row)
            result.append(
                {
                    "kind": "bill",
                    "identity": identity,
                    "reference": _reference(call_id, identity),
                    "book_id": row["book_id"],
                    "canonical_id": row["canonical_id"],
                    "bill_date": row["bill_date"],
                    "title": row["title"][:500],
                    "title_truncated": len(row["title"]) > 500,
                    "promotion_type": row["promotion_type"],
                    "order_cnt": row["order_cnt"],
                }
            )
        return result
    if board.get("row_keys"):
        facts = {source_key(row): row for row in payload["rows"]}
        records = {row["row_key"]: (table, row) for table in ("catalog_rows", "rs_rows") for row in board.get(table, [])}
        result = []
        for key in board["row_keys"]:
            if key in facts and not (key in records and not records[key][1]["lang"]):
                result.append(_drama(facts[key], call_id, request))
            elif key in records:
                table, row = records[key]
                result.append(
                    {
                        "kind": "catalog_record",
                        "identity": None,
                        "row_key": key,
                        "source_table": table,
                        "reference": _reference(call_id, f"catalog:{table}:{key}"),
                        "source_ref": f"mirror:{payload['pin']['mirror_version']}:{table}:{key}",
                        "title": row["title"][:500],
                        "title_truncated": len(row["title"]) > 500,
                        "language": row["lang"],
                        "theater": row["platform"],
                        "listed_on": _date(row["listed_on"]),
                        "availability": "delisted" if row["off_on"] else "unknown",
                        "eligibility": "unknown",
                    }
                )
        metrics = {row["row_key"]: row for row in board.get("rs_rows", [])}
        for projected, key in zip(result, board["row_keys"], strict=True):
            if key in metrics:
                projected["rank_metric"] = selected_metric(metrics[key], board.get("effective_sort"), _reference(call_id, "rs:" + key))
        return result
    return [_drama(row, call_id, request) for row in payload["rows"]]


def model_projection(payload, *, call_id, requested_limit):
    try:
        return _model_projection(payload, call_id=call_id, requested_limit=requested_limit)
    except ValidationError:
        raise QueryFailure("source_unavailable", "来源字段无法生成有界查询投影") from None


def _model_projection(payload, *, call_id, requested_limit):
    rows = _rows(payload, call_id)
    request = payload["request"]
    # Rules are metadata in the HTTP contract, so their model pagination is explicit here.
    start = request["offset"] if request["domain"] == "rules" else 0
    page = rows[start : start + min(MODEL_PAGE_LIMIT, request["limit"])]
    available = max(0, len(rows) - start) if request["domain"] == "rules" else payload["counts"]["returned"]
    source_positions = {}
    board = payload.get("board") or {}
    keys = board.get("row_keys", [])
    for index, row in enumerate(rows):
        key = row.get("row_key") or row.get("sd") or row.get("source_id")
        if row.get("source") == "realshort-pick":
            try:
                key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
            except (ValueError, UnicodeError):
                key = None
        source_positions[row["reference"]] = keys.index(key) if key in keys else index
    base = {key: payload[key] for key in ("request", "pin", "actual_period", "source_as_of", "mirror_synced_at", "order_version", "counts")}
    base.update(
        projection_version="pick-query-model-v1",
        query_next_offset=payload["next_offset"],
        query_truncated=payload["truncated"],
        period_resolution=(payload.get("period_options") or {}).get("resolution"),
        effective_sort=board.get("effective_sort"),
        rank_limit=board.get("rank_limit"),
    )
    while True:
        shown = len(page)
        omitted = max(0, available - shown)
        signals_omitted = sum(row.get("signal_count", 0) - len(row.get("signals", [])) for row in page)
        byte_omitted = len(rows[start:]) - shown
        next_offset = (
            (request["offset"] + source_positions[page[-1]["reference"]] + 1 if page else payload["next_offset"]) if byte_omitted else payload["next_offset"]
        )
        if request["domain"] == "rules":
            next_offset = request["offset"] + shown if omitted else None
        body = QueryModelProjection(
            **base,
            rows=page,
            projection={
                "requested_limit": requested_limit,
                "shown": shown,
                "available_count": max(0, available),
                "omitted_rows": omitted,
                "signals_omitted": signals_omitted,
                "next_offset": next_offset,
                "truncated": bool(omitted or signals_omitted or payload["truncated"]),
            },
        ).model_dump(mode="json")
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) <= MODEL_BYTE_LIMIT:
            return body
        if not page:
            raise QueryFailure("source_unavailable", "查询条件过大，无法生成有界模型投影")
        page.pop()
