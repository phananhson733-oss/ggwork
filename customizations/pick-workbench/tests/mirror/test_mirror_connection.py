"""The mirror's dedicated asyncpg connection, outside the ORM pool (plan 3.1, 5.2 step 1; U33).

The PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

from urllib.parse import unquote, urlsplit

import pg
import pytest
from engines import host_engine
from sqlalchemy import URL, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

SECRET = "pw-s3cret-9f2c"


def test_dsn_from_url_drops_the_driver_and_keeps_the_password():
    from ggwork_pick.mirror.connection import dsn_from_url

    expected = f"postgresql://app:{SECRET}@db.example:6543/postgres"
    assert dsn_from_url(f"postgresql+asyncpg://app:{SECRET}@db.example:6543/postgres") == expected
    assert dsn_from_url(make_url(f"postgresql+asyncpg://app:{SECRET}@db.example:6543/postgres")) == expected
    # PICK_DATABASE_URL may use the short scheme the gateway entrypoint also accepts.
    assert dsn_from_url(f"postgres://app:{SECRET}@db.example:6543/postgres") == expected


def test_dsn_from_url_escapes_a_password_the_way_asyncpg_reads_it_back():
    from ggwork_pick.mirror.connection import dsn_from_url

    password = "p@ss/w:rd?#%+ x"
    dsn = dsn_from_url(URL.create("postgresql+asyncpg", username="app", password=password, host="db.example", database="postgres"))
    # asyncpg splits the DSN with urllib and unquotes the password.
    assert unquote(urlsplit(dsn).password) == password
    assert urlsplit(dsn).hostname == "db.example"


@pytest.mark.parametrize("url", [f"sqlite+aiosqlite:////tmp/{SECRET}.db", f"mysql://app:{SECRET}@db.example/pick"])
def test_dsn_from_url_refuses_anything_but_postgresql(url):
    from ggwork_pick.mirror.connection import dsn_from_url

    with pytest.raises(ValueError, match="PostgreSQL") as failure:
        dsn_from_url(url)
    assert SECRET not in str(failure.value)


@pytest.mark.parametrize("url", [f"not a url {SECRET}", f"postgresql://app:{SECRET}@db.example:port{SECRET}/postgres"])
def test_an_unparseable_url_is_refused_without_echoing_it(url):
    from ggwork_pick.mirror.connection import dsn_from_url

    with pytest.raises(ValueError) as failure:
        dsn_from_url(url)
    assert SECRET not in str(failure.value) and SECRET not in repr(failure.value)
    # A traceback shows neither the parser's message nor the URL it quotes.
    assert failure.value.__cause__ is None and failure.value.__suppress_context__


@pytest.mark.asyncio
async def test_dsn_from_engine_reads_the_host_engine_url():
    from ggwork_pick.mirror.connection import dsn_from_engine

    engine = create_async_engine(f"postgresql+asyncpg://app:{SECRET}@db.example/postgres")
    try:
        assert dsn_from_engine(engine) == f"postgresql://app:{SECRET}@db.example/postgres"
    finally:
        await engine.dispose()


def test_dsn_from_env_needs_pick_database_url(monkeypatch):
    from ggwork_pick.mirror.connection import DATABASE_URL_ENV, dsn_from_env

    assert DATABASE_URL_ENV == "PICK_DATABASE_URL"
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(ValueError, match=DATABASE_URL_ENV):
        dsn_from_env()
    for blank in ("", "  "):
        monkeypatch.setenv(DATABASE_URL_ENV, blank)
        with pytest.raises(ValueError, match=DATABASE_URL_ENV):
            dsn_from_env()
    monkeypatch.setenv(DATABASE_URL_ENV, f"postgresql://app:{SECRET}@db.example/postgres\n")
    assert dsn_from_env() == f"postgresql://app:{SECRET}@db.example/postgres"
    monkeypatch.setenv(DATABASE_URL_ENV, f"sqlite:///{SECRET}.db")
    with pytest.raises(ValueError, match=DATABASE_URL_ENV) as failure:
        dsn_from_env()
    assert SECRET not in str(failure.value)
    # An explicit mapping, for the admin commands' tests.
    assert dsn_from_env({DATABASE_URL_ENV: f"postgres://app:{SECRET}@h/db"}) == f"postgresql://app:{SECRET}@h/db"


@pytest.mark.asyncio
async def test_open_dedicated_sets_no_statement_timeout_and_leaves_tls_to_pgsslmode(monkeypatch):
    import asyncpg

    from ggwork_pick.mirror.connection import open_dedicated

    calls = []

    async def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return "connection"

    monkeypatch.setattr(asyncpg, "connect", connect)
    dsn = f"postgresql://app:{SECRET}@db.example/postgres"
    assert await open_dedicated(dsn) == "connection"
    # COPY, index builds and the gate scans outlast the ORM pool's 30 seconds: every statement's timeout is its caller's.
    # No ssl argument: asyncpg reads PGSSLMODE the way libpq does.
    settings = {"search_path": "deerflow", "application_name": "ggwp-mirror"}
    assert calls == [((dsn,), {"server_settings": settings, "command_timeout": None})]


@pytest.mark.asyncio
async def test_the_dedicated_connection_brings_its_own_search_path_and_name_and_stays_out_of_the_pool(pg_db_url):
    from ggwork_pick.mirror.connection import APPLICATION_NAME, dsn_from_url, open_dedicated

    engine = host_engine(pg_db_url)
    try:
        # The test databases get search_path from ALTER DATABASE; take it away so only the connection's own settings can supply it.
        async with engine.begin() as conn:
            database = (await conn.execute(text("select current_database()"))).scalar_one()
            await conn.execute(text(f'alter database "{database}" set search_path to public'))
        async with engine.connect() as orm:
            orm_pid = (await orm.execute(text("select pg_backend_pid()"))).scalar_one()
            checked_out = engine.sync_engine.pool.checkedout()
            dedicated = await open_dedicated(dsn_from_url(pg_db_url))
            try:
                assert await dedicated.fetchval("show search_path") == pg.SCHEMA
                assert await dedicated.fetchval("show application_name") == APPLICATION_NAME == "ggwp-mirror"
                # search_path deerflow: the ggwp tables resolve unqualified, as they do for the ORM.
                assert await dedicated.fetchval("select count(*) from ggwp_sync_runs") == 0
                assert await dedicated.fetchval("select pg_backend_pid()") != orm_pid
                assert engine.sync_engine.pool.checkedout() == checked_out
                named = await orm.execute(
                    text("select count(*) from pg_stat_activity where datname = current_database() and application_name = :a"), {"a": APPLICATION_NAME}
                )
                assert named.scalar_one() == 1
            finally:
                await dedicated.close()
        assert dedicated.is_closed()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_failed_connection_names_the_error_class_never_the_password(pg_cluster):
    from ggwork_pick.mirror.connection import MirrorConnectionError, open_dedicated

    # A cluster that checks passwords (CI) needs the real one to reach the database check; either way it must not show.
    password = pg_cluster.url.password or SECRET
    base = pg_cluster.url.set(drivername="postgresql", password=password)
    missing_database = base.set(database=pg.unique_name("absent")).render_as_string(hide_password=False)
    closed_port = base.set(host="127.0.0.1", port=1).render_as_string(hide_password=False)
    for dsn, expected in ((missing_database, "InvalidCatalogNameError"), (closed_port, "Error")):
        with pytest.raises(MirrorConnectionError) as failure:
            await open_dedicated(dsn)
        message = str(failure.value)
        assert expected in message and type(failure.value.__context__).__name__ in message
        assert password not in message and password not in repr(failure.value)
        assert failure.value.__cause__ is None and failure.value.__suppress_context__
    # The server's own code rides along: it tells operators what went wrong without any value.
    with pytest.raises(MirrorConnectionError, match="3D000"):
        await open_dedicated(missing_database)
