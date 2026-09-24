"""The operator commands (P2-5c admin.py; plan 5.3, 5.5, 6.5, 947-950; U40, U41; brief test 13).

`python -m ggwork_pick.mirror.admin accept-empty` sets the one-time pass G9 honours, without the lock (U40);
`python -m ggwork_pick.mirror.admin cleanup` takes the mirror lock as cleanup, clears what a dead run left (the run's
own clean_leftovers) and deletes the blobs failed batches still leave on disk. In process against the per-test
PostgreSQL database, and for real: the -m entry in a subprocess with no DEER_FLOW_* (unless a case names one) and
another cwd. Synthetic data only.
"""

import asyncio
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from engines import host_engine
from mirror_pairs import stage_pair
from mirror_rows import version_args
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
ORPHAN = "pickm_v999999"
PASSWORD = "hunter2-admin-secret"
ADVISORY_HERE = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"


@dataclass(frozen=True)
class World:
    engine: object
    service: object
    shared: PickRepository
    importer: Importer
    dsn: str
    home: Path


@pytest_asyncio.fixture
async def world(pg_db_url, tmp_path):
    from ggwork_pick.mirror.connection import dsn_from_url
    from ggwork_pick.service import PickService

    home = tmp_path / "home"
    engine = host_engine(pg_db_url)
    service = PickService(home / "pick")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    shared = PickRepository.shared(service.session_factory)
    yield World(engine, service, shared, Importer(shared, service.data_dir), dsn_from_url(pg_db_url), home)
    await engine.dispose()


async def _fetch(engine, statement: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(text(statement), params)).mappings()]


async def _one(engine, statement: str, **params):
    return (await _fetch(engine, statement, **params))[0]


async def _blob_paths(engine, batches) -> list[Path]:
    ids = [batch["id"] for batch in batches]
    rows = await _fetch(engine, "SELECT raw_blob_path FROM ggwp_import_batches WHERE id = ANY(:ids)", ids=ids)
    return sorted(Path(row["raw_blob_path"]) for row in rows)


async def _statuses(engine, batches) -> set[str]:
    rows = await _fetch(engine, "SELECT status FROM ggwp_import_batches WHERE id = ANY(:ids)", ids=[batch["id"] for batch in batches])
    return {row["status"] for row in rows}


async def _schema_exists(engine, name: str) -> bool:
    return bool(await _fetch(engine, "SELECT 1 FROM pg_namespace WHERE nspname = :n", n=name))


async def _leftovers(world: World) -> dict:
    """What a run killed mid-way leaves: a building version, an orphan schema, a staged pair, and a failed pair whose
    blobs the dead process never got to delete."""
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.versions import create_version

    conn = await open_dedicated(world.dsn)
    try:
        building = await create_version(conn, **version_args())
        await conn.execute(f"CREATE SCHEMA {ORPHAN}")
    finally:
        await conn.close()
    staged = await stage_pair(world.importer, "left")
    gone = await stage_pair(world.importer, "gone")
    await world.shared.fail_staged([batch["id"] for batch in gone])
    return {"building": building, "staged": staged, "gone": gone}


async def _cleanup(world: World, **options) -> tuple[int, str, str]:
    from ggwork_pick.mirror.admin import cleanup

    out, err = io.StringIO(), io.StringIO()
    code = await cleanup(world.dsn, data_dir=world.service.data_dir, session_factory=world.service.session_factory, out=out, err=err, **options)
    return code, out.getvalue(), err.getvalue()


async def _other_holder(world: World, holder: str):
    from ggwork_pick.mirror.connection import open_dedicated
    from ggwork_pick.mirror.lock import try_mirror_lock

    conn = await open_dedicated(world.dsn)
    assert await try_mirror_lock(conn, now=datetime.now(UTC), holder=holder)
    return conn


# ---------------------------------------------------------------- cleanup


@pytest.mark.asyncio
async def test_cleanup_clears_a_dead_runs_leftovers(world):
    from ggwork_pick.mirror.admin import EXIT_OK

    left = await _leftovers(world)
    staged_blobs, gone_blobs = await _blob_paths(world.engine, left["staged"]), await _blob_paths(world.engine, left["gone"])
    assert all(path.exists() for path in [*staged_blobs, *gone_blobs])
    code, out, err = await _cleanup(world)
    assert code == EXIT_OK, err
    version = await _one(world.engine, "SELECT status, dropped_at FROM pick_mirror.versions WHERE id = :id", id=left["building"].id)
    assert version["status"] == "failed" and version["dropped_at"] is not None
    assert not await _schema_exists(world.engine, left["building"].schema_name) and not await _schema_exists(world.engine, ORPHAN)
    assert await _statuses(world.engine, left["staged"]) == {"failed"}
    assert not any(path.exists() for path in [*staged_blobs, *gone_blobs])
    assert f"作废 building 版本 1 个（{left['building'].id}）" in out and ORPHAN in out
    assert f"failed 批次的 blob 删除 {len(gone_blobs)} 个" in out
    assert (await _one(world.engine, ADVISORY_HERE))["count"] == 0
    control = await _one(world.engine, "SELECT lock_holder, lock_holder_since FROM pick_mirror.control WHERE id = 1")
    assert control == {"lock_holder": None, "lock_holder_since": None}
    assert world.dsn not in out + err


@pytest.mark.asyncio
async def test_cleanup_keeps_a_blob_a_live_batch_shares(world):
    """A failed batch and a later published one with the same content point at one file: it stays."""
    from ggwork_pick.mirror.admin import EXIT_OK

    first = await stage_pair(world.importer, "same")
    await world.shared.fail_staged([batch["id"] for batch in first])
    again = await stage_pair(world.importer, "same")
    await world.shared.publish_agent_only(batches=again, reason="degraded:G5", t=datetime.now(UTC))
    shared_blobs = await _blob_paths(world.engine, first)
    assert shared_blobs == await _blob_paths(world.engine, again)
    code, out, err = await _cleanup(world)
    assert code == EXIT_OK, err
    assert all(path.exists() for path in shared_blobs)
    assert "failed 批次的 blob 删除 0 个" in out
    assert await _statuses(world.engine, first) == {"failed"} and await _statuses(world.engine, again) == {"published"}


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["sync", "backfill"])
async def test_cleanup_exits_when_the_lock_is_taken(world, holder):
    from ggwork_pick.mirror.admin import EXIT_LOCKED

    left = await _leftovers(world)
    blobs = await _blob_paths(world.engine, [*left["staged"], *left["gone"]])
    other = await _other_holder(world, holder)
    try:
        code, out, err = await _cleanup(world)
        assert (await _one(world.engine, ADVISORY_HERE))["count"] == 1
    finally:
        await other.close()
    assert code == EXIT_LOCKED and out == ""
    assert "镜像锁被占用" in err and holder in err
    version = await _one(world.engine, "SELECT status FROM pick_mirror.versions WHERE id = :id", id=left["building"].id)
    assert version["status"] == "building" and await _schema_exists(world.engine, ORPHAN)
    assert await _statuses(world.engine, left["staged"]) == {"importing"}
    assert all(path.exists() for path in blobs)


@pytest.mark.asyncio
async def test_cleanup_goes_on_past_a_refused_schema_drop(world, monkeypatch):
    """42501 on the versions half (another role owns a schema): recorded, the batches and blobs still cleaned."""
    from asyncpg.exceptions import InsufficientPrivilegeError

    from ggwork_pick.mirror import run
    from ggwork_pick.mirror.admin import EXIT_OK

    async def refused(conn, **options):
        raise InsufficientPrivilegeError("must be owner of schema pickm_v999999")

    monkeypatch.setattr(run, "clean_leftover_versions", refused)
    left = await _leftovers(world)
    blobs = await _blob_paths(world.engine, [*left["staged"], *left["gone"]])
    code, out, err = await _cleanup(world)
    assert code == EXIT_OK, err
    assert "跳过（权限不足）：leftover_versions：InsufficientPrivilegeError（SQLSTATE 42501）" in out
    assert "must be owner" not in out + err
    assert await _statuses(world.engine, left["staged"]) == {"failed"}
    assert not any(path.exists() for path in blobs)


@pytest.mark.asyncio
async def test_cleanup_goes_on_past_a_blob_it_may_not_delete(world):
    from ggwork_pick.mirror.admin import EXIT_OK

    gone = await stage_pair(world.importer, "locked")
    await world.shared.fail_staged([batch["id"] for batch in gone])
    blobs = await _blob_paths(world.engine, gone)
    folder = blobs[0].parent
    folder.chmod(0o500)
    try:
        code, out, err = await _cleanup(world)
    finally:
        folder.chmod(0o700)
    assert code == EXIT_OK, err
    assert f"权限拒绝 {len(blobs)} 个" in out
    assert all(path.exists() for path in blobs)
    code, out, _ = await _cleanup(world)
    assert code == EXIT_OK and not any(path.exists() for path in blobs)


@pytest.mark.asyncio
async def test_cleanup_counts_blobs_outside_the_data_directory(world, tmp_path):
    """DEER_FLOW_HOME pointing elsewhere: nothing outside $DEER_FLOW_HOME/pick is touched, and the count says so."""
    from ggwork_pick.mirror.admin import EXIT_OK, cleanup

    gone = await stage_pair(world.importer, "elsewhere")
    await world.shared.fail_staged([batch["id"] for batch in gone])
    blobs = await _blob_paths(world.engine, gone)
    out = io.StringIO()
    code = await cleanup(world.dsn, data_dir=tmp_path / "other" / "pick", session_factory=world.service.session_factory, out=out, err=io.StringIO())
    assert code == EXIT_OK
    assert f"不在数据目录下跳过 {len(blobs)} 个" in out.getvalue()
    assert all(path.exists() for path in blobs)


@pytest.mark.asyncio
async def test_cleanup_reports_an_unopenable_connection_without_the_dsn(world):
    from sqlalchemy.engine import make_url

    from ggwork_pick.mirror.admin import EXIT_FAILED, cleanup

    broken = make_url(world.dsn).set(port=1, password=PASSWORD).render_as_string(hide_password=False)
    out, err = io.StringIO(), io.StringIO()
    code = await cleanup(broken, data_dir=world.service.data_dir, session_factory=world.service.session_factory, out=out, err=err)
    assert code == EXIT_FAILED and "镜像专用连接失败" in err.getvalue()
    assert PASSWORD not in out.getvalue() + err.getvalue()


# ---------------------------------------------------------------- clean_leftovers, shared with the sync run


class _Refused(Exception):
    sqlstate = "42501"


@pytest.mark.asyncio
async def test_clean_leftovers_tolerates_a_refusal_through_the_orm(world, monkeypatch):
    """SQLAlchemy wraps asyncpg's 42501 (DBAPIError.orig): still a refusal to record, not a failed cleanup."""
    from sqlalchemy.exc import ProgrammingError

    from ggwork_pick.mirror.lock import mirror_lock
    from ggwork_pick.mirror.run import clean_leftovers

    async def refused(self):
        raise ProgrammingError("UPDATE ggwp_import_batches", {}, _Refused("permission denied for table ggwp_import_batches"))

    left = await _leftovers(world)
    monkeypatch.setattr(PickRepository, "fail_leftover_staged", refused)
    async with mirror_lock(world.dsn, holder="cleanup") as conn:
        report = await clean_leftovers(conn, world.shared, data_dir=world.service.data_dir)
    assert report["errors"] == ["leftover_batches：ProgrammingError（SQLSTATE 42501）"]
    assert report["versions"]["failed_building"] == [left["building"].id] and report["blobs_deleted"] == 0
    assert await _statuses(world.engine, left["staged"]) == {"importing"}


@pytest.mark.asyncio
async def test_clean_leftovers_still_fails_on_other_errors(world, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from ggwork_pick.mirror.lock import mirror_lock
    from ggwork_pick.mirror.run import clean_leftovers

    class _Gone(Exception):
        sqlstate = "08006"

    async def broken(self):
        raise OperationalError("SELECT", {}, _Gone("connection lost"))

    monkeypatch.setattr(PickRepository, "fail_leftover_staged", broken)
    async with mirror_lock(world.dsn, holder="cleanup") as conn:
        with pytest.raises(OperationalError):
            await clean_leftovers(conn, world.shared, data_dir=world.service.data_dir)


# ---------------------------------------------------------------- accept-empty


@pytest.mark.asyncio
async def test_accept_empty_sets_the_one_time_pass_without_the_lock(world):
    from ggwork_pick.mirror.admin import EXIT_OK, accept_empty

    other = await _other_holder(world, "sync")
    try:
        out = io.StringIO()
        assert await accept_empty(world.dsn, out=out, err=io.StringIO()) == EXIT_OK
        first = await _one(world.engine, "SELECT accept_empty_once, accept_empty_set_at FROM pick_mirror.control WHERE id = 1")
        await asyncio.sleep(0.01)
        assert await accept_empty(world.dsn, out=io.StringIO(), err=io.StringIO()) == EXIT_OK
        second = await _one(world.engine, "SELECT accept_empty_once, accept_empty_set_at FROM pick_mirror.control WHERE id = 1")
    finally:
        await other.close()
    assert first["accept_empty_once"] is True and second["accept_empty_once"] is True
    assert second["accept_empty_set_at"] > first["accept_empty_set_at"] > datetime.now(UTC) - timedelta(minutes=5)
    assert "accept_empty_once" in out.getvalue() and world.dsn not in out.getvalue()


@pytest.mark.asyncio
async def test_accept_empty_without_the_control_row_fails(world):
    from ggwork_pick.mirror.admin import EXIT_FAILED, accept_empty

    async with world.engine.begin() as conn:
        await conn.execute(text("DELETE FROM pick_mirror.control"))
    err = io.StringIO()
    assert await accept_empty(world.dsn, out=io.StringIO(), err=err) == EXIT_FAILED
    assert "pick_mirror.control" in err.getvalue()


# ---------------------------------------------------------------- the command line


def test_data_directory_defaults_to_data_pick():
    from ggwork_pick.mirror.admin import data_dir_from_env

    assert data_dir_from_env({}) == Path("/data/pick")
    assert data_dir_from_env({"DEER_FLOW_HOME": "  "}) == Path("/data/pick")
    assert data_dir_from_env({"DEER_FLOW_HOME": "/srv/deer"}) == Path("/srv/deer/pick")


@pytest.mark.parametrize("argv", [[], ["nothing"], ["cleanup", "--more"], ["accept-empty", "cleanup"]])
def test_bad_arguments_are_usage_errors(capsys, argv):
    from ggwork_pick.mirror.admin import EXIT_USAGE, main

    assert main(argv, env={}) == EXIT_USAGE
    assert "accept-empty" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["accept-empty", "cleanup"])
def test_missing_variables_are_named_in_process(capsys, command):
    from ggwork_pick.mirror.admin import EXIT_USAGE, main

    assert main([command], env={"PICK_DATABASE_URL": f"postgresql://postgres:{PASSWORD}@127.0.0.1:1/x"}) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "PGSSLMODE" in err and "PICK_DATABASE_URL" not in err and PASSWORD not in err


def _run(env: dict[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess:
    python_path = os.pathsep.join([str(EXTENSION_ROOT), str(EXTENSION_API)])
    base = {"PATH": os.environ.get("PATH", ""), "HOME": str(cwd.parent / "home-of-user"), "PYTHONPATH": python_path}
    return subprocess.run([sys.executable, *args], cwd=cwd, env={**base, **env}, capture_output=True, text=True, timeout=100)


@pytest.fixture
def work(tmp_path):
    (tmp_path / "home-of-user").mkdir()
    folder = tmp_path / "cwd"
    folder.mkdir()
    return folder


@pytest.mark.parametrize("command", ["accept-empty", "cleanup"])
def test_admin_env_and_cwd(work, command):
    """Brief test 13: python -m runs ggwork_pick/__init__.py first; no DEER_FLOW_*, cwd outside backend, no
    PICK_DATABASE_URL (railway ssh, plan 6.5): a non-zero exit naming the variable, and no value in the output."""
    from ggwork_pick.mirror.admin import EXIT_USAGE

    nothing = _run({}, work, "-m", "ggwork_pick.mirror.admin", command)
    assert nothing.returncode == EXIT_USAGE, nothing.stderr[-2000:]
    assert "PICK_DATABASE_URL" in nothing.stderr and "PGSSLMODE" in nothing.stderr
    only_ssl = _run({"PGSSLMODE": "require"}, work, "-m", "ggwork_pick.mirror.admin", command)
    assert only_ssl.returncode == EXIT_USAGE and "PICK_DATABASE_URL" in only_ssl.stderr and "PGSSLMODE" not in only_ssl.stderr
    only_url = _run({"PICK_DATABASE_URL": f"postgresql://postgres:{PASSWORD}@127.0.0.1:1/x"}, work, "-m", "ggwork_pick.mirror.admin", command)
    assert only_url.returncode == EXIT_USAGE and "PGSSLMODE" in only_url.stderr
    for result in (nothing, only_ssl, only_url):
        assert PASSWORD not in result.stdout + result.stderr and "RuntimeWarning" not in result.stderr
    assert list(work.iterdir()) == []


def test_admin_imports_nothing_of_the_gateway(work):
    """app.gateway.* needs cwd /app/backend and the app config; the command must not pull it in (plan 6.5)."""
    probe = "import sys, ggwork_pick.mirror.admin; print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')))"
    result = _run({}, work, "-c", probe)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "[]"


@pytest.mark.asyncio
async def test_admin_runs_for_real_in_a_clean_process(world, work):
    left = await _leftovers(world)
    blobs = await _blob_paths(world.engine, [*left["staged"], *left["gone"]])
    env = {"PICK_DATABASE_URL": world.dsn, "PGSSLMODE": "disable"}
    accepted = await asyncio.to_thread(_run, env, work, "-m", "ggwork_pick.mirror.admin", "accept-empty")
    assert accepted.returncode == 0, accepted.stderr[-3000:]
    assert (await _one(world.engine, "SELECT accept_empty_once FROM pick_mirror.control WHERE id = 1"))["accept_empty_once"] is True
    # Without DEER_FLOW_HOME the data directory is /data/pick: the database half is cleaned, these blobs are not touched.
    defaulted = await asyncio.to_thread(_run, env, work, "-m", "ggwork_pick.mirror.admin", "cleanup")
    assert defaulted.returncode == 0, defaulted.stderr[-3000:]
    assert not await _schema_exists(world.engine, left["building"].schema_name) and not await _schema_exists(world.engine, ORPHAN)
    assert await _statuses(world.engine, left["staged"]) == {"failed"} and all(path.exists() for path in blobs)
    homed = await asyncio.to_thread(_run, {**env, "DEER_FLOW_HOME": str(world.home)}, work, "-m", "ggwork_pick.mirror.admin", "cleanup")
    assert homed.returncode == 0, homed.stderr[-3000:]
    assert not any(path.exists() for path in blobs)
    for result in (accepted, defaulted, homed):
        assert world.dsn not in result.stdout + result.stderr and "RuntimeWarning" not in result.stderr
    assert (await _one(world.engine, ADVISORY_HERE))["count"] == 0
    assert list(work.iterdir()) == []
