"""Small typed facts from exact common-query rows; never enrich immutable candidate JSON."""

from typing import Annotated

from pydantic import Field, StrictInt

from ggwork_pick.completion_contracts import QueryPin
from ggwork_pick.contracts import PickConditions, StrictInput
from ggwork_pick.query_reader import QueryFailure


class EpisodeFact(StrictInput):
    """Nullable source count with its exact retained mirror/table/key provenance."""

    episodes: Annotated[StrictInt, Field(ge=0)] | None
    source_ref: Annotated[str, Field(min_length=1, max_length=1024)]


class HistoricalItemFacts(EpisodeFact):
    """Model-only supplement bound to one authorized historical candidate item."""

    pin: QueryPin
    identity: Annotated[str, Field(min_length=1, max_length=512)]
    reference: Annotated[str, Field(min_length=1, max_length=1024)]


class HistoricalResultSummary(StrictInput):
    """Stored pre-limit total and conditions, explicitly scoped to the historical result."""

    result_id: Annotated[str, Field(min_length=1, max_length=64)]
    matched_total: Annotated[StrictInt, Field(ge=0)] | None
    conditions: PickConditions
    reference: Annotated[str, Field(min_length=1, max_length=1024)]


def episode_fact(payload: dict, row: dict) -> EpisodeFact | None:
    """Read only the uniquely keyed, known-language catalog source at this response's pin."""
    from ggwork_pick.query_evidence import source_key

    version = payload.get("pin", {}).get("mirror_version")
    if type(version) is not int or version < 1:
        return None
    key = source_key(row)
    board = payload.get("board") or {}
    matches = [(table, record) for table in ("catalog_rows", "rs_rows") for record in board.get(table, []) if record["row_key"] == key]
    if len(matches) != 1:
        return None
    table, record = matches[0]
    if not record["lang"].strip():
        return None
    value = record.get("episodes")
    if value is not None and (type(value) is not int or value < 0):
        raise QueryFailure("source_unavailable", "来源集数字段无效，无法核对")
    return EpisodeFact(episodes=value, source_ref=f"mirror:{version}:{table}:{key}")
