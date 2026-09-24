import pytest
import revisions
from engines import host_engine
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.asyncio
async def test_private_migrations_persist_and_are_repeatable(tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    await service.initialize(factory)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: inspect(c).get_table_names())
        assert set(names) == {
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
        assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalar_one() == revisions.head()
    await engine.dispose()
    second = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    await service.initialize(async_sessionmaker(second, expire_on_commit=False))
    await second.dispose()


@pytest.mark.asyncio
async def test_a_migration_retried_after_a_partial_ddl_still_builds_its_indexes(tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    # SQLite commits each DDL statement on its own: simulate a crash after CREATE TABLE, before CREATE INDEX.
    async with engine.begin() as conn:
        await conn.execute(text("drop index ggwp_answer_checks_thread"))
        await conn.execute(text("drop index ggwp_sync_runs_started"))
        await conn.execute(text("update ggwp_alembic_version set version_num='0002'"))
    await service.initialize(factory)
    async with engine.connect() as conn:
        indexes = await conn.run_sync(lambda c: {i["name"] for t in ("ggwp_answer_checks", "ggwp_sync_runs") for i in inspect(c).get_indexes(t)})
    assert {"ggwp_answer_checks_thread", "ggwp_sync_runs_started"} <= indexes
    await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_migration_fails_instead_of_restamping(tmp_path):
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    async with engine.begin() as conn:
        await conn.execute(text("update ggwp_alembic_version set version_num='future_revision'"))
    with pytest.raises(Exception, match="future_revision"):
        await service.initialize(factory)
    await engine.dispose()


def test_repository_requires_owner():
    from ggwork_pick.repository import PickRepository

    for owner in (None, "", " ", "default"):
        with pytest.raises(ValueError, match="owner"):
            PickRepository(None, owner)


def test_entrypoint_registers_a_service_against_the_pinned_api(tmp_path):
    from ggwork_pick import install

    class Registry:
        services = []
        routes = []
        lifecycles = []
        contributors = []

        def service(self, service):
            self.services.append(service)

        def routers(self, routers):
            self.routes.extend(routers)

        def task_lifecycle(self, lifecycle):
            self.lifecycles.append(lifecycle)

        def middlewares(self, contributor):
            self.contributors.append(contributor)

    registry = Registry()
    install(registry, {"data_dir": str(tmp_path)})
    assert install.__deerflow_api__ == "0.2.1"
    assert len(registry.services) == 1
    assert registry.services[0].data_dir == tmp_path
    assert len(registry.lifecycles) == 1
    assert [router.prefix for router in registry.routes] == ["/api/pick"]
    assert registry.contributors == []


@pytest.mark.asyncio
async def test_memory_backend_is_rejected(tmp_path):
    from types import SimpleNamespace

    from ggwork_pick.service import PickService

    service = PickService(tmp_path)
    with pytest.raises(RuntimeError, match="memory"):
        await service.start(SimpleNamespace(session_factory=None))


@pytest.mark.asyncio
async def test_upgrade_from_0002_keeps_batches_and_allows_pruned_status(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import ggwork_pick
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")

    def to_0002(connection):
        config = Config()
        config.set_main_option("script_location", str(Path(ggwork_pick.__file__).parent / "migrations"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")

    async with engine.begin() as conn:
        await conn.run_sync(to_0002)
        await conn.execute(
            text(
                "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, created_at, validation_json)"
                " values ('b1', 'alice', 'catalog', 'h1', '/x', 'published', '2026-09-21', '{}')"
            )
        )
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as conn:
        assert (await conn.execute(text("select owner_id from ggwp_import_batches where id='b1'"))).scalar_one() == "alice"
        await conn.execute(text("update ggwp_import_batches set status='pruned' where id='b1'"))
        with pytest.raises(Exception):
            await conn.execute(text("update ggwp_import_batches set status='bogus' where id='b1'"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_downgrade_below_0003_survives_pruned_batches(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import ggwork_pick
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    await PickService(tmp_path / "files").initialize(async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, created_at, validation_json)"
                " values ('b1', 'system:shared', 'catalog', 'pruned-b1', '/x', 'pruned', '2026-09-21', '{}')"
            )
        )

    def to_0002(connection):
        config = Config()
        config.set_main_option("script_location", str(Path(ggwork_pick.__file__).parent / "migrations"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")

    async with engine.begin() as conn:
        await conn.run_sync(to_0002)
        assert (await conn.execute(text("select status from ggwp_import_batches where id='b1'"))).scalar_one() == "failed"
    await engine.dispose()
