"""TR-34: the deploy guard over real git repositories and a real PostgreSQL connection (plan D41).

test_deploy_guard.py pins the rules with fakes; here GitRepo and read_prod_state, the two adapters those fakes stand in
for, answer the same questions from a throwaway repository pushed to a local bare remote laid out like the shared one
(.../phananhson733-oss/ggwork.git), and from the PICK_TEST_PG_URL cluster. Nothing leaves the machine: the remotes are
directories, and the one connection that fails goes to 127.0.0.1 port 1. Git runs without the user's configuration (no
signing, no hooks, no credential helpers). The cluster has no TLS, so these runs allow PGSSLMODE=disable through main();
the command line cannot.

The synthetic observer (observer_database) holds only what the guard needs. The S3 seam with TR-12, production at the
revision before MIN_MIGRATION_HEAD and the observer made by the real bootstrap-observer.sql, is its own test.
"""

import asyncio
import io
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

import pg
import pytest
from deploy_guard_fakes import (
    CHAIN_HEAD,
    PASSWORD,
    ROOT,
    SOURCE_OBSERVE_VERSIONS,
    SOURCE_VERSIONS,
    FakeDb,
    FakeRepo,
    dsn_environment,
    dsn_file,
    load_guard,
    make_real_root,
    make_root,
    record_line,
    run_guard,
)

IDENTITY = {"GIT_AUTHOR_NAME": "guard-test", "GIT_AUTHOR_EMAIL": "guard@example.invalid"}
LOCAL = frozenset({"disable"})
OBSERVER_SQL = ROOT / "docs/pick-workbench/supabase/bootstrap-observer.sql"


@pytest.fixture(scope="module")
def guard():
    return load_guard()


def git(cwd, *args) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for name, value in IDENTITY.items():
        monkeypatch.setenv(name, value)
        monkeypatch.setenv(name.replace("AUTHOR", "COMMITTER"), value)


def bare(tmp_path, owner: str, name: str):
    path = tmp_path / owner / f"{name}.git"
    path.parent.mkdir(exist_ok=True)
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(path))
    return path


@pytest.fixture
def checkout(tmp_path, git_env):
    """A clean clone whose HEAD is the main of its "ggwork" remote, a bare repository next to it."""
    origin = bare(tmp_path, "phananhson733-oss", "ggwork")
    work = make_root(tmp_path)
    (work / "frontend").mkdir()
    (work / "frontend" / "package.json").write_text('{"name": "frontend"}\n')
    (work / "frontend" / ".env.example").write_text("PICK_EXAMPLE=1\n")
    (work / ".gitignore").write_text(".env*\n!.env.example\nnode_modules/\n")
    git(work, "init", "--quiet", "-b", "main")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "base")
    git(work, "remote", "add", "ggwork", str(origin))
    git(work, "push", "--quiet", "ggwork", "main")
    return work


def commit_all(work, message: str) -> str:
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", message)
    return git(work, "rev-parse", "HEAD")


def test_clean_main_passes(guard, checkout, tmp_path):
    code, out, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout / "docs"), env=dsn_environment(tmp_path))
    assert (code, err) == (0, "")
    assert f"commit={git(checkout, 'rev-parse', 'HEAD')}" in out and str(checkout.resolve()) in out


def test_dirty_and_env_files_refused(guard, checkout, tmp_path):
    env = dsn_environment(tmp_path)
    (checkout / "frontend" / "package.json").write_text("{}\n")
    assert run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)[0] == 2
    git(checkout, "checkout", "--", "frontend/package.json")
    (checkout / "frontend" / "notes.txt").write_text("an untracked file nobody ignores\n")
    code, _, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)
    assert code == 2 and "frontend/notes.txt" in err
    (checkout / "frontend" / "notes.txt").unlink()
    (checkout / "frontend" / ".env.local").write_text(f"SECRET={PASSWORD}\n")  # ignored, so status stays clean
    code, out, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)
    assert code == 2 and "frontend/.env.local" in err and PASSWORD not in out + err
    (checkout / "frontend" / ".env.local").unlink()
    (checkout / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (checkout / "frontend" / "node_modules" / "pkg" / ".env").write_text("THIRD_PARTY=1\n")  # a dependency's own file
    assert run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)[0] == 0


def test_behind_the_fetched_main_refused(guard, checkout, tmp_path):
    other, origin = tmp_path / "other", tmp_path / "phananhson733-oss" / "ggwork.git"
    git(tmp_path, "clone", "--quiet", "--origin", "ggwork", str(origin), str(other))
    (other / "NEWS").write_text("moved on\n")
    commit_all(other, "moved on")
    git(other, "push", "--quiet", "ggwork", "main")
    code, _, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=dsn_environment(tmp_path))
    assert code == 2 and "ggwork/main" in err
    assert git(checkout, "rev-parse", "refs/remotes/ggwork/main") == git(other, "rev-parse", "HEAD")  # it did fetch


def test_a_fork_remote_is_refused_before_it_is_fetched(guard, checkout, tmp_path):
    """A clone with a personal fork as a remote: --remote fork holds the same main, and is still refused."""
    fork = bare(tmp_path, "someone", "ggwork")
    git(checkout, "remote", "add", "fork", str(fork))
    git(checkout, "push", "--quiet", str(fork), "main")  # by path: no remote-tracking ref yet
    code, out, err = run_guard(["gateway", "--first-record", "--remote", "fork"], guard.GitRepo.open(checkout), env=dsn_environment(tmp_path))
    assert code == 2 and out == "" and "phananhson733-oss/ggwork" in err and str(fork) not in err
    assert subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet", "refs/remotes/fork/main"]).returncode != 0


def test_record_off_main_refused(guard, checkout, tmp_path):
    base = git(checkout, "rev-parse", "HEAD")
    git(checkout, "checkout", "--quiet", "-b", "side")
    (checkout / "SIDE").write_text("deployed from a branch\n")
    side = commit_all(checkout, "side")
    git(checkout, "checkout", "--quiet", "main")
    progress = checkout / "docs" / "pick-workbench" / "progress.md"
    progress.write_text(progress.read_text() + record_line("gateway", side) + "\n")
    commit_all(checkout, "record a deploy from the side branch")
    git(checkout, "push", "--quiet", "ggwork", "main")
    env = dsn_environment(tmp_path)
    code, _, err = run_guard(["gateway"], guard.GitRepo.open(checkout), env=env)
    assert code == 2 and side[:12] in err and "祖先" in err
    progress.write_text(progress.read_text() + record_line("gateway", base, "2026-09-26T00:00:00Z") + "\n")
    commit_all(checkout, "a later deploy from main")
    git(checkout, "push", "--quiet", "ggwork", "main")
    assert run_guard(["gateway"], guard.GitRepo.open(checkout), env=env)[0] == 0


def test_frontend_export_runs_git_only(guard, checkout, tmp_path, monkeypatch):
    """The frontend mode exports tracked files and prints the Vercel step; it never runs vercel or railway itself."""
    commands, real_run, real_popen = [], subprocess.run, subprocess.Popen

    def run(args, *rest, **kwargs):
        commands.append(list(args))
        return real_run(args, *rest, **kwargs)

    def popen(args, *rest, **kwargs):
        commands.append(list(args))
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    out_dir = tmp_path / "export"
    code, out, err = run_guard(["frontend", "--first-record", "--out", str(out_dir)], guard.GitRepo.open(checkout))
    assert (code, err) == (0, ""), err
    exported = sorted(str(path.relative_to(out_dir)) for path in out_dir.rglob("*") if path.is_file())
    assert exported == ["frontend/.env.example", "frontend/package.json"]
    assert commands and all(command[0] == "git" for command in commands)
    assert "vercel deploy --prod" in out


def test_real_reader_error_is_redacted(guard, tmp_path):
    env = {"PICK_OBS_DSN_FILE": dsn_file(tmp_path, f"postgresql://pick_observer:{PASSWORD}@127.0.0.1:1/postgres"), "PGSSLMODE": "disable"}
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=guard.read_prod_state, env=env, ssl_modes=LOCAL)
    assert code == 1 and out == "" and "OperationalError" in err
    assert PASSWORD not in err and "127.0.0.1" not in err


@dataclass(frozen=True)
class ObserverDb:
    admin: str  # the cluster superuser, libpq URL
    url: str  # the observer, SQLAlchemy URL as the DSN file holds it
    role: str

    @property
    def libpq(self) -> str:
        return self.url.replace("postgresql+asyncpg://", "postgresql://")

    def env(self, directory) -> dict[str, str]:
        return {"PICK_OBS_DSN_FILE": dsn_file(directory, self.url), "PGSSLMODE": "disable", "PICK_OBS_OBSERVER_ROLE": self.role}


@contextmanager
def _observer_database(pg_cluster, *, select: bool, observe_tables: bool):
    """A database holding deerflow.ggwp_alembic_version at CHAIN_HEAD and a LOGIN role with the schema's USAGE, and the
    table's SELECT when `select`. observe_tables adds a ggwp_obs_* table, as migration MIN_MIGRATION_HEAD would."""
    import psycopg
    from psycopg import sql

    database, role, password = pg.unique_name("pick_guard"), pg.unique_name("pick_observer"), "guard-test-pw"
    pg_cluster.create_database(database)
    admin = pg_cluster.url.set(drivername="postgresql", database=database).render_as_string(hide_password=False)
    schema, who = sql.Identifier(pg.SCHEMA), sql.Identifier(role)
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(who, sql.Literal(password)))
            conn.execute(sql.SQL("CREATE TABLE {}.ggwp_alembic_version (version_num varchar(32) PRIMARY KEY)").format(schema))
            conn.execute(sql.SQL("INSERT INTO {}.ggwp_alembic_version VALUES ({})").format(schema, sql.Literal(CHAIN_HEAD)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, who))
            if select:
                conn.execute(sql.SQL("GRANT SELECT ON {}.ggwp_alembic_version TO {}").format(schema, who))
            if observe_tables:
                conn.execute(sql.SQL("CREATE TABLE {}.ggwp_obs_runs (id int PRIMARY KEY)").format(schema))
        url = pg_cluster.url.set(drivername="postgresql+asyncpg", username=role, password=password, database=database)
        yield ObserverDb(admin=admin, url=url.render_as_string(hide_password=False), role=role)
    finally:
        pg_cluster.drop_database(database)
        pg_cluster.drop_role(role)


@pytest.fixture
def observer_database(pg_cluster):
    """The observer as the guard needs it: the version table's SELECT granted (bootstrap-observer.sql's job at S3)."""
    with _observer_database(pg_cluster, select=True, observe_tables=True) as db:
        yield db


def test_reads_the_head_as_the_observer(guard, observer_database, tmp_path):
    db = observer_database
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=guard.read_prod_state, env=db.env(tmp_path), ssl_modes=LOCAL)
    assert (code, err) == (0, "") and f"prod_head={CHAIN_HEAD} chain_head={CHAIN_HEAD}" in out
    state = guard.read_prod_state(db.libpq, sslmode="disable")
    assert state == guard.ProdState(role=db.role, versions=(CHAIN_HEAD,), observe_tables=True)


def test_connects_with_the_given_sslmode_and_its_own_name(guard, observer_database, monkeypatch):
    import psycopg

    seen, real_connect = [], psycopg.connect

    def connect(*args, **kwargs):
        seen.append(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.setattr(psycopg, "connect", connect)
    guard.read_prod_state(observer_database.libpq, sslmode="disable")
    assert [(kwargs.get("sslmode"), kwargs.get("application_name"), kwargs.get("connect_timeout")) for kwargs in seen] == [("disable", "ggwp-deploy-guard", 15)]


def test_reads_in_a_read_only_transaction(guard, observer_database, monkeypatch):
    """The guard's queries run READ ONLY: a write slipped into them is refused by the server (SQLSTATE 25006)."""
    import psycopg

    monkeypatch.setattr(guard.readers, "VERSION_QUERY", "INSERT INTO deerflow.ggwp_alembic_version VALUES ('x') RETURNING version_num")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        guard.read_prod_state(observer_database.libpq, sslmode="disable")


def test_waits_no_longer_than_its_statement_timeout(guard, observer_database, monkeypatch):
    """A gateway migrating right now holds the version table: the read gives up after STATEMENT_TIMEOUT (57014)."""
    import psycopg

    monkeypatch.setattr(guard.readers, "STATEMENT_TIMEOUT", "300ms")
    with psycopg.connect(observer_database.admin) as migrating:
        migrating.execute("LOCK TABLE deerflow.ggwp_alembic_version IN ACCESS EXCLUSIVE MODE")
        release = threading.Timer(5, migrating.rollback)  # without the timeout the read would wait this long
        release.start()
        started = time.monotonic()
        try:
            with pytest.raises(psycopg.errors.QueryCanceled):
                guard.read_prod_state(observer_database.libpq, sslmode="disable")
        finally:
            release.cancel()
        assert time.monotonic() - started < 4


@pytest.mark.parametrize(
    ("observe_tables", "shown", "hidden"), [(False, "bootstrap-observer.sql", "admin regrant"), (True, "admin regrant", "bootstrap-observer.sql")]
)
def test_an_observer_without_the_select_gets_the_fix_for_the_stage(guard, pg_cluster, tmp_path, observe_tables, shown, hidden):
    """No SELECT on the version table: the probe tells production before MIN_MIGRATION_HEAD (fix bootstrap-observer.sql,
    TR-12's seam) from production after it (regrant), without a permission error and without reading the table."""
    with _observer_database(pg_cluster, select=False, observe_tables=observe_tables) as db:
        state = guard.read_prod_state(db.libpq, sslmode="disable")
        assert state == guard.ProdState(role=db.role, versions=None, observe_tables=observe_tables)
        code, out, err = run_guard(
            ["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=guard.read_prod_state, env=db.env(tmp_path), ssl_modes=LOCAL
        )
        assert code == 1 and out == "" and shown in err and hidden not in err


def test_s3_reads_production_as_the_bootstrapped_observer(guard, pg_cluster, tmp_path, monkeypatch):
    """S2 then S3 (plan section 10), the TR-12 seam: production at the revision before MIN_MIGRATION_HEAD, pick_observer
    made by the real bootstrap-observer.sql and nothing else. The guard must read the head then, or the first gateway
    deploy with MIN_MIGRATION_HEAD can never pass it: regrant refuses before that migration, and the old gateway has none.
    """
    if not OBSERVER_SQL.is_file():
        pytest.skip("TR-12 的 bootstrap-observer.sql 合入之前跳过；合入后这条必须通过：它须授 deerflow.ggwp_alembic_version 的 SELECT")
    import supabase_stand_in as supabase

    chain = guard.read_chain(ROOT / SOURCE_VERSIONS)
    before = chain[chain.index(guard.read_constant(ROOT / SOURCE_OBSERVE_VERSIONS, "MIN_MIGRATION_HEAD")) - 1]
    with supabase.stand_in_for(pg_cluster, tmp_path, monkeypatch) as stand_in:
        supabase.bootstrap(stand_in, supabase.P0_SCRIPT)
        stand_in.set_passwords(stand_in.app)
        asyncio.run(supabase.migrate_as_app(stand_in, tmp_path, before))
        supabase.bootstrap(stand_in, supabase.OBSERVER)
        stand_in.set_passwords(stand_in.observer)
        url = stand_in.url_as(stand_in.observer)
        env = {"PICK_OBS_DSN_FILE": dsn_file(tmp_path, url), "PGSSLMODE": "disable", "PICK_OBS_OBSERVER_ROLE": stand_in.observer}
        repo = FakeRepo(make_real_root(tmp_path))
        code, out, err = run_guard(["gateway", "--first-record"], repo, db=guard.read_prod_state, env=env, ssl_modes=LOCAL)
    assert (code, err) == (0, ""), err
    assert f"prod_head={before} chain_head={chain[-1]}" in out


def test_fake_db_matches_the_reader_signature(guard):
    """FakeDb stands in for read_prod_state: same call shape (dsn, *, sslmode), same answer type."""
    import inspect

    assert list(inspect.signature(guard.read_prod_state).parameters) == ["dsn", "sslmode"]
    assert isinstance(FakeDb()("postgresql://x", sslmode="require"), guard.ProdState)


def test_git_failures_are_errors_without_git_output(guard, checkout, tmp_path):
    """Not a checkout, or a remote that is not there: exit 1, naming the git command and never its stderr."""
    (tmp_path / "plain").mkdir()
    out, err = io.StringIO(), io.StringIO()
    assert guard.main(["gateway", "--repo", str(tmp_path / "plain")], read_prod=FakeDb(), environ={}, out=out, err=err) == 1
    assert "git rev-parse" in err.getvalue() and "fatal" not in err.getvalue()
    code, _, err_text = run_guard(["gateway", "--first-record", "--remote", "nowhere"], guard.GitRepo.open(checkout), env=dsn_environment(tmp_path))
    assert code == 1 and "git remote" in err_text and "fatal" not in err_text and "error" not in err_text
