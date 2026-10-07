"""Read-only preview of the existing catalog selection, never an observation plan."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy import text

from ggwork_pick.observe.contract import Frozen, Identity, Stamp
from ggwork_pick.observe.instants import instant, stamp
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends.top_dramas import TARGET_DRAMAS, select_candidates
from ggwork_pick.observe.trends_table import Short, TableBasis, TableSources
from ggwork_pick.repository import SHARED_OWNER, PickRepository


class CandidateRow(Frozen):
    order: Annotated[int, Field(ge=1, le=TARGET_DRAMAS)]
    identity: Identity
    title: Annotated[str, Field(max_length=500)]
    platform: Short
    language: Short
    term: Annotated[str, Field(min_length=1, max_length=200)]
    geo: Literal["WW"] = "WW"
    time_range: Literal["today 1-m"] = "today 1-m"
    basis: list[TableBasis]


class TrendsCandidates(Frozen):
    checked_at: Stamp
    kind: Literal["candidate_preview"] = "candidate_preview"
    selection_state: Literal["ready", "empty"]
    catalog_batch_id: Annotated[str, Field(max_length=200)] | None
    target: Literal[100] = TARGET_DRAMAS
    selected: Annotated[int, Field(ge=0, le=TARGET_DRAMAS)]
    unusable_titles: Annotated[int, Field(ge=0)]
    sources: TableSources
    rows: Annotated[list[CandidateRow], Field(max_length=TARGET_DRAMAS)]


async def trends_candidates(repo: PickRepository, *, now: datetime) -> dict:
    if repo.owner_id != SHARED_OWNER:
        raise ValueError("趋势候选只读共享目录")
    async with repo.session_factory() as session:
        dialect = session.get_bind().dialect.name
        options = {"isolation_level": "REPEATABLE READ"} if dialect == "postgresql" else {}
        conn = await session.connection(execution_options=options)
        if dialect == "postgresql":
            await session.execute(text("SET LOCAL transaction_read_only = on"))
        else:
            # SQLite's legacy driver does not begin a snapshot for SELECT by itself.
            await session.execute(text("BEGIN"))
        step = ReadStep(conn)
        try:
            selected = await select_candidates(step)
        finally:
            step._close()
    rows = [CandidateRow(**pick.to_note(order), term=pick.term) for order, pick in enumerate(selected.picks, start=1)]
    return TrendsCandidates(
        checked_at=stamp(instant(now)),
        selection_state="ready" if rows else "empty",
        catalog_batch_id=selected.catalog_batch_id,
        selected=len(rows),
        unusable_titles=selected.unusable_titles,
        sources=TableSources(
            boards=[board.note() for board in selected.boards],
            revenue=selected.revenue.note(selected.filled),
        ),
        rows=rows,
    ).model_dump(mode="json")
