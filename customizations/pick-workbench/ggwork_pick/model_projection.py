"""Model-only copies of query/detail payloads; persistence and HTTP retain full evidence.

RealShort's feed repeats each row's detail_url in its signals' source_ref. citation_id
still maps those signals to the full stored evidence. Distinct references (including
obs references) remain. Unknown fields stay intact pending explicit metadata review.
"""

from copy import deepcopy


def model_payload(payload: dict) -> dict:
    """Remove only display links and their exact evidence duplicates, without aliasing input."""
    projected = deepcopy(payload)
    items = projected.get("items", []) if "items" in projected else [projected["item"]]
    for item in items:
        row_link = item.pop("detail_url", None)
        for evidence in item.get("evidence", []):
            if row_link and evidence.get("source_ref") == row_link and evidence.get("citation_id") and not evidence.get("kind", "").startswith("obs_"):
                del evidence["source_ref"]
    return projected
