"""ggwp migrations and the per-test database fixture on PostgreSQL. Skipped when PICK_TEST_PG_URL is unset."""

import os

import pg
import pytest
import revisions
from engines import host_engine
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

TABLES = {
    "ggwp_alembic_version",
    "ggwp_import_batches",
    "ggwp_drama_versions",
    "ggwp_knowledge_versions",
    "ggwp_candidate_sets",
    "ggwp_selections",
    "ggwp_selection_commands",
    "ggwp_sync_runs",
    "ggwp_answer_checks",
    # Migration 0007: the observation radar (design 3.5's seventeen, then D12, D13, D26 and D27).
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


def _batch(status: str) -> str:
    return (
        "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, created_at, validation_json)"
        f" values ('b-{status}', 'alice', 'catalog', 'h-{status}', '/x', '{status}', '2026-09-23', '{{}}')"
    )


@pytest.mark.asyncio
async def test_migrations_reach_head_in_an_empty_schema_and_a_restart_is_a_no_op(empty_pg_url, tmp_path):
    from ggwork_pick.service import PickService

    # The second pass is a gateway restart: a new engine re-runs the upgrade against a database already at head.
    for _ in range(2):
        engine = host_engine(empty_pg_url)
        try:
            await PickService(tmp_path / "files").initialize(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    engine = host_engine(empty_pg_url)
    try:
        async with engine.connect() as conn:
            tables = (await conn.execute(text("select tablename from pg_tables where tablename like 'ggwp%' and schemaname = :s"), {"s": pg.SCHEMA})).scalars()
            assert set(tables) == TABLES
            elsewhere = await conn.execute(text("select count(*) from pg_tables where tablename like 'ggwp%' and schemaname <> :s"), {"s": pg.SCHEMA})
            assert elsewhere.scalar_one() == 0
            assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalars().all() == [revisions.head()]
            constraint = await conn.execute(
                text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ggwp_batch_status' and conrelid = 'ggwp_import_batches'::regclass")
            )
            assert "pruned" in constraint.scalar_one()
        async with engine.begin() as conn:
            for status in ("importing", "published", "failed", "pruned"):
                await conn.execute(text(_batch(status)))
        with pytest.raises(IntegrityError, match="ggwp_batch_status"):
            async with engine.begin() as conn:
                await conn.execute(text(_batch("bogus")))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_each_test_gets_its_own_migrated_database_and_reader_role(pg_db_url, pg_template, pg_reader_role):
    engine = host_engine(pg_db_url)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("select current_database()"))).scalar_one() != pg_template
            assert (await conn.execute(text("show search_path"))).scalar_one() == pg.SCHEMA
            assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalar_one() == revisions.head()
            assert (await conn.execute(text("select count(*) from ggwp_import_batches"))).scalar_one() == 0
            role = await conn.execute(text("select count(*) from pg_roles where rolname = :r"), {"r": pg_reader_role})
            assert role.scalar_one() == 1
    finally:
        await engine.dispose()
    # Roles are cluster-wide: a per-test name keeps one test's grants from leaking into the next.
    assert os.environ[pg.READER_ROLE_ENV] == pg_reader_role
    assert pg_reader_role.startswith(pg.DEFAULT_READER_ROLE + "_")
