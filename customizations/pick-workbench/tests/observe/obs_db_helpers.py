"""Shared by the TR-13 tests: a migrated database on both dialects, manual clocks, a stand-in collector day, and plain
reads of the runtime and budget rows through a separate test engine (never through the code under test)."""

from datetime import UTC, date, datetime

import httpx
import pg
from cryptography.fernet import Fernet
from engines import host_engine
from sqlalchemy import text
from sqlalchemy.engine import make_url

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.crypto import StateCipher
from ggwork_pick.observe.db import APPLICATION_NAMES, ObsDatabase
from ggwork_pick.observe.errors import exit_code_for
from ggwork_pick.observe.versions import COLLECTOR_VERSION

T0 = datetime(2026, 9, 25, 22, 10, tzinfo=UTC)
TARGET = date(2026, 9, 26)  # T0's target date: the 02:00 UTC publication the session feeds (D23)
UA = "Mozilla/5.0 (X11; Linux x86_64) ggwork-test"
TRENDS_WARMUP = "https://trends.google.com/trends/"


async def migrated(url: str, tmp_path) -> str:
    """`url` migrated to the head: the PostgreSQL copy of the template already is, the SQLite file not yet. Each test
    module's obs_url fixture calls it with pick_db_url, so every test runs on both dialects."""
    await pg.migrate(url, tmp_path / "files")
    return url


def clock_at(moment: datetime = T0) -> ManualClock:
    return ManualClock(moment)


def open_db(url: str, channel: str = "trends") -> ObsDatabase:
    return ObsDatabase(url, application_name=APPLICATION_NAMES[channel])


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def new_cipher() -> StateCipher:
    return StateCipher([Fernet.generate_key()])


def is_postgres(url: str) -> bool:
    return make_url(url).get_backend_name() == "postgresql"


def collector_env(url: str, **overrides: str | None) -> dict[str, str]:
    """What a cron service's environment holds (plan section 10, S5); None removes a variable."""
    env = {"PICK_DATABASE_URL": url, "PICK_OBS_EXPECTED_COLLECTOR": COLLECTOR_VERSION}
    if is_postgres(url):
        env["PICK_OBS_EXPECTED_ROLE"] = make_url(url).username
    merged = env | overrides
    return {name: value for name, value in merged.items() if value is not None}


async def rows(url: str, sql: str, **params) -> list[dict]:
    engine = host_engine(url)
    try:
        async with engine.connect() as conn:
            return [dict(row) for row in (await conn.execute(text(sql), params)).mappings().all()]
    finally:
        await engine.dispose()


async def execute(url: str, sql: str, **params) -> None:
    engine = host_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


def as_json(value):
    """A JSON column as a raw query returns it: parsed on PostgreSQL (asyncpg's codec), text on SQLite."""
    import json

    return json.loads(value) if isinstance(value, str) else value


async def runtime_row(url: str, channel: str = "trends") -> dict | None:
    found = await rows(url, "select * from ggwp_obs_runtime where channel = :c", c=channel)
    return found[0] if found else None


async def budget_rows(url: str, channel: str = "trends") -> list[dict]:
    return await rows(url, "select * from ggwp_obs_budget where channel = :c order by budget_day", c=channel)


async def count(url: str, table: str) -> int:
    return (await rows(url, f"select count(*) as n from {table}"))[0]["n"]


async def snapshot(url: str, tables: tuple[str, ...]) -> dict[str, list[tuple]]:
    """Every row of each table, in a stable order: equal snapshots mean nothing was written."""
    found = {}
    for table in tables:
        listed = await rows(url, f"select * from {table}")
        found[table] = sorted(tuple(sorted((key, repr(value)) for key, value in row.items())) for row in listed)
    return found


class CountingTransport(httpx.AsyncBaseTransport):
    """Stands in for Google: counts what would have been sent."""

    def __init__(self):
        self.sent: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(str(request.url))
        return httpx.Response(200, text="ok")


async def collector_day(env: dict, cipher: StateCipher, clock: ManualClock, transport: CountingTransport, *, owner: str = "proc-day") -> int:
    """A collector's start as TR-14 wires it: self-check, the lease, the state, and only then the first HTTP request.
    Returns the exit status the entry point would exit with."""
    from ggwork_pick.observe.lease import DbStateStore, collector_session

    try:
        async with collector_session("trends", clock=clock, environ=env, owner=owner) as session:
            await DbStateStore(session.writer, cipher).load()
            async with httpx.AsyncClient(transport=transport) as client:
                await client.get(TRENDS_WARMUP)
    except Exception as exc:  # the entry point maps every failure to its exit status
        return int(exit_code_for(exc))
    return 0
