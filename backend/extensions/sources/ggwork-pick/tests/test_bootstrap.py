import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_private_migrations_persist_and_are_repeatable(tmp_path):
    from ggwork_pick.service import PickService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
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
        }
        assert (await conn.execute(text("select version_num from ggwp_alembic_version"))).scalar_one() == "0002"
    await engine.dispose()
    second = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    await service.initialize(async_sessionmaker(second, expire_on_commit=False))
    await second.dispose()


@pytest.mark.asyncio
async def test_unknown_migration_fails_instead_of_restamping(tmp_path):
    from ggwork_pick.service import PickService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
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
    assert len(registry.routes) == len(registry.lifecycles) == 1
    assert registry.contributors == []


@pytest.mark.asyncio
async def test_memory_backend_is_rejected(tmp_path):
    from types import SimpleNamespace

    from ggwork_pick.service import PickService

    service = PickService(tmp_path)
    with pytest.raises(RuntimeError, match="memory"):
        await service.start(SimpleNamespace(session_factory=None))
