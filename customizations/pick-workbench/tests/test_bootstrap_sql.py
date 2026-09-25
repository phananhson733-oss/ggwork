"""docs/pick-workbench/supabase/*.sql on a stand-in for Supabase (plan 3.1, 6.2, P0-4; trends radar TR-12, D3, D4).

bootstrap.sql builds a new project with all three roles; production ran its P0 version (tests/fixtures/supabase/bootstrap-p0.sql)
on 2026-09-23 and gets pick_observer from bootstrap-observer.sql before migration 0007 reaches it (S2 before S3). Both
paths must end in the same grants. The stand-in (supabase_stand_in.py) is a NOSUPERUSER CREATEROLE role owning a
per-test database; the PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import hashlib
import re

import pytest
from supabase_stand_in import (
    OBSERVER,
    OBSERVER_RUNBOOK,
    OBSERVER_UNDO,
    P0_SCRIPT,
    READ_ALL,
    RUNBOOK,
    SCRIPT,
    STOPPED,
    UNDO,
    bootstrap,
    error,
    expected_output,
    host_tables,
    migrate_as_app,
    sql_lines,
    stand_in_for,
    statuses,
)

GRANT_SET = "GRANT deerflow_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;"
SET_ROLE = "SET ROLE deerflow_app;"
RESET_ROLE = "RESET ROLE;"
# The git blob of docs/pick-workbench/supabase/bootstrap.sql at 5f0cd65, the version production ran (supabase.md section 12).
P0_BLOB = "79be51f1bb1a7f82c298ae30eeb2c4b6410c0bc0"
APP_CONFIG = {"search_path=deerflow", "TimeZone=UTC", "idle_in_transaction_session_timeout=5min"}
READER_CONFIG = {
    "search_path=pick_mirror",
    "default_transaction_read_only=on",
    "statement_timeout=8s",
    "idle_in_transaction_session_timeout=15s",
    "TimeZone=UTC",
}
OBSERVER_CONFIG = {"search_path=deerflow", "TimeZone=UTC", "idle_in_transaction_session_timeout=1min", "statement_timeout=2min"}
OWNERS = ("createrole", "read_all_data")


@pytest.fixture
def stand_in(pg_cluster, tmp_path, monkeypatch):
    with stand_in_for(pg_cluster, tmp_path, monkeypatch) as made:
        yield made


@pytest.fixture(params=OWNERS)
def owner_kind(request, stand_in):
    """read_all_data is the Supabase-like owner on which a missing SET ROLE only warned (audit host-1)."""
    if request.param == "read_all_data":
        stand_in.admin(f"GRANT {', '.join(READ_ALL)} TO {stand_in.owner}")
    return request.param


@pytest.fixture
def bootstrapped(stand_in, owner_kind):
    bootstrap(stand_in)
    stand_in.set_passwords(stand_in.app, stand_in.reader, stand_in.observer)
    return stand_in


def _roles(stand_in) -> dict[str, tuple]:
    rows = stand_in.query(
        "SELECT rolname, rolcanlogin, rolinherit, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = ANY(%s)",
        [stand_in.app, stand_in.reader, stand_in.observer],
    )
    return {name: (login, inherit, limit, set(config or ())) for name, login, inherit, limit, config in rows}


def _schema_acls(stand_in, *schemas: str) -> dict[str, tuple[str, list[str]]]:
    rows = stand_in.query("SELECT nspname, nspowner::regrole::text, nspacl::text[] FROM pg_namespace WHERE nspname = ANY(%s)", list(schemas))
    return {name: (owner, sorted(acl or ())) for name, owner, acl in rows}


def _database_privileges(stand_in, role: str) -> tuple[bool, bool]:
    return stand_in.query(
        "SELECT has_database_privilege(%s, %s, 'CONNECT'), has_database_privilege(%s, %s, 'CREATE')", role, stand_in.database, role, stand_in.database
    )[0]


def _connect_entry(stand_in, role: str) -> bool:
    """CONNECT on the database granted to the role by name, not merely through PUBLIC."""
    found = stand_in.query(
        "SELECT EXISTS (SELECT 1 FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname = %s AND a.grantee = %s::regrole AND a.privilege_type = 'CONNECT')",
        stand_in.database,
        role,
    )
    return found[0][0]


# ---------------------------------------------------------------- the full bootstrap (a new project)


def test_the_script_caps_every_role_at_twenty_and_holds_no_password():
    sql = sql_lines(SCRIPT)
    assert sql[0] == r"\set ON_ERROR_STOP on"
    assert not [line for line in sql if "PASSWORD" in line.upper()]
    assert [line for line in sql if line.startswith("CREATE ROLE")] == [
        "CREATE ROLE deerflow_app LOGIN NOINHERIT CONNECTION LIMIT 20;",
        "CREATE ROLE pick_board_reader LOGIN NOINHERIT CONNECTION LIMIT 20;",
        "CREATE ROLE pick_observer LOGIN NOINHERIT CONNECTION LIMIT 20;",
    ]

    def first(prefix: str) -> int:
        return next(index for index, line in enumerate(sql) if line.startswith(prefix))

    def last(prefix: str) -> int:
        return max(index for index, line in enumerate(sql) if line.startswith(prefix))

    # The owner grants run as deerflow_app, the ALTER ROLEs as the bootstrap role again (plan 3.1).
    assert sql.index(GRANT_SET) < first("CREATE SCHEMA")
    assert last("CREATE SCHEMA") < sql.index(SET_ROLE) < first("REVOKE")
    assert last("GRANT USAGE") < sql.index(RESET_ROLE) < first("ALTER ROLE")


def test_the_incremental_script_holds_no_password_and_grants_as_the_owner():
    sql = sql_lines(OBSERVER)
    assert sql[0] == r"\set ON_ERROR_STOP on"
    assert not [line for line in sql if "PASSWORD" in line.upper()]
    # D4: the same cap as the other two roles, the rule of supabase plan 922.
    assert [line for line in sql if line.startswith("CREATE ROLE")] == ["CREATE ROLE pick_observer LOGIN NOINHERIT CONNECTION LIMIT 20;"]
    assert sql.index(SET_ROLE) < sql.index("GRANT USAGE ON SCHEMA deerflow, pick_mirror TO pick_observer;") < sql.index(RESET_ROLE)


def test_the_p0_fixture_is_what_production_ran():
    data = P0_SCRIPT.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == P0_BLOB


def test_the_runbooks_and_the_scripts_hold_no_secret():
    for path in (RUNBOOK, OBSERVER_RUNBOOK, SCRIPT, UNDO, OBSERVER, OBSERVER_UNDO, P0_SCRIPT):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{path.name}:{number}"
            for password in re.findall(r"postgres(?:ql)?://[^\s:@/]+:([^\s@]+)@", line):
                assert password.startswith("<") and password.endswith(">"), where
            assert not re.search(r"eyJ[A-Za-z0-9_-]{16,}|sb_secret_|sb_publishable_|sbp_[0-9a-f]", line), where
            assert not re.search(r"PASSWORD\s+'", line, re.I), where
            # Passwords and tokens come from openssl rand -hex 32; the only long hex allowed is the backup's sha256.
            assert "sha256" in line.lower() or not re.search(r"[0-9a-fA-F]{32,}", line), where


def test_bootstrap_grants_what_the_plan_lists(bootstrapped):
    app, reader, observer = bootstrapped.app, bootstrapped.reader, bootstrapped.observer
    assert _roles(bootstrapped) == {
        app: (True, False, 20, APP_CONFIG),
        reader: (True, False, 20, READER_CONFIG),
        observer: (True, False, 20, OBSERVER_CONFIG),
    }
    # The \dn+ view: owned by deerflow_app, nothing for PUBLIC, anon or authenticated; the reader on pick_mirror and pick_obs,
    # the observer on deerflow and pick_mirror, never pick_obs (design 3.4).
    assert _schema_acls(bootstrapped, "deerflow", "pick_mirror", "pick_obs") == {
        "deerflow": (app, sorted([f"{app}=UC/{app}", f"{observer}=U/{app}"])),
        "pick_mirror": (app, sorted([f"{app}=UC/{app}", f"{reader}=U/{app}", f"{observer}=U/{app}"])),
        "pick_obs": (app, sorted([f"{app}=UC/{app}", f"{reader}=U/{app}"])),
    }
    assert bootstrapped.usage(reader, "pick_mirror") and bootstrapped.usage(reader, "pick_obs")
    assert not bootstrapped.usage(reader, "deerflow")
    assert bootstrapped.usage(observer, "deerflow") and bootstrapped.usage(observer, "pick_mirror")
    assert not bootstrapped.usage(observer, "pick_obs")
    for role in (bootstrapped.role("anon"), bootstrapped.role("authenticated")):
        assert not any(bootstrapped.usage(role, schema) for schema in ("deerflow", "pick_mirror", "pick_obs"))
    assert [_database_privileges(bootstrapped, role) for role in (app, reader, observer)] == [(True, True), (True, False), (True, False)]
    assert _connect_entry(bootstrapped, observer)
    # The bootstrap role may become deerflow_app but does not inherit its privileges.
    membership = "SELECT bool_or(set_option), bool_or(inherit_option) FROM pg_auth_members WHERE roleid = %s::regrole AND member = %s::regrole"
    assert bootstrapped.query(membership, app, bootstrapped.owner) == [(True, False)]


def test_the_bootstrap_prints_what_the_runbook_lists(stand_in):
    result = bootstrap(stand_in)
    assert statuses(result.stdout) == expected_output(RUNBOOK, "`bootstrap.out` 的内容逐行是")


def _check_output(stand_in, runbook, marker: str) -> tuple[list[str], str]:
    """A runbook's check block, run by psql as the stand-in owner with unaligned output; (the block, what psql printed)."""
    block = expected_output(runbook, marker)
    result = stand_in.run(stand_in.renamed("\\pset format unaligned\n" + "\n".join(block) + "\n"))
    assert result.returncode == 0 and "ERROR" not in result.stdout, result.stdout
    return block, _normalized(stand_in, result.stdout)


def _assert_runbook_claims(runbook, block: list[str], output: str) -> None:
    """The runbook's own words about its check hold for the output: each rolconfig it quotes, the three role attributes,
    and which of the check's boolean columns are t."""
    text = runbook.read_text(encoding="utf-8")
    rows = {line.split("|")[0]: line.split("|") for line in output.splitlines() if line.count("|") == 4}
    configs = dict(re.findall(r"`(\w+)` 的 rolconfig 是 `(\{[^`]*\})`", text))
    assert configs and {role: rows[role][4] for role in configs} == configs
    assert all(rows[role][1:4] == ["t", "f", "20"] for role in configs)
    columns = re.findall(r"\bAS (\w+)", "\n".join(block))
    lines = output.splitlines()
    values = lines[lines.index("|".join(columns)) + 1].split("|")
    claimed = next(line for line in text.splitlines() if "为 t，其余为 f" in line)
    assert {column for column, value in zip(columns, values) if value == "t"} == set(re.findall(r"`(\w+)`", claimed))


def test_the_runbook_check_reads_what_the_runbook_says(stand_in):
    """Supabase.md 2.4, run as written on a new project: the numbers and settings the runbook quotes are the real ones."""
    bootstrap(stand_in)
    _assert_runbook_claims(RUNBOOK, *_check_output(stand_in, RUNBOOK, "以 `postgres` 身份执行，结果贴进第 12 节"))


def test_the_observer_check_reads_what_its_runbook_says(stand_in):
    """observer-role.md's record check after S2 on production (P0, then bootstrap-observer.sql)."""
    bootstrap(stand_in, P0_SCRIPT)
    bootstrap(stand_in, OBSERVER)
    _assert_runbook_claims(OBSERVER_RUNBOOK, *_check_output(stand_in, OBSERVER_RUNBOOK, "**留档检查**，以 `postgres` 身份执行"))


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


def test_the_observer_logs_in_with_its_defaults_and_creates_nothing(bootstrapped):
    import psycopg

    with bootstrapped.connect(bootstrapped.observer, autocommit=False) as conn:
        names = ("search_path", "TimeZone", "idle_in_transaction_session_timeout", "statement_timeout", "default_transaction_read_only")
        assert [_show(conn, name) for name in names] == ["deerflow", "UTC", "1min", "2min", "off"]
        conn.rollback()
        # USAGE, not CREATE: its tables come from the migrations, owned by deerflow_app.
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for schema deerflow"):
            conn.execute("CREATE TABLE deerflow.observer_owned (x int)")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for database"):
            conn.execute("CREATE SCHEMA observer_owned")
        conn.rollback()


@pytest.mark.asyncio
async def test_migrating_as_deerflow_app_gives_the_reader_three_mirror_tables(bootstrapped, tmp_path):
    """Production migrates as deerflow_app, owner of pick_mirror; 0006's grants must take effect there, not merely not fail.

    The other migration tests run as the cluster superuser, which passes every ownership check (plan 3.1, audit host-1).
    """
    import psycopg

    await migrate_as_app(bootstrapped, tmp_path)
    owners = bootstrapped.query("SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'pick_mirror'")
    assert dict(owners) == {table: bootstrapped.app for table in ("versions", "series", "series_state", "control")}
    with bootstrapped.connect(bootstrapped.reader) as conn:
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("versions", "series", "series_state")}
        assert counts == {"versions": 0, "series": 0, "series_state": 1}
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for table control"):
            conn.execute("SELECT * FROM control")


def test_the_roles_connect_where_public_cannot(stand_in):
    # All three log in on the script's own CONNECT grants, not on PUBLIC's.
    stand_in.admin(f"REVOKE CONNECT ON DATABASE {stand_in.database} FROM PUBLIC")
    bootstrap(stand_in)
    roles = (stand_in.app, stand_in.reader, stand_in.observer)
    stand_in.set_passwords(*roles)
    for role in roles:
        with stand_in.connect(role) as conn:
            assert conn.execute("select 1").fetchone() == (1,)


def test_without_the_set_grant_create_schema_authorization_fails(stand_in):
    script = stand_in.script(without=(GRANT_SET,))
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = error(result.stdout, script)
    assert statement == f"CREATE SCHEMA IF NOT EXISTS deerflow AUTHORIZATION {stand_in.app};"
    assert message == f'must be able to SET ROLE "{stand_in.app}"'
    assert stand_in.leftovers() == []


def test_without_set_role_the_first_revoke_is_denied(stand_in):
    script = stand_in.script(without=(SET_ROLE, RESET_ROLE))
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "REVOKE ALL ON SCHEMA deerflow, pick_mirror, pick_obs FROM PUBLIC;"
    assert message == "permission denied for schema deerflow"
    assert stand_in.leftovers() == []


def test_without_set_role_an_owner_reading_all_data_only_warns_and_the_reader_lacks_usage(stand_in):
    """Why the runbook reads the output for WARNING: this run exits 0 and prints GRANT, yet granted nothing."""
    stand_in.admin(f"GRANT {', '.join(READ_ALL)} TO {stand_in.owner}")
    result = stand_in.run(stand_in.script(without=(SET_ROLE, RESET_ROLE)))
    assert result.returncode == 0, result.stdout
    assert 'WARNING:  no privileges were granted for "pick_mirror"' in result.stdout
    assert not stand_in.usage(stand_in.reader, "pick_mirror")
    assert not stand_in.usage(stand_in.observer, "deerflow")


def test_a_database_the_bootstrap_role_does_not_own_stops_at_the_create_check(stand_in):
    other = stand_in.role("other")
    stand_in.admin(f"CREATE ROLE {other} NOLOGIN", f"ALTER DATABASE {stand_in.database} OWNER TO {other}")
    script = stand_in.script()
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    # The GRANT ON DATABASE only warned; the DO block turns that into a stop.
    assert f'WARNING:  no privileges were granted for "{stand_in.database}"' in result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "END $$;"
    assert message.startswith(f"{stand_in.app} 没有拿到 {stand_in.database} 库的 CREATE 权限")
    assert stand_in.leftovers() == []


def test_another_database_is_refused_before_anything_is_created(stand_in):
    elsewhere = f"{stand_in.database}_elsewhere"
    stand_in.admin(f"CREATE DATABASE {elsewhere} OWNER {stand_in.owner}")
    for path in (SCRIPT, OBSERVER):
        script = stand_in.script(path)
        result = stand_in.run(script, database=elsewhere)
        assert result.returncode == STOPPED, result.stdout
        _, message = error(result.stdout, script)
        assert message.startswith(f"当前连接的是 {elsewhere} 库"), path.name
        assert stand_in.leftovers() == []


def test_the_undo_script_leaves_the_project_as_before_and_bootstrap_runs_again(bootstrapped):
    result = bootstrapped.run(bootstrapped.script(UNDO))
    assert result.returncode == 0, result.stdout
    assert "WARNING" not in result.stdout, result.stdout
    # The roles are gone, so no grant on the database can name them either.
    assert bootstrapped.leftovers() == []
    bootstrap(bootstrapped)


def test_the_undo_script_refuses_once_the_host_has_created_tables(bootstrapped):
    with bootstrapped.connect(bootstrapped.app) as conn:
        conn.execute("CREATE TABLE deerflow.users (id int)")
    script = bootstrapped.script(UNDO)
    result = bootstrapped.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "DROP SCHEMA deerflow, pick_mirror, pick_obs;"
    assert "because other objects depend on" in message
    everything = [bootstrapped.app, bootstrapped.reader, bootstrapped.observer, "deerflow", "pick_mirror", "pick_obs"]
    assert bootstrapped.leftovers() == sorted(everything)
    with bootstrapped.connect(bootstrapped.app) as conn:
        assert conn.execute("SELECT count(*) FROM deerflow.users").fetchone() == (0,)


# ---------------------------------------------------------------- production: the P0 bootstrap, then bootstrap-observer.sql (S2)


def _published_version(stand_in, number: int) -> str:
    """A version the mirror published before the observer existed: its schema, an rs_ids row, the reader's grants."""
    schema = f"pickm_v{number:06d}"
    with stand_in.connect(stand_in.app) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"CREATE TABLE {schema}.rs_ids (id text NOT NULL, canonical_id text)")
        conn.execute(f"CREATE TABLE {schema}.meta (key text PRIMARY KEY, value jsonb NOT NULL)")
        conn.execute(f"INSERT INTO {schema}.rs_ids VALUES ('rs-{number}', NULL)")
        conn.execute(
            "INSERT INTO pick_mirror.versions (id, schema_name, status, as_of, fingerprint, created_at, published_at)"
            " VALUES (%s, %s, 'published', date_trunc('minute', now(), 'UTC'), repeat('0', 64), now(), now())",
            (number, schema),
        )
        conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {stand_in.reader}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {stand_in.reader}")
    return schema


async def _production_at_0006(stand_in, workdir) -> str:
    """Production before S2: the P0 bootstrap, the chain up to 0006 run as deerflow_app, host tables, a published version."""
    bootstrap(stand_in, P0_SCRIPT)
    stand_in.set_passwords(stand_in.app, stand_in.reader)
    await migrate_as_app(stand_in, workdir, "0006")
    host_tables(stand_in)
    return _published_version(stand_in, 1)


@pytest.mark.asyncio
async def test_observer_bootstrap_incremental(stand_in, owner_kind, tmp_path):
    schema = await _production_at_0006(stand_in, tmp_path)
    before = _schema_acls(stand_in, "deerflow", "pick_mirror", schema)
    result = bootstrap(stand_in, OBSERVER)
    assert statuses(result.stdout) == expected_output(OBSERVER_RUNBOOK, "`bootstrap-observer.out` 的内容逐行是")
    observer = stand_in.observer
    assert _roles(stand_in)[observer] == (True, False, 20, OBSERVER_CONFIG)
    assert _connect_entry(stand_in, observer) and _database_privileges(stand_in, observer) == (True, False)
    after = _schema_acls(stand_in, "deerflow", "pick_mirror", schema)
    # Only the two USAGE lines are new; the version schema waits for regrant (D15), pick_obs for 0007.
    assert after == {
        name: (owner, sorted([*acl, f"{observer}=U/{stand_in.app}"]) if name in ("deerflow", "pick_mirror") else acl) for name, (owner, acl) in before.items()
    }
    assert not stand_in.query("SELECT has_table_privilege(%s, 'deerflow.ggwp_import_batches', 'SELECT')", observer)[0][0]
    assert stand_in.query("SELECT count(*) FROM pg_namespace WHERE nspname = 'pick_obs'") == [(0,)]


def test_the_incremental_script_needs_the_p0_bootstrap(stand_in):
    script = stand_in.script(OBSERVER)
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "END $$;"
    assert message.startswith(f"没有找到 {stand_in.app} 或 deerflow、pick_mirror 两个 schema")
    assert stand_in.leftovers() == []


def test_the_incremental_script_runs_once(stand_in):
    bootstrap(stand_in, P0_SCRIPT)
    bootstrap(stand_in, OBSERVER)
    before = _roles(stand_in), _schema_acls(stand_in, "deerflow", "pick_mirror")
    script = stand_in.script(OBSERVER)
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "CREATE ROLE pick_observer LOGIN NOINHERIT CONNECTION LIMIT 20;".replace("pick_observer", stand_in.observer)
    assert message == f'role "{stand_in.observer}" already exists'
    assert (_roles(stand_in), _schema_acls(stand_in, "deerflow", "pick_mirror")) == before


def test_without_set_role_the_observer_check_stops_a_grant_that_only_warned(stand_in):
    """The owner reading all data turns the missing SET ROLE into a WARNING; the script's own check makes it a stop."""
    stand_in.admin(f"GRANT {', '.join(READ_ALL)} TO {stand_in.owner}")
    bootstrap(stand_in, P0_SCRIPT)
    script = stand_in.script(OBSERVER, without=(SET_ROLE, RESET_ROLE))
    result = stand_in.run(script)
    assert result.returncode == STOPPED, result.stdout
    assert 'WARNING:  no privileges were granted for "deerflow"' in result.stdout
    statement, message = error(result.stdout, script)
    assert statement == "END $$;"
    assert message.startswith(f"{stand_in.observer} 的库级 CONNECT 或两个 schema 的 USAGE 没有授上")
    assert stand_in.leftovers() == sorted([stand_in.app, stand_in.reader, "deerflow", "pick_mirror"])


def _normalized(stand_in, text: str) -> str:
    return text.replace(f"_{stand_in.suffix}", "")


def _snapshot(stand_in) -> dict:
    """Every role attribute and grant the scripts and migrations leave behind, with the per-test suffix taken off."""
    roles = {_normalized(stand_in, name): (*attributes[:3], sorted(attributes[3])) for name, attributes in _roles(stand_in).items()}
    schemas = {name: (owner, acl) for name, (owner, acl) in _schema_acls(stand_in, "deerflow", "pick_mirror", "pick_obs").items()}
    relations = stand_in.query(
        "SELECT n.nspname || '.' || c.relname, c.relacl::text[] FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname IN ('deerflow', 'pick_mirror', 'pick_obs') AND c.relacl IS NOT NULL"
    )
    columns = stand_in.query(
        "SELECT c.relname || '.' || a.attname, a.attacl::text[] FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'deerflow' AND a.attacl IS NOT NULL"
    )
    database = stand_in.query("SELECT datacl::text[] FROM pg_database WHERE datname = %s", stand_in.database)[0][0]
    grants = {**{name: sorted(acl) for name, acl in relations + columns}, **{f"schema {name}": value for name, value in schemas.items()}}
    return _normalized(stand_in, repr({"roles": roles, "grants": sorted(grants.items()), "database": sorted(database)}))


@pytest.mark.asyncio
async def test_the_incremental_path_ends_where_a_new_project_starts(pg_cluster, tmp_path, monkeypatch):
    """D3: production (P0, 0001-0006, bootstrap-observer.sql, then 0007) and a new project (bootstrap.sql, then the whole
    chain) end with the same roles and the same grants on every schema, table, column, sequence and the database."""
    for name in ("new", "production"):
        (tmp_path / name).mkdir()
    with stand_in_for(pg_cluster, tmp_path / "new", monkeypatch) as fresh:
        bootstrap(fresh)
        await migrate_as_app(fresh, tmp_path / "new")
        expected = _snapshot(fresh)
    with stand_in_for(pg_cluster, tmp_path / "production", monkeypatch) as production:
        bootstrap(production, P0_SCRIPT)
        await migrate_as_app(production, tmp_path / "production", "0006")
        bootstrap(production, OBSERVER)
        await migrate_as_app(production, tmp_path / "production")
        assert _snapshot(production) == expected


# ---------------------------------------------------------------- undoing bootstrap-observer.sql


def _mentions(stand_in, oid: int) -> list[str]:
    """Every catalog ACL in the database still naming the role's oid."""
    named = "(SELECT 1 FROM aclexplode({}) x WHERE x.grantee = %(oid)s::oid)"
    relation = "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    sql = (
        f"SELECT 'relation ' || n.nspname || '.' || c.relname {relation} WHERE EXISTS {named.format('c.relacl')}"
        f" UNION ALL SELECT 'column ' || n.nspname || '.' || c.relname || '.' || a.attname {relation}"
        f"  JOIN pg_attribute a ON a.attrelid = c.oid WHERE EXISTS {named.format('a.attacl')}"
        f" UNION ALL SELECT 'schema ' || n.nspname FROM pg_namespace n WHERE EXISTS {named.format('n.nspacl')}"
        f" UNION ALL SELECT 'database ' || d.datname FROM pg_database d WHERE EXISTS {named.format('d.datacl')}"
    )
    with stand_in.connect() as conn:
        return sorted(name for (name,) in conn.execute(sql, {"oid": oid}).fetchall())


@pytest.mark.asyncio
async def test_observer_undo_rerun(stand_in, owner_kind, tmp_path):
    """After S2, S3 and a regrant, the undo takes back every grant and the role; bootstrap-observer.sql then runs again."""
    from ggwork_pick.observe.admin.cmd_regrant import regrant

    schema = await _production_at_0006(stand_in, tmp_path)
    bootstrap(stand_in, OBSERVER)
    await migrate_as_app(stand_in, tmp_path)
    assert await regrant(stand_in.url_as(stand_in.app, driver="postgresql"), stand_in.observer) == 0
    oid = stand_in.query("SELECT %s::regrole::oid", stand_in.observer)[0][0]
    granted = _mentions(stand_in, oid)
    # Tables, their sequences, four columns, three schemas, the version's rs_ids and the database: all of it goes.
    assert {f"schema {schema}", f"relation {schema}.rs_ids", "relation deerflow.ggwp_obs_sets", "column deerflow.ggwp_candidate_sets.gsc_set_id"} <= set(
        granted
    )
    assert f"database {stand_in.database}" in granted
    result = stand_in.run(stand_in.script(OBSERVER_UNDO))
    assert result.returncode == 0 and "WARNING" not in result.stdout, result.stdout
    assert statuses(result.stdout) == expected_output(OBSERVER_RUNBOOK, "`bootstrap-observer-undo.out` 的内容逐行是")
    assert _mentions(stand_in, oid) == []
    assert stand_in.leftovers() == sorted([stand_in.app, stand_in.reader, "deerflow", "pick_mirror", "pick_obs"])
    # The reader and the tables are untouched.
    assert stand_in.query("SELECT has_table_privilege(%s, %s, 'SELECT')", stand_in.reader, f"{schema}.rs_ids") == [(True,)]
    bootstrap(stand_in, OBSERVER)
    # A new role: only the bootstrap's USAGE lines again, the table grants wait for regrant (runbook).
    assert stand_in.query("SELECT has_table_privilege(%s, 'deerflow.ggwp_obs_sets', 'SELECT')", stand_in.observer) == [(False,)]
    assert stand_in.usage(stand_in.observer, "deerflow")


def test_the_observer_undo_refuses_another_database(stand_in):
    elsewhere = f"{stand_in.database}_elsewhere"
    stand_in.admin(f"CREATE DATABASE {elsewhere} OWNER {stand_in.owner}")
    bootstrap(stand_in, P0_SCRIPT)
    bootstrap(stand_in, OBSERVER)
    script = stand_in.script(OBSERVER_UNDO)
    result = stand_in.run(script, database=elsewhere)
    assert result.returncode == STOPPED, result.stdout
    _, message = error(result.stdout, script)
    assert message.startswith(f"当前连接的是 {elsewhere} 库")
    assert stand_in.observer in stand_in.leftovers()


def test_the_scripts_name_the_observer_role_the_migrations_default_to():
    """0007, the mirror publish and regrant fall back to pick_observer when PICK_OBS_OBSERVER_ROLE is unset."""
    from ggwork_pick.mirror.publish import DEFAULT_OBSERVER_ROLE

    assert DEFAULT_OBSERVER_ROLE == "pick_observer"
    for path in (SCRIPT, OBSERVER, OBSERVER_UNDO):
        assert "pick_observer" in path.read_text(encoding="utf-8"), path.name
