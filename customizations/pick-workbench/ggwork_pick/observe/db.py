"""The collectors' database connection: one at a time, a transaction per step (plan D1, D2; design 3.3, 3.4).

A cron service holds at most one connection, and only while a step runs: NullPool closes it when the step's transaction
ends, and an asyncio.Lock makes a second step wait for the first, so a four-hour breaker pause holds no connection and a
channel never counts for more than one in the connection budget (plan:922, D4). PostgreSQL goes through asyncpg (D1);
SQLite (tests, local runs) through aiosqlite, where a write transaction starts with BEGIN IMMEDIATE and so waits for any
other writer the way FOR UPDATE on the runtime row does on PostgreSQL (design 3.3). A read transaction is read only on
both (PostgreSQL's transaction_read_only; SQLite's query_only, which dies with the connection).

JSON goes through the host's own serializer object (deerflow.persistence.engine, D1), so a collector stores what the
gateway would. Settings are transaction scoped only, set_config(..., true): a session-level SET would outlive the step
on a pooled server connection (plan 6.7). Each step bounds its lock waits, its statements and any idle time inside it,
so a stuck process cannot hold the runtime row for long. application_name, set when connecting, names the channel in
pg_stat_activity (ggwp-obs-trends, ggwp-obs-gsc).

This is the observe package's low-level write interface. The collectors never import it (test_write_paths): they reach
the database through lease.LeasedWriter. The URL is never printed; errors about it name the variable only.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType

from deerflow.persistence.engine import _json_serializer as HOST_JSON_SERIALIZER
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from ggwork_pick.observe.errors import Refused

DATABASE_URL_VARIABLE = "PICK_DATABASE_URL"
APPLICATION_NAMES = MappingProxyType({"trends": "ggwp-obs-trends", "gsc": "ggwp-obs-gsc"})
ADMIN_APPLICATION_NAME = "ggwp-obs-admin"
COMMAND_TIMEOUT_SECONDS = 30  # the host's (deerflow.persistence.engine)
# A step is milliseconds of SQL: waiting longer than this means another process is stuck, not busy.
STEP_SETTINGS = (("lock_timeout", "15s"), ("statement_timeout", "30s"), ("idle_in_transaction_session_timeout", "60s"))
READ_ONLY_SETTINGS = (("transaction_read_only", "on"),)
POSTGRES_DRIVER = "postgresql+asyncpg"
SQLITE_DRIVER = "sqlite+aiosqlite"
_POSTGRES_NAMES = frozenset({"postgres", "postgresql", POSTGRES_DRIVER})
_SQLITE_NAMES = frozenset({"sqlite", SQLITE_DRIVER})
_READ_ONLY = "ggwp_obs_read_only"  # an execution option: which BEGIN the SQLite hook sends


def normalize_url(raw: str | URL) -> URL:
    """The URL with its async driver; Refused (exit 2) for anything that is not PostgreSQL or SQLite. The URL is never
    quoted back: a parse error's own message would carry it, password and all."""
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError, TypeError):
        raise Refused(f"{DATABASE_URL_VARIABLE} 不是合法的数据库 URL（为免泄露，不回显）") from None
    if url.drivername in _POSTGRES_NAMES:
        return url.set(drivername=POSTGRES_DRIVER)
    if url.drivername in _SQLITE_NAMES:
        return url.set(drivername=SQLITE_DRIVER)
    raise Refused(f"{DATABASE_URL_VARIABLE} 须是 PostgreSQL 或 SQLite 的 URL")


def database_url(environ: Mapping[str, str] | None = None) -> URL:
    """PICK_DATABASE_URL: the observer's Supavisor session DSN in production (plan section 10, S5), TLS from PGSSLMODE."""
    env = os.environ if environ is None else environ
    raw = env.get(DATABASE_URL_VARIABLE, "").strip()
    if not raw:
        raise Refused(f"缺少 {DATABASE_URL_VARIABLE}")
    return normalize_url(raw)


def _sqlite_transactions(engine) -> None:
    """SQLAlchemy's recipe for pysqlite and aiosqlite: the driver sends no BEGIN of its own, the hook sends ours."""

    @event.listens_for(engine.sync_engine, "connect")
    def _driver_sends_no_begin(dbapi_connection, record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN" if conn.get_execution_options().get(_READ_ONLY) else "BEGIN IMMEDIATE")


def _set_config_sql(settings: tuple[tuple[str, str], ...]) -> str:
    return "SELECT " + ", ".join(f"set_config(:n{index}, :v{index}, true)" for index in range(len(settings)))


def _set_config_params(settings: tuple[tuple[str, str], ...]) -> dict[str, str]:
    names = {f"n{index}": name for index, (name, _) in enumerate(settings)}
    values = {f"v{index}": value for index, (_, value) in enumerate(settings)}
    return names | values


class ObsDatabase:
    """One connection at a time, opened for a transaction and closed after it. Its repr never shows the URL."""

    def __init__(self, url: str | URL, *, application_name: str):
        target = normalize_url(url)
        self._postgres = target.drivername == POSTGRES_DRIVER
        connect_args = {"server_settings": {"application_name": application_name}, "command_timeout": COMMAND_TIMEOUT_SECONDS} if self._postgres else {}
        self._engine = create_async_engine(target, poolclass=NullPool, json_serializer=HOST_JSON_SERIALIZER, connect_args=connect_args)
        if not self._postgres:
            _sqlite_transactions(self._engine)
        self._application_name = application_name
        self._lock = asyncio.Lock()
        self._holder: asyncio.Task | None = None

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    @property
    def application_name(self) -> str:
        return self._application_name

    def __repr__(self) -> str:
        return f"ObsDatabase(dialect={self.dialect!r}, application_name={self._application_name!r})"

    @asynccontextmanager
    async def transaction(self, *, read_only: bool = False) -> AsyncIterator[AsyncConnection]:
        """A transaction on a connection of its own, committed when the block ends (rolled back if it raises), after
        which the connection is closed. A step inside a step would wait for itself: it is refused instead."""
        task = asyncio.current_task()
        if task is not None and self._holder is task:
            raise RuntimeError("一步里又开一步：唯一的连接正被这一步占着（先结束这一步）")
        async with self._lock:
            self._holder = task
            try:
                async with self._begin(read_only) as conn:
                    yield conn
            finally:
                self._holder = None

    @asynccontextmanager
    async def _begin(self, read_only: bool) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            if read_only:
                conn = await conn.execution_options(**{_READ_ONLY: True})
            async with conn.begin():
                await self._scope(conn, read_only)
                yield conn

    async def _scope(self, conn: AsyncConnection, read_only: bool) -> None:
        if self._postgres:
            settings = STEP_SETTINGS + (READ_ONLY_SETTINGS if read_only else ())
            await conn.execute(text(_set_config_sql(settings)), _set_config_params(settings))
        elif read_only:
            await conn.exec_driver_sql("PRAGMA query_only = ON")  # the connection closes with the transaction

    async def dispose(self) -> None:
        await self._engine.dispose()


def open_database(channel: str, environ: Mapping[str, str] | None = None) -> ObsDatabase:
    """The channel's database from PICK_DATABASE_URL, named for pg_stat_activity."""
    try:
        name = APPLICATION_NAMES[channel]
    except KeyError:
        raise ValueError(f"没有 {channel} 这个通道；通道是 {', '.join(APPLICATION_NAMES)}") from None
    return ObsDatabase(database_url(environ), application_name=name)
