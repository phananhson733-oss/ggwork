"""TR-34: the deploy guard over real git repositories and a real PostgreSQL connection (plan D41).

test_deploy_guard.py pins the rules with fakes; here GitRepo and read_prod_state, the two adapters those fakes stand in
for, answer the same questions from a throwaway repository pushed to a local bare "ggwork" remote, and from the
PICK_TEST_PG_URL cluster. Nothing leaves the machine: the remote is a directory, and the one connection that fails
goes to 127.0.0.1 port 1. Git runs without the user's configuration (no signing, no hooks, no credential helpers).
"""

import io
import os
import subprocess

import pg
import pytest
from deploy_guard_fakes import PASSWORD, FakeDb, FakeRepo, dsn_environment, dsn_file, load_guard, make_root, record_line, run_guard

IDENTITY = {"GIT_AUTHOR_NAME": "guard-test", "GIT_AUTHOR_EMAIL": "guard@example.invalid"}


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


@pytest.fixture
def checkout(tmp_path, git_env):
    """A clean clone whose HEAD is the main of its "ggwork" remote, a bare repository next to it."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
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
    (checkout / "frontend" / ".env.local").write_text(f"SECRET={PASSWORD}\n")  # ignored, so status stays clean
    code, out, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)
    assert code == 2 and "frontend/.env.local" in err and PASSWORD not in out + err
    (checkout / "frontend" / ".env.local").unlink()
    (checkout / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (checkout / "frontend" / "node_modules" / "pkg" / ".env").write_text("THIRD_PARTY=1\n")  # a dependency's own file
    assert run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=env)[0] == 0


def test_behind_the_fetched_main_refused(guard, checkout, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", "--origin", "ggwork", str(tmp_path / "origin.git"), str(other))
    (other / "NEWS").write_text("moved on\n")
    commit_all(other, "moved on")
    git(other, "push", "--quiet", "ggwork", "main")
    code, _, err = run_guard(["gateway", "--first-record"], guard.GitRepo.open(checkout), env=dsn_environment(tmp_path))
    assert code == 2 and "ggwork/main" in err
    assert git(checkout, "rev-parse", "refs/remotes/ggwork/main") == git(other, "rev-parse", "HEAD")  # it did fetch


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
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=guard.read_prod_state, env=env)
    assert code == 1 and out == "" and "OperationalError" in err
    assert PASSWORD not in err and "127.0.0.1" not in err


@pytest.fixture
def observer_database(pg_cluster):
    """A database holding deerflow.ggwp_alembic_version at 0007 and a LOGIN role that may read only that table."""
    import psycopg
    from psycopg import sql

    database, role, password = pg.unique_name("pick_guard"), pg.unique_name("pick_observer"), "guard-test-pw"
    pg_cluster.create_database(database)
    admin = pg_cluster.url.set(drivername="postgresql", database=database).render_as_string(hide_password=False)
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            conn.execute(f"CREATE TABLE {pg.SCHEMA}.ggwp_alembic_version (version_num varchar(32) PRIMARY KEY)")
            conn.execute(f"INSERT INTO {pg.SCHEMA}.ggwp_alembic_version VALUES ('0007')")
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(pg.SCHEMA), sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT SELECT ON {}.ggwp_alembic_version TO {}").format(sql.Identifier(pg.SCHEMA), sql.Identifier(role)))
        url = pg_cluster.url.set(drivername="postgresql+asyncpg", username=role, password=password, database=database)
        yield url.render_as_string(hide_password=False), role
    finally:
        pg_cluster.drop_database(database)
        pg_cluster.drop_role(role)


def test_reads_the_head_as_the_observer(guard, observer_database, tmp_path):
    url, role = observer_database
    env = {"PICK_OBS_DSN_FILE": dsn_file(tmp_path, url), "PGSSLMODE": "disable", "PICK_OBS_OBSERVER_ROLE": role}
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=guard.read_prod_state, env=env)
    assert (code, err) == (0, "") and "prod_head=0007 chain_head=0007" in out
    state = guard.read_prod_state(url.replace("postgresql+asyncpg://", "postgresql://"), sslmode="disable")
    assert state == guard.ProdState(role=role, versions=("0007",))


def test_reads_in_a_read_only_transaction(guard, observer_database, monkeypatch):
    """The guard's one query runs READ ONLY: a write slipped into it would be refused by the server."""
    import psycopg

    url, _ = observer_database
    seen = []
    real_connect = psycopg.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        seen.append(conn)
        return conn

    monkeypatch.setattr(psycopg, "connect", connect)
    guard.read_prod_state(url.replace("postgresql+asyncpg://", "postgresql://"), sslmode="disable")
    assert len(seen) == 1 and seen[0].read_only is True and seen[0].closed


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
    assert code == 1 and "git fetch" in err_text and "fatal" not in err_text
