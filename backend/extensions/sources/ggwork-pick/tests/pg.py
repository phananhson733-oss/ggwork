"""PostgreSQL helpers for the tests: one migrated template per session, one database per test.

A database per test, not a random schema: later migrations create database-wide names (pick_mirror,
pickm_v*) and advisory locks are keyed per database, so tests sharing one database would leave state for
each other even when run one after another. Roles are cluster-wide, so the reader role is per test too.

PICK_TEST_PG_URL names a throwaway cluster (libpq URL, superuser, no query parameters); the tests create
and drop databases and roles on it. Drivers are imported lazily so a SQLite-only install still collects.
"""

from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

URL_ENV = "PICK_TEST_PG_URL"
READER_ROLE_ENV = "PICK_MIRROR_READER_ROLE"
DEFAULT_READER_ROLE = "pick_board_reader"
# Production keeps the host and ggwp tables in this schema through search_path; the tests do the same.
SCHEMA = "deerflow"


def unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class PgCluster:
    """Administrative statements against the cluster named by PICK_TEST_PG_URL."""

    def __init__(self, url: str):
        self.url = make_url(url)

    def async_url(self, database: str) -> str:
        return self.url.set(drivername="postgresql+asyncpg", database=database).render_as_string(hide_password=False)

    def _execute(self, *statements, database: str | None = None) -> None:
        import psycopg

        url = self.url.set(drivername="postgresql", database=database or self.url.database)
        with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as conn:
            for statement in statements:
                conn.execute(statement)

    def create_database(self, name: str, *, template: str | None = None) -> None:
        """An empty database has an empty SCHEMA; a copy of a template has whatever the template had."""
        from psycopg import sql

        create = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
        if template is not None:
            create = sql.SQL("{} TEMPLATE {}").format(create, sql.Identifier(template))
        self._execute(create)
        if template is None:
            self._execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)), database=name)
        # Per-database settings are not copied from a template.
        self._execute(sql.SQL("ALTER DATABASE {} SET search_path TO {}").format(sql.Identifier(name), sql.Identifier(SCHEMA)))

    def drop_database(self, name: str) -> None:
        from psycopg import sql

        # FORCE: a failed test may still hold pooled connections.
        self._execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def create_role(self, name: str) -> None:
        from psycopg import sql

        self._execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(name)))

    def drop_role(self, name: str) -> None:
        from psycopg import sql

        self._execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


async def migrate(url: str, data_dir: Path) -> None:
    """Bring a database to the ggwp head the way the gateway does at startup."""
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.service import PickService

    engine = host_engine(url)
    try:
        await PickService(data_dir).initialize(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
