"""A candidate item's row facts: its tags, listing date and channel rules (evaluation batch 2, 2026-10-05).

The batch row has them; the stored item never did, so neither the model nor the card could say what genre a drama
is, when it was listed or whether a channel allows it. They are read back from the result's own frozen batch, never
stored with the item: the stored snapshot is what saves and replays parse, and the frontend parses it strictly.
"""

FACT_KEYS = ("tags", "listed_at", "channel_rules")


def item_facts(row: dict) -> dict:
    """The row's facts as new values; the row itself is shared with the batch cache."""
    return {
        "tags": list(row.get("tags") or ()),
        "listed_at": row.get("listed_at"),
        "channel_rules": dict(row.get("channel_rules") or {}),
    }


def facts_by_item(items: list[dict], rows: list[dict]) -> dict[str, dict]:
    """item_id -> facts for every item whose row the batch holds."""
    by_identity = {row["identity"]: row for row in rows}
    return {item["item_id"]: item_facts(by_identity[item["identity"]]) for item in items if item["identity"] in by_identity}


def with_facts(items: list[dict], rows: list[dict]) -> list[dict]:
    """New item dicts with their row facts merged in, for the model's view only."""
    facts = facts_by_item(items, rows)
    return [{**item, **facts[item["item_id"]]} if item["item_id"] in facts else dict(item) for item in items]
