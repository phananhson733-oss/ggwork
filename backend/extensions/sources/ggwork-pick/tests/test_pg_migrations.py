"""ggwp migrations and the per-test database fixture on PostgreSQL. Skipped when PICK_TEST_PG_URL is unset."""

import os
from pathlib import Path

import pg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
}


def _head() -> str:
    import ggwork_pick

    config = Config()
    config.set_main_option("script_location", str(Path(ggwork_pick.__file__).parent / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def _batch(status: str) -> str:
    return (
        "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, created_at, validation_json)"
        f" values ('b-{status}', 'alice', 'catalog', 'h-{status}', '/x', '{status}', '2026-09-23', '{{}}')"
    )


@pytest.fixture
def empty_pg_url(pg_cluster):
    name = pg.unique_name("m")
    pg_cluster.create_database(name)
    yield pg_cluster.async_url(name)
    pg_cluster.drop_database(name)


@pytest.mark.asyncio
async def test_migrations_reach_head_in_an_empty_schema_and_a_restart_is_a_no_op(empty_pg_url, tmp_path):
    from ggwork_pick.service import PickService

    # The second pass is a gateway restart: a new engine re-runs the upgrade against a database already at head.
    for _ in range(2):
        engine = create_async_engine(empty_pg_url)
        try:
            await PickService(tmp_path / "files").initialize(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    engine = create_async_engine(empty_pg_url)
    try:
        async with engine.connect() as conn:
            tables = (await conn.execute(text("select tablename from pg_tables where tablename like 'ggwp%' and schemaname = :s"), {"s": pg.SCHEMA})).scalars()
            assert set(tables) == TABLES
            elsewhere = await conn.execute(text("select count(*) from pg_tables where tablename like 'ggwp%' and schemaname <> :s"), {"s": pg.SCHEMA})
            assert elsewhere.scalar_one() == 0
            assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalars().all() == [_head()]
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
    engine = create_async_engine(pg_db_url)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("select current_database()"))).scalar_one() != pg_template
            assert (await conn.execute(text("show search_path"))).scalar_one() == pg.SCHEMA
            assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalar_one() == _head()
            assert (await conn.execute(text("select count(*) from ggwp_import_batches"))).scalar_one() == 0
            role = await conn.execute(text("select count(*) from pg_roles where rolname = :r"), {"r": pg_reader_role})
            assert role.scalar_one() == 1
    finally:
        await engine.dispose()
    # Roles are cluster-wide: a per-test name keeps one test's grants from leaking into the next.
    assert os.environ[pg.READER_ROLE_ENV] == pg_reader_role
    assert pg_reader_role.startswith(pg.DEFAULT_READER_ROLE + "_")
