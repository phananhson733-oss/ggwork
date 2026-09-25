"""TR-13 × TR-12 seam: the collector's start, its state and reset-disable work as the observer role and nothing more.

Production runs both crons as pick_observer (plan D3, S5), a LOGIN role with the table grants 0007 gives when the role
exists (TR-12's list, D14). Here a per-test observer role logs in with a password, the database is migrated with
PICK_OBS_OBSERVER_ROLE naming it, and everything TR-13 does runs on that connection: the self-check (current_user, the
version table), the lease on the runtime row, the state on the runtime and budget rows, the read-only door, and the
operator's reset. PostgreSQL only; skips without PICK_TEST_PG_URL.
"""

from datetime import timedelta

import pg
import pytest
import pytest_asyncio
from obs_db_helpers import T0, TARGET, CountingTransport, clock_at, collector_day, collector_env, new_cipher, runtime_row
from sqlalchemy import select

from ggwork_pick.models import obs_runtime
from ggwork_pick.observe.admin import cmd_reset_disable
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.lease import DbStateStore, collector_session, status_reader
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends import breaker
from ggwork_pick.observe.trends.budget import BudgetDay

OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"
PASSWORD = "observer-test-pw"


@pytest_asyncio.fixture
async def observer_url(pg_cluster, pg_reader_role, monkeypatch, tmp_path):
    """(the observer's URL, the admin's URL) for a database migrated while the observer role existed."""
    from psycopg import sql

    role = pg.unique_name("pick_observer")
    pg_cluster._execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(PASSWORD)))
    monkeypatch.setenv(OBSERVER_ROLE_ENV, role)
    name = pg.unique_name("o")
    pg_cluster.create_database(name)
    try:
        admin = pg_cluster.async_url(name)
        await pg.migrate(admin, tmp_path / "files")
        observer = pg_cluster.url.set(drivername="postgresql+asyncpg", database=name, username=role, password=PASSWORD)
        yield observer.render_as_string(hide_password=False), admin
    finally:
        pg_cluster.drop_database(name)
        pg_cluster.drop_role(role)


@pytest.mark.asyncio
async def test_everything_tr13_does_runs_as_the_observer(observer_url):
    url, admin = observer_url
    env = collector_env(url)  # PICK_OBS_EXPECTED_ROLE is the observer itself
    transport = CountingTransport()
    assert await collector_day(env, new_cipher(), clock_at(T0), transport) == ExitCode.OK
    assert len(transport.sent) == 1

    cipher = new_cipher()
    disabled = breaker.BreakerState(breaker.BreakerDay(TARGET, extinguished="trips"), (TARGET,), disabled_on=TARGET)
    async with collector_session("trends", clock=clock_at(T0), environ=env) as session:
        store = DbStateStore(session.writer, cipher)
        await store.save(RuntimeState(breaker=disabled.to_dict(), budget=BudgetDay(TARGET, reserved=4).to_dict()))
        assert (await store.load()).section("budget", BudgetDay.from_dict).reserved == 4
        async with status_reader("trends", environ=env) as reader:
            owner = (await reader.execute(select(obs_runtime.c.lease_owner).where(obs_runtime.c.channel == "trends"))).scalar_one()
        assert owner == session.writer.token.owner

    later = clock_at(T0 + timedelta(minutes=1))
    assert await cmd_reset_disable.run(["--operator", "wzb"], environ=env, clock=later) == ExitCode.OK
    row = await runtime_row(admin)
    assert (row["reset_by"], row["disabled_at"], row["lease_generation"]) == ("wzb", None, 2)


@pytest.mark.asyncio
async def test_the_role_check_names_the_observer(observer_url):
    """Expecting pick_board_reader while connected as the observer (or the other way round) exits 2."""
    url, _ = observer_url
    transport = CountingTransport()
    status = await collector_day(collector_env(url, PICK_OBS_EXPECTED_ROLE="pick_board_reader"), new_cipher(), clock_at(T0), transport)
    assert (status, transport.sent) == (ExitCode.REFUSED, [])
