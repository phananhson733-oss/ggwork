"""Model-only copies of query/detail payloads; persistence and HTTP retain full evidence.

RealShort's feed repeats each row's detail_url in its signals' source_ref. citation_id
still maps those signals to the full stored evidence. Distinct references (including
obs references) remain. Repeated non-obs facts use a reversible payload-local dictionary;
unknown fields stay inline, and reserved-name collisions get an explicit inline envelope.
"""

import json
from copy import deepcopy

_FACTS = ("kind", "label", "observed_at", "rank", "value", "grade", "note")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dictionary(payload: dict, items: list[dict]) -> dict:
    """Factor equal known facts only; unknown fields and obs contracts stay inline."""
    if any(key in payload for key in ("evidence_encoding", "evidence_facts")) or any(
        "facts_ref" in evidence for item in items for evidence in item.get("evidence", [])
    ):
        # Even a valid-looking preexisting marker is input data, not our codec. An
        # explicit outer inline envelope avoids overwriting or interpreting its names.
        return {**{key: payload[key] for key in ("id", "result_id") if key in payload}, "evidence_encoding": "inline-v1", "inline_payload": payload}
    encoded = deepcopy(payload)
    encoded_items = encoded["items"] if "items" in encoded else [encoded["item"]]
    groups: dict[str, list[dict]] = {}
    for item in encoded_items:
        for evidence in item.get("evidence", []):
            kind = evidence.get("kind")
            if isinstance(kind, str) and kind and not kind.startswith("obs_"):
                groups.setdefault(kind, []).append(evidence)
    table = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        first = group[0]
        common = {
            key: first[key] for key in _FACTS if key in first and all(key in evidence and _json(evidence[key]) == _json(first[key]) for evidence in group)
        }
        reference = str(len(table))
        table[reference] = common
        for evidence in group:
            for key in common:
                del evidence[key]
            evidence["facts_ref"] = reference
    if not table:
        return payload
    encoded["evidence_encoding"] = "facts-ref-v1"
    encoded["evidence_facts"] = table
    # Do not add dictionary overhead to small or heterogeneous detail responses.
    return encoded if len(_json(encoded).encode("utf-8")) < len(_json(payload).encode("utf-8")) else payload


def model_payload(payload: dict) -> dict:
    """Copy, remove permitted duplicate display links, then encode repeated facts losslessly."""
    projected = deepcopy(payload)
    items = projected.get("items", []) if "items" in projected else [projected["item"]]
    for item in items:
        row_link = item.pop("detail_url", None)
        for evidence in item.get("evidence", []):
            if row_link and evidence.get("source_ref") == row_link and evidence.get("citation_id") and not evidence.get("kind", "").startswith("obs_"):
                del evidence["source_ref"]
    return _dictionary(projected, items)
