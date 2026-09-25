"""The observation radar's pick_obs views (plan TR-33, design 3.4 and 3.6): names, columns, types and row models.

Part of the shared data contract; see contract.py for the rules the four modules follow and the document they match. TR-11
creates the views from these columns; TR-24 reads them.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, create_model

from ggwork_pick.observe.contract import Frozen, ViewType

# ---- the pick_obs views (design 3.4, 3.6) --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ViewColumn:
    name: str
    type: ViewType
    nullable: bool


def _columns(*definitions: str) -> tuple[ViewColumn, ...]:
    """Columns from definitions written name:type, or name:type? for a nullable column."""
    parsed = (definition.partition(":") for definition in definitions)
    return tuple(ViewColumn(name, kind.rstrip("?"), kind.endswith("?")) for name, _, kind in parsed)


_STATE_COLUMNS = (
    "row_id:int", "set_id:text", "channel:text", "mode:text", "identity:text", "title:text", "language:text", "theater:text", "scope:text",
    "window_kind:text", "state:text", "confirmation:text?", "admission:text?", "labels:json", "tier:text?", "correspondence:text?",
    "id_evidence:text?", "ambiguity:text?", "flags:json", "carried_over:bool", "stale:bool", "window_end:text", "latest_block_end:text?",
    "metrics:json", "quality_note:json?", "paste_row:json?", "created_at:text",
)  # fmt: skip
VIEW_COLUMNS = MappingProxyType(
    {
        "sets": _columns(
            "set_id:text",
            "channel:text",
            "mode:text",
            "status:text",
            "published_at:text",
            "as_of:text",
            "source_catalog_batch_id:text",
            "collector_version:text",
            "rules_version:text",
            "link_rules_version:text",
            "alias_version:int",
            "decisions_version:int",
            "target_date:text?",
            "window_end:text?",
            "round_id:text?",
            "frozen_inputs:json",
            "summary:json",
        ),
        "states": _columns(*_STATE_COLUMNS),
        "links": _columns(
            "id:int",
            "link_rules_version:text",
            "trends_set_id:text",
            "gsc_set_id:text",
            "mode:text",
            "identity:text",
            "country:text?",
            "trends_geo:text?",
            "label:text",
            "trends_row_id:int?",
            "gsc_row_id:int?",
            "trends_anchor:text",
            "gsc_anchor:text",
            "pair_gap_minutes:int",
            "timely:bool",
            "published_gap_minutes:int",
            "stale:bool",
            "created_at:text",
        ),
        "discoveries": _columns(
            "discovery_id:int",
            "set_id:text",
            "mode:text",
            "geo:text",
            "seed:text",
            "property:text",
            "term:text",
            "normalized_term:text",
            "language:text?",
            "match_status:text",
            "route:text",
            "matched_identity:text?",
            "breakout:bool",
            "first_seen_at:text",
        ),
        "alias_queue": _columns(
            "alias_id:int",
            "alias_version:int",
            "old_identity:text",
            "new_identity:text",
            "status:text",
            "platform:text",
            "language:text",
            "old_title:text",
            "new_title:text",
            "evidence:json",
            "created_at:text",
        ),
        "confirm_queue": _columns(
            "identity:text",
            "title:text",
            "platform:text",
            "normalized_title:text",
            "language:text",
            "geo:text",
            "state:text",
            "confirmation:text?",
            "id_evidence:text",
            "set_id:text",
            "mode:text",
            "since:text",
        ),
        "alerts": _columns(
            "id:int",
            "mode:text",
            "channel:text",
            "set_id:text",
            "state_row_id:int",
            "identity:text",
            "root_identity:text",
            "state:text",
            "scope:text",
            "dedupe_key:text",
            "published_at:text",
            "eval_rules_version:text",
            "alias_version:int",
            "evidence:json",
            "created_at:text",
        ),
        "run_status": _columns(
            "channel:text",
            "batch_id:text",
            "mode:text",
            "target_date:text?",
            "round_id:text?",
            "started_at:text",
            "finished_at:text?",
            "outcome:text",
            "requests:int",
            "published_set_id:text?",
            "status_codes:json",
        ),
    }
)
VIEW_NAMES = tuple(VIEW_COLUMNS)
# What information_schema.columns.data_type may say for each contract type (TR-11's view test).
VIEW_PG_TYPES = MappingProxyType(
    {"text": ("text", "character varying"), "int": ("integer", "bigint"), "bool": ("boolean",), "json": ("json",), "float": ("double precision",)}
)
_VIEW_PYTHON_TYPES = MappingProxyType(
    {"text": StrictStr, "int": StrictInt, "bool": StrictBool, "json": dict[str, Any] | list[Any], "float": StrictFloat | StrictInt}
)


def _row_model(view: str, columns: tuple[ViewColumn, ...]) -> type[Frozen]:
    fields = {c.name: ((_VIEW_PYTHON_TYPES[c.type] | None) if c.nullable else _VIEW_PYTHON_TYPES[c.type], ...) for c in columns}
    return create_model(f"PickObs_{view}", __base__=Frozen, **fields)


VIEW_ROW_MODELS = MappingProxyType({view: _row_model(view, columns) for view, columns in VIEW_COLUMNS.items()})
