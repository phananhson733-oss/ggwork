"""The collectors' database connection: one at a time, a transaction per step (plan D1, D2; design 3.3, 3.4).

A cron service holds at most one connection, and only while a step runs: NullPool closes it when the step's transaction
ends, and an asyncio.Lock makes a second step wait for the first, so a four-hour breaker pause holds no connection and a
channel never counts for more than one in the connection budget (plan:922, D4). PostgreSQL goes through asyncpg (D1);
SQLite (tests, local runs) through aiosqlite, where a write transaction starts with BEGIN IMMEDIATE and so waits for any
other writer the way FOR UPDATE on the runtime row does on PostgreSQL (design 3.3). A read transaction is read only on
both (PostgreSQL's transaction_read_only; SQLite's query_only, which dies with the connection).

JSON goes through the host's own serializer object (deerflow.persistence.engine, D1), so a collector stores what the
gateway would. Settings are transaction scoped only, set_config(..., true): a session-level SET would outlive the step
on a pooled server connection (plan 6.7). Each step bounds its lock waits, its statements and any idle time inside it
(StepLimits: 15 s, 30 s, 60 s), so a stuck process cannot hold the runtime row for long; a heavy step (a large prune,
the link materialization) passes limits of its own, and every other step keeps the defaults. The driver's own limit
follows the step's: asyncpg's command_timeout is never below the statement_timeout (nor the host's 30 s), and SQLite's
busy timeout is the lock wait. NullPool opens a connection per transaction, so the do_connect hook sets it per step.
A wait that runs out (55P03, 57014, SQLITE_BUSY) means another process holds the rows, not that they cannot be read:
the transaction raises StepTimedOut for it (exit 1, the next trigger tries again), at start-up as in the middle of a
run, and only the other database errors reach the callers that turn them into StateUnavailable (exit 3).
application_name, set when connecting, names the channel in pg_stat_activity (ggwp-obs-trends, ggwp-obs-gsc; operator
and status commands ggwp-obs-admin).

This is the observe package's low-level write interface. The collectors never import it (test_write_paths): they reach
the database through lease.LeasedWriter. The URL is never printed; errors about it name the variable only.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType

from deerflow.persistence.engine import _json_serializer as HOST_JSON_SERIALIZER
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from ggwork_pick.observe.errors import ExitCode, ObserveFailure, Refused, sqlstate

DATABASE_URL_VARIABLE = "PICK_DATABASE_URL"
APPLICATION_NAMES = MappingProxyType({"trends": "ggwp-obs-trends", "gsc": "ggwp-obs-gsc"})
ADMIN_APPLICATION_NAME = "ggwp-obs-admin"
COMMAND_TIMEOUT_SECONDS = 30  # the host's (deerflow.persistence.engine)
MAX_LIMIT_MS = 600_000  # ten minutes, the GSC round's hard deadline (design 5.2): no step waits longer
READ_ONLY_SETTINGS = (("transaction_read_only", "on"),)
TIMED_OUT_STATES = frozenset({"55P03", "57014"})  # lock_not_available (lock_timeout), query_canceled (statement_timeout)
SQLITE_BUSY = "SQLITE_BUSY"
POSTGRES_DRIVER = "postgresql+asyncpg"
SQLITE_DRIVER = "sqlite+aiosqlite"
_POSTGRES_NAMES = frozenset({"postgres", "postgresql", POSTGRES_DRIVER})
_SQLITE_NAMES = frozenset({"sqlite", SQLITE_DRIVER})
_READ_ONLY = "ggwp_obs_read_only"  # an execution option: which BEGIN the SQLite hook sends


@dataclass(frozen=True)
class StepLimits:
    """How long one step may wait for a lock, run one statement and sit idle inside its transaction, in milliseconds.
    A step is milliseconds of SQL: waiting longer than the defaults means another process is stuck, not busy."""

    lock_ms: int = 15_000
    statement_ms: int = 30_000
    idle_ms: int = 60_000

    def __post_init__(self) -> None:
        for name in ("lock_ms", "statement_ms", "idle_ms"):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= MAX_LIMIT_MS:
                raise ValueError(f"{name} 须是 1–{MAX_LIMIT_MS} 之间的整数毫秒")

    def settings(self) -> tuple[tuple[str, str], ...]:
        """PostgreSQL's settings for them, set for the step's transaction alone."""
        return (
            ("lock_timeout", f"{self.lock_ms}ms"),
            ("statement_timeout", f"{self.statement_ms}ms"),
            ("idle_in_transaction_session_timeout", f"{self.idle_ms}ms"),
        )

    def driver_arguments(self, postgres: bool) -> dict[str, float]:
        """The connection's own limit: asyncpg's per statement (never below the server's nor the host's), SQLite's
        busy timeout (its lock wait)."""
        if postgres:
            return {"command_timeout": max(COMMAND_TIMEOUT_SECONDS, self.statement_ms / 1000)}
        return {"timeout": self.lock_ms / 1000}


STEP_LIMITS = StepLimits()


class StepTimedOut(ObserveFailure):
    """A step waited too long for a lock or a statement: another process holds the rows (a stuck step, a migration).
    Nothing of it was written; the next trigger tries again."""

    exit_code = ExitCode.FAILED


def timed_out(exc: BaseException) -> bool:
    """Whether a database error is a wait that ran out: 55P03 or 57014 on PostgreSQL, SQLITE_BUSY on SQLite."""
    if sqlstate(exc) in TIMED_OUT_STATES:
        return True
    return any(getattr(error, "sqlite_errorname", None) == SQLITE_BUSY for error in (exc, getattr(exc, "orig", None), exc.__cause__))


def _checked_limits(limits: StepLimits) -> StepLimits:
    if not isinstance(limits, StepLimits):
        raise TypeError("limits 须是 StepLimits")
    return limits


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
    """One connection at a time, opened for a transaction and closed after it. Its repr never shows the URL.

    `limits` are its steps' limits unless a transaction passes its own."""

    def __init__(self, url: str | URL, *, application_name: str, limits: StepLimits = STEP_LIMITS):
        target = normalize_url(url)
        self._postgres = target.drivername == POSTGRES_DRIVER
        connect_args = {"server_settings": {"application_name": application_name}} if self._postgres else {}
        self._engine = create_async_engine(target, poolclass=NullPool, json_serializer=HOST_JSON_SERIALIZER, connect_args=connect_args)
        if not self._postgres:
            _sqlite_transactions(self._engine)
        event.listen(self._engine.sync_engine, "do_connect", self._driver_limits)
        self._application_name = application_name
        self._limits = _checked_limits(limits)
        self._connecting = self._limits  # the limits of the transaction now connecting: one at a time, under the lock
        self._lock = asyncio.Lock()
        self._holder: asyncio.Task | None = None

    def _driver_limits(self, dialect, connection_record, cargs, cparams) -> None:
        cparams.update(self._connecting.driver_arguments(self._postgres))  # do_connect's contract: edit cparams in place

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    @property
    def application_name(self) -> str:
        return self._application_name

    def __repr__(self) -> str:
        return f"ObsDatabase(dialect={self.dialect!r}, application_name={self._application_name!r})"

    @asynccontextmanager
    async def transaction(self, *, read_only: bool = False, limits: StepLimits | None = None) -> AsyncIterator[AsyncConnection]:
        """A transaction on a connection of its own, committed when the block ends (rolled back if it raises), after
        which the connection is closed. A step inside a step would wait for itself: it is refused instead."""
        chosen = self._limits if limits is None else _checked_limits(limits)
        task = asyncio.current_task()
        if task is not None and self._holder is task:
            raise RuntimeError("一步里又开一步：唯一的连接正被这一步占着（先结束这一步）")
        async with self._lock:
            self._holder = task
            self._connecting = chosen
            try:
                async with self._begin(read_only, chosen) as conn:
                    yield conn
            except DBAPIError as exc:
                if timed_out(exc):
                    raise StepTimedOut("等锁或语句超时：另一个进程正占着这些行（卡住的一步，或正在跑的迁移）；这一步什么都没写") from exc
                raise
            finally:
                self._holder = None

    @asynccontextmanager
    async def _begin(self, read_only: bool, limits: StepLimits) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            if read_only:
                conn = await conn.execution_options(**{_READ_ONLY: True})
            async with conn.begin():
                await self._scope(conn, read_only, limits)
                yield conn

    async def _scope(self, conn: AsyncConnection, read_only: bool, limits: StepLimits) -> None:
        if self._postgres:
            settings = limits.settings() + (READ_ONLY_SETTINGS if read_only else ())
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
