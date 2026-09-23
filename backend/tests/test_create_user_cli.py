"""``python -m app.gateway.auth.create_user``: the only way to add accounts once self-registration is off.

It runs inside ``railway ssh`` sessions, where none of the DEER_FLOW_* variables that pick_entrypoint sets
for the gateway exist and the working directory holds no config.yaml, so the subprocess cases reproduce
that shell. The PostgreSQL case needs PICK_TEST_PG_URL (a throwaway cluster) and skips without it; with it
set, a missing psycopg fails the case rather than skipping it (tests/support/pg.py).
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from support import pg as support_pg

BACKEND = Path(__file__).resolve().parents[1]
TEMPLATE = BACKEND.parent / "config.pick.example.yaml"
PASSWORD_LINE = re.compile(r"^password: (\S+)$", re.MULTILINE)


@pytest.fixture
def environ():
    """A process environment without DEER_FLOW_* or PICK_* variables, restored afterwards."""
    saved = dict(os.environ)
    for name in list(os.environ):
        if name.startswith(("DEER_FLOW_", "PICK_")):
            del os.environ[name]
    yield os.environ
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def home(tmp_path):
    """A volume on which the gateway has started once in sqlite mode."""
    from app.gateway.pick_entrypoint import prepare_config

    path = tmp_path / "volume"
    prepare_config(path, TEMPLATE, backend="sqlite")
    return path


def users(home: Path) -> list[tuple]:
    with sqlite3.connect(home / "data" / "deerflow.db") as conn:
        return conn.execute("SELECT email, system_role, needs_setup, password_hash FROM users ORDER BY email").fetchall()


def written_password(path: Path) -> str:
    match = PASSWORD_LINE.search(path.read_text())
    assert match is not None
    return match.group(1)


def run_cli(*args: str) -> int:
    from app.gateway.auth.create_user import main

    return main(list(args))


def test_new_email_gets_a_user_account_that_must_finish_setup(environ, home, capsys):
    from app.gateway.auth.password import verify_password

    environ["DEER_FLOW_HOME"] = str(home)
    assert run_cli("--email", "New.Member@Example.com") == 0
    out = capsys.readouterr()
    [(email, role, needs_setup, password_hash)] = users(home)
    assert (email, role, needs_setup) == ("new.member@example.com", "user", 1)
    credentials = home / "credentials" / "new.member@example.com.txt"
    assert (credentials.stat().st_mode & 0o777) == 0o600
    assert (credentials.parent.stat().st_mode & 0o777) == 0o700
    password = written_password(credentials)
    assert verify_password(password, password_hash)
    assert password not in out.out
    assert password not in out.err
    assert str(credentials) in out.out


def test_admin_role_on_request(environ, home):
    environ["DEER_FLOW_HOME"] = str(home)
    assert run_cli("--email", "boss@example.com", "--role", "admin") == 0
    assert [row[:3] for row in users(home)] == [("boss@example.com", "admin", 1)]


def test_duplicate_email_exits_nonzero_and_keeps_the_first_password(environ, home, capsys):
    from app.gateway.auth.password import verify_password

    environ["DEER_FLOW_HOME"] = str(home)
    assert run_cli("--email", "dup@example.com") == 0
    credentials = home / "credentials" / "dup@example.com.txt"
    first = credentials.read_text()
    capsys.readouterr()
    assert run_cli("--email", "DUP@example.com") != 0
    assert "already" in capsys.readouterr().err
    assert credentials.read_text() == first
    [(_, _, _, password_hash)] = users(home)
    assert verify_password(written_password(credentials), password_hash)


def test_the_password_file_is_in_place_once_the_account_exists_even_if_closing_fails(environ, home, monkeypatch):
    # Rerunning only says "already exists", so a created account whose password stayed in the hidden staging file
    # would need someone to know to look for it.
    from app.gateway.auth.password import verify_password
    from deerflow.persistence import engine

    close = engine.close_engine

    async def failing_close():
        await close()
        raise RuntimeError("pool close failed")

    monkeypatch.setattr(engine, "close_engine", failing_close)
    environ["DEER_FLOW_HOME"] = str(home)
    with pytest.raises(RuntimeError):
        run_cli("--email", "closing@example.com")
    credentials = home / "credentials"
    assert [path.name for path in credentials.iterdir()] == ["closing@example.com.txt"]
    [(_, _, _, password_hash)] = users(home)
    assert verify_password(written_password(credentials / "closing@example.com.txt"), password_hash)


@pytest.mark.parametrize("email", ["not-an-email", "a/b@example.com", "../x@example.com"])
def test_bad_email_is_rejected_before_the_database(environ, home, capsys, email):
    environ["DEER_FLOW_HOME"] = str(home)
    assert run_cli("--email", email) != 0
    assert "email" in capsys.readouterr().err.lower()
    assert not (home / "data").exists()
    assert not (home / "credentials").exists()


def test_missing_runtime_config_exits_without_touching_the_database(environ, tmp_path, capsys, monkeypatch):
    import deerflow.persistence.engine as engine

    def forbidden(*args, **kwargs):
        raise AssertionError("the database must not be opened")

    monkeypatch.setattr(engine, "init_engine_from_config", forbidden)
    monkeypatch.setattr(engine, "init_engine", forbidden)
    volume = tmp_path / "volume"
    volume.mkdir()
    environ["DEER_FLOW_HOME"] = str(volume)
    assert run_cli("--email", "early@example.com") != 0
    err = capsys.readouterr().err
    assert "start the gateway" in err
    assert str(volume / "pick-runtime.yaml") in err
    assert list(volume.iterdir()) == []


def test_fills_the_same_defaults_as_the_entrypoint(environ, home, monkeypatch):
    from app.gateway import pick_entrypoint

    monkeypatch.setattr(pick_entrypoint.uvicorn, "run", lambda *args, **kwargs: None)

    def deerflow_variables() -> dict[str, str]:
        return {name: value for name, value in os.environ.items() if name.startswith("DEER_FLOW_")}

    environ.update(DEER_FLOW_HOME=str(home), PICK_DB_BACKEND="sqlite")
    pick_entrypoint.main()
    from_entrypoint = deerflow_variables()
    for name in from_entrypoint:
        del environ[name]
    environ["DEER_FLOW_HOME"] = str(home)
    assert run_cli("--email", "same@example.com") == 0
    assert deerflow_variables() == from_entrypoint == pick_entrypoint.runtime_environment({"DEER_FLOW_HOME": str(home)})


def ssh_shell(tmp_path: Path, home: Path, **extra: str) -> tuple[dict[str, str], Path]:
    """What ``railway ssh`` gives: service variables but no DEER_FLOW_*, and a backend checkout as cwd.

    The backend's ``app`` package is copied so ``python -m`` imports it from cwd exactly as in
    ``cd /app/backend``; nothing in the copy or above it is a config.yaml.
    """
    shell = tmp_path / "shell" / "backend"
    shutil.copytree(BACKEND / "app", shell / "app", ignore=shutil.ignore_patterns("__pycache__"))
    env = {name: value for name, value in os.environ.items() if not name.startswith(("DEER_FLOW_", "PICK_")) and name != "PYTHONPATH"}
    env.update(DEER_FLOW_HOME=str(home), **extra)
    assert not any(parent.joinpath("config.yaml").exists() for parent in (shell, *shell.parents))
    return env, shell


def run_in_shell(env: dict[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.gateway.auth.create_user", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_railway_ssh_shell_creates_the_user_on_the_volume(tmp_path, home):
    env, cwd = ssh_shell(tmp_path, home)
    result = run_in_shell(env, cwd, "--email", "ops@example.com")
    assert result.returncode == 0, result.stderr
    assert [row[:3] for row in users(home)] == [("ops@example.com", "user", 1)]
    credentials = home / "credentials" / "ops@example.com.txt"
    assert (credentials.stat().st_mode & 0o777) == 0o600
    password = written_password(credentials)
    assert password not in result.stdout
    assert password not in result.stderr
    assert not (cwd / ".deer-flow").exists()
    assert not (cwd / "credentials").exists()


def test_railway_ssh_shell_without_runtime_config_fails_before_the_database(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    env, cwd = ssh_shell(tmp_path, volume)
    result = run_in_shell(env, cwd, "--email", "early@example.com")
    assert result.returncode != 0
    assert "start the gateway" in result.stderr
    assert str(volume / "pick-runtime.yaml") in result.stderr
    assert list(volume.iterdir()) == []
    assert not (cwd / ".deer-flow").exists()


@pytest.fixture
def pg_database():
    """A fresh database on the PICK_TEST_PG_URL cluster, dropped afterwards; its libpq URL."""
    url = support_pg.cluster_url()
    if url is None:
        pytest.skip(f"{support_pg.URL_ENV} is not set")
    with support_pg.fresh_database(url, "t_create_user") as database:
        yield database


def test_the_postgres_case_fails_without_its_driver_instead_of_skipping(monkeypatch):
    # With PICK_TEST_PG_URL set, a run that cannot import psycopg must not pass by skipping the case.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    try:
        with support_pg.fresh_database("postgresql://unused@127.0.0.1:9/postgres", "t_no_driver"):
            pass
    except pytest.skip.Exception:
        pytest.fail("a missing psycopg skipped the PostgreSQL case")
    except ImportError:
        return
    pytest.fail("the database was created without psycopg")


def test_railway_ssh_shell_creates_the_user_in_the_postgres_schema(tmp_path, pg_database):
    import psycopg

    from app.gateway.pick_entrypoint import prepare_config

    volume = tmp_path / "volume"
    runtime = prepare_config(volume, TEMPLATE, backend="postgres")
    assert "$PICK_DATABASE_URL" in runtime.read_text()
    env, cwd = ssh_shell(tmp_path, volume, PICK_DATABASE_URL=pg_database)
    result = run_in_shell(env, cwd, "--email", "pg@example.com")
    assert result.returncode == 0, result.stderr
    with psycopg.connect(pg_database) as conn:
        rows = conn.execute("SELECT email, system_role, needs_setup FROM deerflow.users").fetchall()
        public = conn.execute("SELECT to_regclass('public.users')").fetchone()[0]
    assert rows == [("pg@example.com", "user", True)]
    assert public is None
    credentials = volume / "credentials" / "pg@example.com.txt"
    assert (credentials.stat().st_mode & 0o777) == 0o600
    assert not (volume / "data").exists()
    assert written_password(credentials) not in result.stdout
