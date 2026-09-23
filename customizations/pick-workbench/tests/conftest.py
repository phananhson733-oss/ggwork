import os
import sys
from pathlib import Path

import pg
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Exercise the pinned host API source; production installs the workspace package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend/packages/extension-api"))


@pytest.fixture(scope="session")
def pg_cluster():
    url = os.environ.get(pg.URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{pg.URL_ENV} is not set")
    return pg.PgCluster(url)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_template(pg_cluster, tmp_path_factory):
    """Migrated to head once per session; every PostgreSQL test starts from a copy."""
    name = pg.unique_name("pick_tpl")
    pg_cluster.create_database(name)
    try:
        await pg.migrate(pg_cluster.async_url(name), tmp_path_factory.mktemp("pick-tpl"))
        yield name
    finally:
        pg_cluster.drop_database(name)


@pytest.fixture
def pg_reader_role(pg_cluster, monkeypatch):
    name = pg.unique_name(pg.DEFAULT_READER_ROLE)
    pg_cluster.create_role(name)
    monkeypatch.setenv(pg.READER_ROLE_ENV, name)
    yield name
    pg_cluster.drop_role(name)


@pytest.fixture
def pg_db_url(pg_cluster, pg_template, pg_reader_role):
    # Set up after the role, so torn down before it: the database goes first, with any grants it gave the role.
    name = pg.unique_name("t")
    pg_cluster.create_database(name, template=pg_template)
    yield pg_cluster.async_url(name)
    pg_cluster.drop_database(name)


@pytest.fixture(params=["sqlite", "postgres"])
def pick_db_url(request, tmp_path):
    """The same test on both dialects; the PostgreSQL half skips when PICK_TEST_PG_URL is unset."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    return request.getfixturevalue("pg_db_url")
