"""The observation radar's pick_obs views (plan TR-33, design 3.4 and 3.6): names, columns, types and row models.

Part of the shared data contract; see contract.py for the rules the four modules follow and the document they match. TR-11
creates the views from these columns; TR-24 reads them. A row model checks a whole row: the stored row models for the views
that expose one (states, links, alerts), FrozenInputs and SetSummary inside sets, and each text column naming an enum.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, create_model, model_validator

from ggwork_pick.observe.contract import ENUMS, Frozen, FrozenInputs, ViewType, first_problem, refuse
from ggwork_pick.observe.contract_rows import AlertRow, LinkRow, SetSummary, StateRow

# ---- the pick_obs views (design 3.4, 3.6) --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ViewColumn:
    """One view column; enum names the ENUMS entry its values come from (a json column with one holds a list of them)."""

    name: str
    type: ViewType
    nullable: bool
    enum: str | None = None


def _column(definition: str) -> ViewColumn:
    name, _, rest = definition.partition(":")
    kind, _, enum = rest.partition("=")
    return ViewColumn(name, kind.rstrip("?"), kind.endswith("?"), enum or None)


def _columns(*definitions: str) -> tuple[ViewColumn, ...]:
    """Columns from definitions written name:type, name:type? for a nullable column, and =ENUM after either for an enum."""
    return tuple(_column(definition) for definition in definitions)


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
            "channel:text=CHANNELS",
            "mode:text=MODES",
            "status:text=SET_STATUSES",
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
            "mode:text=MODES",
            "geo:text",
            "seed:text",
            "property:text=DISCOVERY_PROPERTIES",
            "term:text",
            "normalized_term:text",
            "language:text?",
            "match_status:text=DISCOVERY_MATCHES",
            "route:text=DISCOVERY_ROUTES",
            "matched_identity:text?",
            "breakout:bool",
            "first_seen_at:text",
        ),
        "alias_queue": _columns(
            "alias_id:int",
            "alias_version:int",
            "old_identity:text",
            "new_identity:text",
            "status:text=ALIAS_STATUSES",
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
            "state:text=TREND_STATES",
            "confirmation:text?=CONFIRMATIONS",
            "id_evidence:text=ID_EVIDENCE_LEVELS",
            "set_id:text",
            "mode:text=MODES",
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
            "channel:text=CHANNELS",
            "batch_id:text",
            "mode:text=MODES",
            "target_date:text?",
            "round_id:text?",
            "started_at:text",
            "finished_at:text?",
            "outcome:text=RUN_OUTCOMES",
            "requests:int",
            "published_set_id:text?",
            "status_codes:json=STATUS_CODES",
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
# The json columns whose inner shape is a contract model.
_JSON_SHAPES = MappingProxyType({("sets", "frozen_inputs"): FrozenInputs, ("sets", "summary"): SetSummary})


def _column_type(view: str, column: ViewColumn):
    if column.enum is not None:
        values = Literal[ENUMS[column.enum]]
        kind = list[values] if column.type == "json" else values
    else:
        kind = _JSON_SHAPES.get((view, column.name), _VIEW_PYTHON_TYPES[column.type])
    return kind | None if column.nullable else kind


def _row_model(view: str, columns: tuple[ViewColumn, ...]) -> type[Frozen]:
    fields = {column.name: (_column_type(view, column), ...) for column in columns}
    return create_model(f"PickObs_{view}", __base__=Frozen, **fields)


_COPIED_FROM_FROZEN = ("source_catalog_batch_id", "collector_version", "link_rules_version", "alias_version", "decisions_version")


def _sets_row_problem(row) -> str | None:
    """A sets row repeats what its frozen inputs and summary say; getattr because a mismatched channel lacks the other's keys."""
    frozen, summary, trends = row.frozen_inputs, row.summary, row.channel == "trends"
    own = (row.target_date, row.window_end, row.round_id)
    frozen_own = (getattr(frozen, "target_date", None), getattr(frozen, "window_end", None), None)
    rules = {rule.version for rule in frozen.rules if rule.version.startswith("trend-rules-" if trends else "gsc-rules-")}
    return first_problem(
        (
            (row.channel == frozen.channel == summary.channel, "channel、frozen_inputs 与 summary 的通道一致"),
            (own == (frozen_own if trends else (None, None, getattr(frozen, "round_id", None))), "target_date、window_end、round_id 取自冻结输入"),
            (all(getattr(row, name) == getattr(frozen, name) for name in _COPIED_FROM_FROZEN), "批次、采集版本、link-rules、别名与决定版本取自冻结输入"),
            (row.rules_version in rules, "rules_version 是冻结输入里本通道的判定规则"),
            (trends or row.as_of == getattr(summary, "cutoff", None), "GSC 集合的 as_of 是 summary 的 cutoff"),
        )
    )


class _SetsViewRow(_row_model("sets", VIEW_COLUMNS["sets"])):
    @model_validator(mode="after")
    def _consistent(self):
        refuse("obs_sets_view", _sets_row_problem(self))
        return self


# Views exposing a stored row validate it with that row's model; the rest are built from their columns.
_STORED_ROW_MODELS = MappingProxyType({"sets": _SetsViewRow, "states": StateRow, "links": LinkRow, "alerts": AlertRow})
VIEW_ROW_MODELS = MappingProxyType({view: _STORED_ROW_MODELS.get(view) or _row_model(view, columns) for view, columns in VIEW_COLUMNS.items()})
