"""docs/pick-workbench/supabase/bootstrap.sql on a stand-in for Supabase (plan 3.1, 6.2, P0-4).

Supabase's `postgres` is not a superuser: it has CREATEROLE and owns the `postgres` database. The stand-in is a NOSUPERUSER CREATEROLE
role owning a per-test database, and psql runs the script logged in as it. Never as the cluster superuser: a superuser passes the
SET ROLE and grant checks, so the cases proving a line necessary would pass with the line missing.

Roles are cluster-wide and the cluster is shared with the other PostgreSQL tests, so every role the script names, and the database,
gets a per-test suffix; otherwise the script runs as committed. The PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pg
import pytest

DOCS = Path(__file__).resolve().parents[3] / "docs/pick-workbench"
SCRIPT = DOCS / "supabase/bootstrap.sql"
UNDO = DOCS / "supabase/bootstrap-undo.sql"
RUNBOOK = DOCS / "supabase.md"
# The names the script uses; anon and authenticated are Supabase's API roles, the stand-in cluster gets lookalikes.
ROLES = ("deerflow_app", "pick_board_reader", "anon", "authenticated")
DATABASE = "postgres"
GRANT_SET = "GRANT deerflow_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;"
SET_ROLE = "SET ROLE deerflow_app;"
RESET_ROLE = "RESET ROLE;"
READ_ALL = ("pg_read_all_data", "pg_write_all_data")
# psql exits with 3 when ON_ERROR_STOP stops a script.
STOPPED = 3


def _sql_lines() -> list[str]:
    lines = (line.strip() for line in SCRIPT.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("--")]


def _rename(text: str, names: dict[str, str]) -> str:
    for original, renamed in names.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])", renamed, text)
    return text


def _error(output: str, script: str) -> tuple[str, str]:
    """The script line psql reports the (only) error at, and the server's message."""
    match = re.search(r"^psql:\S+?\.sql:(\d+): ERROR:\s+(.+)$", output, re.M)
    assert match, output
    return script.splitlines()[int(match[1]) - 1].strip(), match[2]


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

    def script(self, path: Path = SCRIPT, *, without: tuple[str, ...] = ()) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        for statement in without:
            assert [line.strip() for line in lines].count(statement) == 1, statement
            lines = [line for line in lines if line.strip() != statement]
        return _rename("\n".join(lines) + "\n", {name: self.role(name) for name in ROLES} | {DATABASE: self.database})

    def run(self, script: str, *, database: str | None = None) -> subprocess.CompletedProcess:
        """psql -f, logged in as the stand-in owner; stdout and stderr interleaved the way an operator sees them."""
        path = self.workdir / "bootstrap.sql"
        path.write_text(script, encoding="utf-8")
        url = self.cluster.url
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")} | {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": database or self.database,
            "PGUSER": self.owner,
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

    def leftovers(self) -> list[str]:
        """What a stopped script left behind: it runs in one transaction, so nothing."""
        roles = self.query("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [self.app, self.reader])
        schemas = self.query("SELECT nspname FROM pg_namespace WHERE nspname IN ('deerflow', 'pick_mirror')")
        return sorted(name for (name,) in roles + schemas)


@pytest.fixture
def stand_in(pg_cluster, tmp_path):
    psql = shutil.which("psql")
    if psql is None:
        pytest.fail("bootstrap.sql is a psql script: put psql on PATH")
    stand_in = StandIn(pg_cluster, psql, tmp_path)
    try:
        stand_in.admin(
            f"CREATE ROLE {stand_in.role('anon')} NOLOGIN",
            f"CREATE ROLE {stand_in.role('authenticated')} NOLOGIN",
            f"CREATE ROLE {stand_in.owner} LOGIN NOSUPERUSER CREATEROLE PASSWORD '{stand_in.password}'",
            f"CREATE DATABASE {stand_in.database} OWNER {stand_in.owner}",
        )
        yield stand_in
    finally:
        # The databases take the grants with them; the script's roles go before the owner that administers them.
        stand_in.admin(
            f"DROP DATABASE IF EXISTS {stand_in.database} WITH (FORCE)",
            f"DROP DATABASE IF EXISTS {stand_in.database}_elsewhere WITH (FORCE)",
            f"DROP ROLE IF EXISTS {stand_in.app}, {stand_in.reader}",
            f"DROP ROLE IF EXISTS {stand_in.owner}, {stand_in.role('anon')}, {stand_in.role('authenticated')}, {stand_in.role('other')}",
        )


@pytest.fixture(params=["createrole", "read_all_data"])
def bootstrapped(request, stand_in):
    """A clean run; read_all_data is the Supabase-like owner on which the missing SET ROLE only warned (audit host-1)."""
    if request.param == "read_all_data":
        stand_in.admin(f"GRANT {', '.join(READ_ALL)} TO {stand_in.owner}")
    result = stand_in.run(stand_in.script())
    assert result.returncode == 0, result.stdout
    # psql prints GRANT even when the server only warned that nothing was granted.
    assert "WARNING" not in result.stdout, result.stdout
    # What \password does in the runbook.
    stand_in.admin(*(f"ALTER ROLE {role} PASSWORD '{stand_in.password}'" for role in (stand_in.app, stand_in.reader)))
    return stand_in


def test_the_script_caps_both_roles_at_twenty_and_holds_no_password():
    sql = _sql_lines()
    assert sql[0] == r"\set ON_ERROR_STOP on"
    assert not [line for line in sql if "PASSWORD" in line.upper()]
    assert [line for line in sql if line.startswith("CREATE ROLE")] == [
        "CREATE ROLE deerflow_app LOGIN NOINHERIT CONNECTION LIMIT 20;",
        "CREATE ROLE pick_board_reader LOGIN NOINHERIT CONNECTION LIMIT 20;",
    ]

    def first(prefix: str) -> int:
        return next(index for index, line in enumerate(sql) if line.startswith(prefix))

    def last(prefix: str) -> int:
        return max(index for index, line in enumerate(sql) if line.startswith(prefix))

    # The owner grants run as deerflow_app, the ALTER ROLEs as the bootstrap role again (plan 3.1).
    assert sql.index(GRANT_SET) < first("CREATE SCHEMA")
    assert last("CREATE SCHEMA") < sql.index(SET_ROLE) < first("REVOKE")
    assert last("GRANT USAGE") < sql.index(RESET_ROLE) < first("ALTER ROLE")


def test_the_runbook_and_the_script_hold_no_secret():
    for path in (RUNBOOK, SCRIPT, UNDO):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{path.name}:{number}"
            for password in re.findall(r"postgres(?:ql)?://[^\s:@/]+:([^\s@]+)@", line):
                assert password.startswith("<") and password.endswith(">"), where
            assert not re.search(r"eyJ[A-Za-z0-9_-]{16,}|sb_secret_|sb_publishable_|sbp_[0-9a-f]", line), where
            assert not re.search(r"PASSWORD\s+'", line, re.I), where
            # Passwords and tokens come from openssl rand -hex 32; the only long hex allowed is the backup's sha256.
            assert "sha256" in line.lower() or not re.search(r"[0-9a-fA-F]{32,}", line), where


def test_bootstrap_grants_what_the_plan_lists(bootstrapped):
    app, reader = bootstrapped.app, bootstrapped.reader
    roles = {
        name: rest
        for name, *rest in bootstrapped.query(
            "SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = ANY(%s)", [app, reader]
        )
    }
    login, inherit, limit, config = roles[app]
    assert (login, inherit, limit) == (True, False, 20)
    assert set(config) == {"search_path=deerflow", "TimeZone=UTC", "idle_in_transaction_session_timeout=5min"}
    login, inherit, limit, config = roles[reader]
    assert (login, inherit, limit) == (True, False, 20)
    assert set(config) == {
        "search_path=pick_mirror",
        "default_transaction_read_only=on",
        "statement_timeout=8s",
        "idle_in_transaction_session_timeout=15s",
        "TimeZone=UTC",
    }
    # The \dn+ view: owned by deerflow_app, nothing for PUBLIC, anon or authenticated, USAGE for the reader on pick_mirror only.
    schemas = {
        name: (owner, acl)
        for name, owner, acl in bootstrapped.query(
            "SELECT nspname, nspowner::regrole::text, nspacl::text[] FROM pg_namespace WHERE nspname IN ('deerflow', 'pick_mirror')"
        )
    }
    assert schemas == {"deerflow": (app, [f"{app}=UC/{app}"]), "pick_mirror": (app, [f"{app}=UC/{app}", f"{reader}=U/{app}"])}
    assert bootstrapped.usage(reader, "pick_mirror")
    assert not bootstrapped.usage(reader, "deerflow")
    for role in (bootstrapped.role("anon"), bootstrapped.role("authenticated")):
        assert not bootstrapped.usage(role, "deerflow") and not bootstrapped.usage(role, "pick_mirror")
    database = bootstrapped.database
    assert bootstrapped.query(
        "SELECT has_database_privilege(%s, %s, 'CONNECT'), has_database_privilege(%s, %s, 'CREATE'),"
        " has_database_privilege(%s, %s, 'CONNECT'), has_database_privilege(%s, %s, 'CREATE')",
        *(app, database, app, database, reader, database, reader, database),
    ) == [(True, True, True, False)]
    # The bootstrap role may become deerflow_app but does not inherit its privileges.
    membership = "SELECT bool_or(set_option), bool_or(inherit_option) FROM pg_auth_members WHERE roleid = %s::regrole AND member = %s::regrole"
    assert bootstrapped.query(membership, app, bootstrapped.owner) == [(True, False)]


def _show(conn, setting: str) -> str:
    return conn.execute(f"SHOW {setting}").fetchone()[0]


def test_deerflow_app_logs_in_with_its_defaults_and_creates_schemas(bootstrapped):
    import psycopg

    with bootstrapped.connect(bootstrapped.app) as conn:
        assert [_show(conn, name) for name in ("search_path", "TimeZone", "idle_in_transaction_session_timeout")] == ["deerflow", "UTC", "5min"]
        # The host engine and checkpointer run this at every start, the schema already there.
        conn.execute("CREATE SCHEMA IF NOT EXISTS deerflow")
        # The mirror writer's per-version schemas.
        conn.execute("CREATE SCHEMA pickm_v000001")
    # PostgreSQL checks CREATE on the database before it looks for the schema: without the grant every start fails.
    bootstrapped.admin(f"REVOKE CREATE ON DATABASE {bootstrapped.database} FROM {bootstrapped.app}")
    with bootstrapped.connect(bootstrapped.app) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for database"):
        conn.execute("CREATE SCHEMA IF NOT EXISTS deerflow")


def test_the_reader_logs_in_read_only_and_reaches_nothing_in_deerflow(bootstrapped):
    import psycopg

    with bootstrapped.connect(bootstrapped.app) as conn:
        conn.execute("CREATE TABLE deerflow.probe (x int)")
    with bootstrapped.connect(bootstrapped.reader, autocommit=False) as conn:
        assert [_show(conn, name) for name in ("default_transaction_read_only", "statement_timeout", "search_path")] == ["on", "8s", "pick_mirror"]
        assert [_show(conn, name) for name in ("idle_in_transaction_session_timeout", "TimeZone")] == ["15s", "UTC"]
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for schema deerflow"):
            conn.execute("SELECT * FROM deerflow.probe")
        conn.rollback()
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE SCHEMA reader_owned")
        conn.rollback()
        # Read-only is only a default the reader may lift; the privilege is what stops it.
        conn.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for database"):
            conn.execute("CREATE SCHEMA reader_owned")
        conn.rollback()


def test_the_reader_connects_where_public_cannot(stand_in):
    # Both roles log in on the script's own CONNECT grants, not on PUBLIC's.
    stand_in.admin(f"REVOKE CONNECT ON DATABASE {stand_in.database} FROM PUBLIC")
    result = stand_in.run(stand_in.script())
    assert result.returncode == 0 and "WARNING" not in result.stdout, result.stdout
    stand_in.admin(*(f"ALTER ROLE {role} PASSWORD '{stand_in.password}'" for role in (stand_in.app, stand_in.reader)))
    for role in (stand_in.app, stand_in.reader):
        with stand_in.connect(role) as conn:
            assert conn.execute("select 1").fetchone() == (1,)


def test_without_the_set_grant_create_schema_authorization_fails(stand_in):
    script = stand_in.script(without=(GRANT_SET,))
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = _error(result.stdout, script)
    assert statement == f"CREATE SCHEMA IF NOT EXISTS deerflow AUTHORIZATION {stand_in.app};"
    assert message == f'must be able to SET ROLE "{stand_in.app}"'
    assert stand_in.leftovers() == []


def test_without_set_role_the_first_revoke_is_denied(stand_in):
    script = stand_in.script(without=(SET_ROLE, RESET_ROLE))
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = _error(result.stdout, script)
    assert statement == "REVOKE ALL ON SCHEMA deerflow, pick_mirror FROM PUBLIC;"
    assert message == "permission denied for schema deerflow"
    assert stand_in.leftovers() == []


def test_without_set_role_an_owner_reading_all_data_only_warns_and_the_reader_lacks_usage(stand_in):
    """Why the runbook reads the output for WARNING: this run exits 0 and prints GRANT, yet granted nothing."""
    stand_in.admin(f"GRANT {', '.join(READ_ALL)} TO {stand_in.owner}")
    result = stand_in.run(stand_in.script(without=(SET_ROLE, RESET_ROLE)))
    assert result.returncode == 0, result.stdout
    assert 'WARNING:  no privileges were granted for "pick_mirror"' in result.stdout
    assert not stand_in.usage(stand_in.reader, "pick_mirror")


def test_a_database_the_bootstrap_role_does_not_own_stops_at_the_create_check(stand_in):
    other = stand_in.role("other")
    stand_in.admin(f"CREATE ROLE {other} NOLOGIN", f"ALTER DATABASE {stand_in.database} OWNER TO {other}")
    script = stand_in.script()
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    # The GRANT ON DATABASE only warned; the DO block turns that into a stop.
    assert f'WARNING:  no privileges were granted for "{stand_in.database}"' in result.stdout
    statement, message = _error(result.stdout, script)
    assert statement == "END $$;"
    assert message.startswith(f"{stand_in.app} 没有拿到 {stand_in.database} 库的 CREATE 权限")
    assert stand_in.leftovers() == []


def test_another_database_is_refused_before_anything_is_created(stand_in):
    elsewhere = f"{stand_in.database}_elsewhere"
    stand_in.admin(f"CREATE DATABASE {elsewhere} OWNER {stand_in.owner}")
    script = stand_in.script()
    result = stand_in.run(script, database=elsewhere)
    assert result.returncode == STOPPED, result.stdout
    _, message = _error(result.stdout, script)
    assert message.startswith(f"当前连接的是 {elsewhere} 库")
    assert stand_in.leftovers() == []


def test_the_undo_script_leaves_the_project_as_before_and_bootstrap_runs_again(bootstrapped):
    result = bootstrapped.run(bootstrapped.script(UNDO))
    assert result.returncode == 0, result.stdout
    assert "WARNING" not in result.stdout, result.stdout
    # The roles are gone, so no grant on the database can name them either.
    assert bootstrapped.leftovers() == []
    again = bootstrapped.run(bootstrapped.script())
    assert again.returncode == 0 and "WARNING" not in again.stdout, again.stdout


def test_the_undo_script_refuses_once_the_host_has_created_tables(bootstrapped):
    with bootstrapped.connect(bootstrapped.app) as conn:
        conn.execute("CREATE TABLE deerflow.users (id int)")
    script = bootstrapped.script(UNDO)
    result = bootstrapped.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = _error(result.stdout, script)
    assert statement == "DROP SCHEMA deerflow, pick_mirror;"
    assert "because other objects depend on" in message
    assert bootstrapped.leftovers() == sorted([bootstrapped.app, bootstrapped.reader, "deerflow", "pick_mirror"])
    with bootstrapped.connect(bootstrapped.app) as conn:
        assert conn.execute("SELECT count(*) FROM deerflow.users").fetchone() == (0,)
