"""Migration 0007's tables as its tests read and write them: shared by test_0007.py and test_0007_pick_obs.py."""

import revisions
import sqlalchemy as sa
from sqlalchemy import inspect, text

from ggwork_pick.observe.contract_views import VIEW_NAMES

# Design 3.5's seventeen, then D12's decisions, D13's links, D26's V checks and D27's totals.
OBS_TABLES = {
    "ggwp_obs_watch",
    "ggwp_obs_runtime",
    "ggwp_obs_budget",
    "ggwp_obs_batches",
    "ggwp_obs_requests",
    "ggwp_obs_raw",
    "ggwp_obs_discoveries",
    "ggwp_obs_identity_alias",
    "ggwp_obs_legacy",
    "ggwp_gsc_slices",
    "ggwp_gsc_hourly",
    "ggwp_gsc_daily",
    "ggwp_gsc_query_daily",
    "ggwp_obs_sets",
    "ggwp_obs_states",
    "ggwp_obs_milestones",
    "ggwp_obs_alerts",
    "ggwp_obs_decisions",
    "ggwp_obs_links",
    "ggwp_gsc_vchecks",
    "ggwp_gsc_totals",
}
CANDIDATE_COLUMNS = ("trends_set_id", "gsc_set_id", "obs_as_of_json")
CANDIDATE_INDEXES = {"ggwp_candidate_sets_trends_set", "ggwp_candidate_sets_gsc_set"}
STAMP = "2026-09-25T01:52:10.000000+00:00"


def obs_table(name: str) -> sa.Table:
    from ggwork_pick.models import metadata

    return metadata.tables[name]


def is_serial(column: sa.Column) -> bool:
    return column.primary_key and len(column.table.primary_key.columns) == 1 and isinstance(column.type, sa.Integer)


# Columns whose CHECK takes only the contract's values get one of them.
CHECKED = {"channel": "gsc", "mode": "shadow", "status": "published"}


def _dummy(column: sa.Column):
    if column.name in CHECKED:
        return CHECKED[column.name]
    kind = column.type
    if isinstance(kind, sa.JSON):
        return {}
    if isinstance(kind, sa.Boolean):
        return False
    if isinstance(kind, sa.Integer):
        return 1
    if isinstance(kind, sa.Float):
        return 0.5
    return "x"


def filler(name: str, **values) -> tuple[sa.Table, dict]:
    """A row of the named table: the given values, and a dummy for every other NOT NULL column without a default."""
    table = obs_table(name)
    row = {
        column.name: _dummy(column)
        for column in table.columns
        if column.name not in values and not column.nullable and column.server_default is None and not is_serial(column)
    }
    if name == "ggwp_obs_states" and values.get("channel", row.get("channel")) == "trends":
        row.setdefault("normalized_title", "x")
    return table, row | values


async def insert(engine, name: str, **values) -> None:
    table, row = filler(name, **values)
    async with engine.begin() as conn:
        await conn.execute(table.insert(), row)


async def scalar(engine, sql: str, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def fetch_rows(engine, sql: str, **params) -> list[tuple]:
    async with engine.connect() as conn:
        return [tuple(row) for row in (await conn.execute(text(sql), params)).all()]


async def _inspect(engine, read):
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync: read(inspect(sync)))


async def table_names(engine) -> set[str]:
    return set(await _inspect(engine, lambda found: found.get_table_names()))


async def columns_of(engine, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in await _inspect(engine, lambda found: found.get_columns(table))}


async def index_names(engine) -> set[str]:
    """Index names from the catalog: SQLAlchemy skips SQLite's expression indexes when it reflects."""
    if engine.dialect.name == "sqlite":
        return set(await first_column(engine, "select name from sqlite_master where type = 'index' and name not like 'sqlite_%'"))
    return set(await first_column(engine, "select indexname from pg_indexes where schemaname = current_schema()"))


async def first_column(engine, sql: str) -> list:
    return [row[0] for row in await fetch_rows(engine, sql)]


async def view_names(engine) -> set[str]:
    return set(await first_column(engine, "select viewname from pg_views where schemaname = 'pick_obs'"))


async def set_version(engine, version: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("update ggwp_alembic_version set version_num = :v"), {"v": version})


async def runtime_rows(engine) -> list[tuple]:
    return await fetch_rows(engine, "select channel, lease_generation from ggwp_obs_runtime order by channel")


def migration_module():
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(revisions.config()).get_revision("0007").module


async def assert_0007_in_place(engine) -> None:
    assert OBS_TABLES <= await table_names(engine)
    candidates = await columns_of(engine, "ggwp_candidate_sets")
    assert all(candidates[name]["nullable"] for name in CANDIDATE_COLUMNS)
    assert CANDIDATE_INDEXES <= await index_names(engine)
    assert await runtime_rows(engine) == [("gsc", 0), ("trends", 0)]
    assert await scalar(engine, "select version_num from ggwp_alembic_version") == revisions.head() == "0007"
    if engine.dialect.name == "postgresql":
        assert await view_names(engine) == set(VIEW_NAMES)
