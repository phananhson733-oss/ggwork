"""A stand-in for Supabase's postgres role, for the scripts under docs/pick-workbench/supabase (plan 3.1, 6.2, P0-4; trends
radar TR-12, D3).

Supabase's `postgres` is not a superuser: it has CREATEROLE and owns the `postgres` database. The stand-in is a NOSUPERUSER
CREATEROLE role owning a per-test database, and psql runs the scripts logged in as it. Never as the cluster superuser: a
superuser passes the SET ROLE and grant checks, so the cases proving a line necessary would pass with the line missing.

Roles are cluster-wide and the cluster is shared with the other PostgreSQL tests, so every role the scripts name, and the
database, gets a per-test suffix; otherwise the scripts run as committed. The migrations run as the stand-in deerflow_app,
as production's do, so the grants they give come from the owner there too.
"""

import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pg
import pytest

DOCS = Path(__file__).resolve().parents[3] / "docs/pick-workbench"
SUPABASE = DOCS / "supabase"
SCRIPT = SUPABASE / "bootstrap.sql"
UNDO = SUPABASE / "bootstrap-undo.sql"
OBSERVER = SUPABASE / "bootstrap-observer.sql"
OBSERVER_UNDO = SUPABASE / "bootstrap-observer-undo.sql"
RUNBOOK = DOCS / "supabase.md"
OBSERVER_RUNBOOK = DOCS / "observe-runbook/observer-role.md"
# What production ran on 2026-09-23 (P0-6): bootstrap.sql before the observer, byte for byte (test_bootstrap_sql pins it).
P0_SCRIPT = Path(__file__).resolve().parent / "fixtures/supabase/bootstrap-p0.sql"
# The names the scripts use; anon and authenticated are Supabase's API roles, the stand-in cluster gets lookalikes.
ROLES = ("deerflow_app", "pick_board_reader", "pick_observer", "anon", "authenticated")
DATABASE = "postgres"
OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"
READ_ALL = ("pg_read_all_data", "pg_write_all_data")
# psql exits with 3 when ON_ERROR_STOP stops a script.
STOPPED = 3


def rename(text: str, names: dict[str, str]) -> str:
    for original, renamed in names.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])", renamed, text)
    return text


def error(output: str, script: str) -> tuple[str, str]:
    """The script line psql reports the (only) error at, and the server's message."""
    match = re.search(r"^psql:\S+?\.sql:(\d+): ERROR:\s+(.+)$", output, re.M)
    assert match, output
    return script.splitlines()[int(match[1]) - 1].strip(), match[2]


def sql_lines(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("--")]


@dataclass(frozen=True)
class StandIn:
    cluster: pg.PgCluster
    psql: str
    workdir: Path
    suffix: str = field(default_factory=lambda: uuid4().hex[:10])
    password: str = field(default_factory=lambda: secrets.token_hex(16))

    def role(self, name: str) -> str:
        return f"{name}_{self.suffix}"

    @property
    def database(self) -> str:
        return f"bs_{self.suffix}"

    @property
    def owner(self) -> str:
        return self.role("sb_postgres")

    @property
    def app(self) -> str:
        return self.role("deerflow_app")

    @property
    def reader(self) -> str:
        return self.role("pick_board_reader")

    @property
    def observer(self) -> str:
        return self.role("pick_observer")

    def script(self, path: Path = SCRIPT, *, without: tuple[str, ...] = ()) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        for statement in without:
            assert [line.strip() for line in lines].count(statement) == 1, statement
            lines = [line for line in lines if line.strip() != statement]
        return self.renamed("\n".join(lines) + "\n")

    def renamed(self, text: str) -> str:
        """SQL as written, the roles and the database given this stand-in's names."""
        return rename(text, {name: self.role(name) for name in ROLES} | {DATABASE: self.database})

    def run(self, script: str, *, database: str | None = None, role: str | None = None) -> subprocess.CompletedProcess:
        """psql -f, logged in as the stand-in owner (or `role`); stdout and stderr interleaved the way an operator sees them."""
        path = self.workdir / "script.sql"
        path.write_text(script, encoding="utf-8")
        url = self.cluster.url
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")} | {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": database or self.database,
            "PGUSER": role or self.owner,
            "PGPASSWORD": self.password,
            "PGCLIENTENCODING": "UTF8",
            "PGCONNECT_TIMEOUT": "10",
            "LC_ALL": "C",
        }
        command = [self.psql, "-X", "-w", "-f", str(path)]
        return subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)

    def connect(self, role: str | None = None, *, database: str | None = None, autocommit: bool = True):
        """The cluster superuser by default; a stand-in role logs in with the per-test password."""
        import psycopg

        url = self.cluster.url
        return psycopg.connect(
            host=url.host,
            port=url.port or 5432,
            dbname=database or self.database,
            user=role or url.username,
            password=self.password if role else url.password,
            connect_timeout=10,
            autocommit=autocommit,
        )

    def admin(self, *statements: str, database: str | None = None) -> None:
        with self.connect(database=database or self.cluster.url.database) as conn:
            for statement in statements:
                conn.execute(statement)

    def query(self, statement: str, *params) -> list[tuple]:
        with self.connect() as conn:
            return conn.execute(statement, params).fetchall()

    def usage(self, role: str, schema: str) -> bool:
        return self.query("SELECT has_schema_privilege(%s, %s, 'USAGE')", role, schema)[0][0]

    def set_passwords(self, *roles: str) -> None:
        """What \\password does in the runbook."""
        self.admin(*(f"ALTER ROLE {role} PASSWORD '{self.password}'" for role in roles))

    def url_as(self, role: str, *, driver: str = "postgresql+asyncpg") -> str:
        """A SQLAlchemy URL logging in as a stand-in role (deerflow_app for the migrations and the mirror publish)."""
        url = self.cluster.url.set(drivername=driver, username=role, password=self.password, database=self.database)
        return url.render_as_string(hide_password=False)

    def leftovers(self) -> list[str]:
        """What a stopped script left behind: every script runs in one transaction, so nothing."""
        roles = self.query("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [self.app, self.reader, self.observer])
        schemas = self.query("SELECT nspname FROM pg_namespace WHERE nspname IN ('deerflow', 'pick_mirror', 'pick_obs')")
        return sorted(name for (name,) in roles + schemas)


@contextmanager
def stand_in_for(cluster: pg.PgCluster, workdir: Path, monkeypatch=None) -> Iterator[StandIn]:
    """A fresh stand-in: its owner, database and API-role lookalikes; the migrations' role variables point at its roles."""
    psql = shutil.which("psql")
    if psql is None:
        pytest.fail("the bootstrap scripts are psql scripts: put psql on PATH")
    stand_in = StandIn(cluster, psql, workdir)
    if monkeypatch is not None:
        monkeypatch.setenv(pg.READER_ROLE_ENV, stand_in.reader)
        monkeypatch.setenv(OBSERVER_ROLE_ENV, stand_in.observer)
    try:
        stand_in.admin(
            f"CREATE ROLE {stand_in.role('anon')} NOLOGIN",
            f"CREATE ROLE {stand_in.role('authenticated')} NOLOGIN",
            f"CREATE ROLE {stand_in.owner} LOGIN NOSUPERUSER CREATEROLE PASSWORD '{stand_in.password}'",
            f"CREATE DATABASE {stand_in.database} OWNER {stand_in.owner}",
        )
        yield stand_in
    finally:
        # The databases take the grants with them; the scripts' roles go before the owner that administers them.
        stand_in.admin(
            f"DROP DATABASE IF EXISTS {stand_in.database} WITH (FORCE)",
            f"DROP DATABASE IF EXISTS {stand_in.database}_elsewhere WITH (FORCE)",
            f"DROP ROLE IF EXISTS {stand_in.app}, {stand_in.reader}, {stand_in.observer}",
            f"DROP ROLE IF EXISTS {stand_in.owner}, {stand_in.role('anon')}, {stand_in.role('authenticated')}, {stand_in.role('other')}",
        )


def bootstrap(stand_in: StandIn, path: Path = SCRIPT) -> subprocess.CompletedProcess:
    """A clean run of a bootstrap script: exit 0 and not one WARNING (psql prints GRANT even when nothing was granted)."""
    result = stand_in.run(stand_in.script(path))
    assert result.returncode == 0, result.stdout
    assert "WARNING" not in result.stdout, result.stdout
    return result


def statuses(output: str) -> list[str]:
    """psql's command tags, one per statement, as the runbooks list them."""
    return [line for line in output.splitlines() if line.strip()]


def expected_output(runbook: Path, marker: str) -> list[str]:
    """The fenced block right after `marker` in a runbook, its list-item indent taken off: what psql must print, or run."""
    text = runbook.read_text(encoding="utf-8")
    assert text.count(marker) == 1, marker
    block = re.search(r"^( *)```[a-z]*\n(.*?)\n\1```$", text[text.index(marker) :], re.S | re.M)
    assert block, marker
    return [line.removeprefix(block[1]) for line in block[2].splitlines()]


async def migrate_as_app(stand_in: StandIn, workdir: Path, revision: str = "head") -> None:
    """The ggwp chain up to `revision`, run as deerflow_app like the gateway's start-up upgrade."""
    import revisions
    from engines import host_engine

    url = stand_in.url_as(stand_in.app)
    if revision == "head":
        await pg.migrate(url, workdir)
        return
    engine = host_engine(url)
    try:
        await revisions.upgrade(engine, revision)
    finally:
        await engine.dispose()


def version_table(stand_in: StandIn, revision: str = "0006") -> None:
    """The migrations' version table as the gateway's start-up upgrade leaves it, owned by deerflow_app, without running
    the chain: production is at 0006 when S2 runs bootstrap-observer.sql, which grants the observer SELECT on it (D41)."""
    with stand_in.connect() as conn:
        conn.execute(f"SET ROLE {stand_in.app}")
        conn.execute("CREATE TABLE deerflow.ggwp_alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
        conn.execute("INSERT INTO deerflow.ggwp_alembic_version VALUES (%s)", (revision,))


def host_tables(stand_in: StandIn) -> None:
    """What the host keeps next to the ggwp tables and the observer must never reach: users and the checkpoints."""
    with stand_in.connect(stand_in.app) as conn:
        for table in ("users", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"CREATE TABLE deerflow.{table} (id int PRIMARY KEY, secret text)")
            conn.execute(f"INSERT INTO deerflow.{table} VALUES (1, 'not for the observer')")
