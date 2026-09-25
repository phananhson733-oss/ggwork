"""TR-13: the start-up self-check (plan D5; design 3.1; counterexample 15) and the database settings (plan D1, 6.7).

A collector checks its collection contract, the database's migration head and its role before it takes the lease or
sends anything; a mismatch exits 2 and leaves the runtime row as it was. The head passes when this image's own chain
knows it and it is 0007 or later, so a newer migration the image ships with passes and one it does not know is refused.
"""

import re
import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from obs_db_helpers import (
    T0,
    CountingTransport,
    clock_at,
    collector_day,
    collector_env,
    execute,
    is_postgres,
    migrated,
    new_cipher,
    open_db,
    runtime_row,
)
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from ggwork_pick.observe import selfcheck
from ggwork_pick.observe.db import database_url
from ggwork_pick.observe.errors import ExitCode, Refused, StateUnavailable, exit_code_for
from ggwork_pick.observe.versions import COLLECTOR_VERSION, MIN_MIGRATION_HEAD

VERSIONS = Path(selfcheck.__file__).resolve().parents[1] / "migrations" / "versions"


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


async def _set_head(url, head):
    await execute(url, "update ggwp_alembic_version set version_num = :v", v=head)


async def _no_head(url, _):
    await execute(url, "delete from ggwp_alembic_version")


async def _no_version_table(url, _):
    await execute(url, "drop table ggwp_alembic_version")


# name -> (environment overrides, how to change the database, PostgreSQL only)
MISMATCHES = {
    "collector_differs": ({"PICK_OBS_EXPECTED_COLLECTOR": "obs-collector-v0"}, None, False),
    "collector_unset": ({"PICK_OBS_EXPECTED_COLLECTOR": None}, None, False),
    "head_unknown_to_this_image": ({}, lambda url: _set_head(url, "0099"), False),
    "head_before_0007": ({}, lambda url: _set_head(url, "0006"), False),
    "no_head": ({}, lambda url: _no_head(url, None), False),
    "no_version_table": ({}, lambda url: _no_version_table(url, None), False),
    "role_differs": ({"PICK_OBS_EXPECTED_ROLE": "pick_observer"}, None, True),
    "role_unset": ({"PICK_OBS_EXPECTED_ROLE": None}, None, True),
}


@pytest.mark.parametrize("mismatch", sorted(MISMATCHES))
@pytest.mark.asyncio
async def test_selfcheck(obs_url, mismatch):
    """[counterexample 15] Any mismatch exits 2 before any HTTP, and before the lease: the runtime row is untouched."""
    overrides, change, postgres_only = MISMATCHES[mismatch]
    if postgres_only and not is_postgres(obs_url):
        pytest.skip("SQLite has no roles")
    if change is not None:
        await change(obs_url)
    transport = CountingTransport()
    status = await collector_day(collector_env(obs_url, **overrides), new_cipher(), clock_at(T0), transport)
    assert (status, transport.sent) == (ExitCode.REFUSED, [])
    row = await runtime_row(obs_url)
    assert (row["lease_owner"], row["lease_generation"]) == (None, 0)


@pytest.mark.asyncio
async def test_selfcheck_passes_and_reports(obs_url):
    """The control: the matching image runs, and the report S6 reads names the contract, head, role and package."""
    transport = CountingTransport()
    assert await collector_day(collector_env(obs_url), new_cipher(), clock_at(T0), transport) == ExitCode.OK
    assert len(transport.sent) == 1
    db = open_db(obs_url)
    try:
        report = await selfcheck.run_selfcheck(db, selfcheck.expectations_from(collector_env(obs_url)))
    finally:
        await db.dispose()
    assert (report.collector_version, report.migration_head) == (COLLECTOR_VERSION, MIN_MIGRATION_HEAD)
    assert report.role == (collector_env(obs_url)["PICK_OBS_EXPECTED_ROLE"] if is_postgres(obs_url) else None)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", report.package_digest)
    line = report.line()
    assert all(part in line for part in (COLLECTOR_VERSION, MIN_MIGRATION_HEAD, report.package_digest))
    assert "pick-ci" not in line and "@" not in line  # never the DSN or its password


def _chain_with(tmp_path: Path, name: str, revision: str, down: str) -> Path:
    directory = tmp_path / "versions"
    shutil.copytree(VERSIONS, directory, ignore=shutil.ignore_patterns("__pycache__"))
    (directory / name).write_text(f'revision = "{revision}"\ndown_revision = "{down}"\n', encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_selfcheck_accepts_known_newer_head(obs_url, tmp_path):
    """D5: a newer head the image knows passes; the same head is refused by an image that does not know it."""
    await _set_head(obs_url, "0008")
    newer = selfcheck.migration_chain(_chain_with(tmp_path, "0008_later.py", "0008", "0007"))
    expectations = selfcheck.expectations_from(collector_env(obs_url))
    db = open_db(obs_url)
    try:
        report = await selfcheck.run_selfcheck(db, expectations, chain=newer)
        assert report.migration_head == "0008"
        with pytest.raises(Refused) as refused:
            await selfcheck.run_selfcheck(db, expectations)  # this image's chain ends at 0007
        assert exit_code_for(refused.value) == ExitCode.REFUSED
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_selfcheck_refuses_a_known_head_off_the_0007_line(obs_url, tmp_path):
    """Known is not enough: a head that branches off before 0007 lacks the observe tables."""
    await _set_head(obs_url, "0007b")
    side = selfcheck.migration_chain(_chain_with(tmp_path, "0007b_side.py", "0007b", "0006"))
    db = open_db(obs_url)
    try:
        with pytest.raises(Refused):
            await selfcheck.run_selfcheck(db, selfcheck.expectations_from(collector_env(obs_url)), chain=side)
    finally:
        await db.dispose()


def test_migration_chain_matches_alembic():
    """The chain is read with ast, never through alembic (a cron never imports it); it must say what alembic says."""
    import revisions
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(revisions.config())
    expected = {rev.revision: tuple(rev._all_down_revisions) for rev in script.walk_revisions()}
    chain = selfcheck.migration_chain()
    assert dict(chain.parents) == expected
    assert chain.knows(MIN_MIGRATION_HEAD) and chain.at_or_after(revisions.head(), MIN_MIGRATION_HEAD)
    assert not chain.at_or_after("0006", MIN_MIGRATION_HEAD)


def test_package_digest_follows_content(tmp_path):
    package = tmp_path / "ggwork_pick"
    shutil.copytree(selfcheck.PACKAGE_ROOT, package, ignore=shutil.ignore_patterns("__pycache__"))
    first = selfcheck.package_digest(package)
    assert first == selfcheck.package_digest(package)
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "x.cpython-312.pyc").write_bytes(b"compiled")
    assert selfcheck.package_digest(package) == first  # bytecode is not the package
    (package / "observe" / "versions.py").write_text("# changed\n", encoding="utf-8")
    assert selfcheck.package_digest(package) != first


@pytest.mark.asyncio
async def test_unreachable_database_exit_3(tmp_path):
    """A database the self-check cannot read is the runtime row unreadable: exit 3, nothing sent."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'missing-dir' / 'pick.db'}"
    transport = CountingTransport()
    assert await collector_day(collector_env(url), new_cipher(), clock_at(T0), transport) == ExitCode.STATE_UNAVAILABLE
    assert transport.sent == []


@pytest.mark.asyncio
async def test_unreachable_postgres_exit_3():
    url = "postgresql+asyncpg://nobody:secret-pw@127.0.0.1:1/pick"
    db = open_db(url)
    try:
        with pytest.raises(StateUnavailable) as failed:
            await selfcheck.run_selfcheck(db, selfcheck.expectations_from(collector_env(url)))
    finally:
        await db.dispose()
    assert "secret-pw" not in str(failed.value)


def test_database_url_is_checked_and_never_echoed():
    with pytest.raises(Refused):
        database_url({})
    with pytest.raises(Refused) as bad:
        database_url({"PICK_DATABASE_URL": "::not a url with secret-pw::"})
    assert "secret-pw" not in str(bad.value)
    with pytest.raises(Refused) as foreign:
        database_url({"PICK_DATABASE_URL": "mysql://u:secret-pw@h/db"})
    assert "secret-pw" not in str(foreign.value)
    for given in ("postgres://u:p@h:5432/db", "postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"):
        assert database_url({"PICK_DATABASE_URL": given}).drivername == "postgresql+asyncpg"
    assert database_url({"PICK_DATABASE_URL": "sqlite:///x.db"}).drivername == "sqlite+aiosqlite"
    assert "secret" not in repr(open_db("postgresql://u:secret@h/db"))


# ---- settings: transaction-scoped only, and the host's JSON serializer (plan 6.7, D1) ------------------------------


@pytest.mark.asyncio
async def test_no_session_set(obs_url):
    """Every setting the observe code sends is SET LOCAL or set_config(..., true); none outlives its transaction."""
    from engines import HOST_JSON_SERIALIZER

    from ggwork_pick.observe.admin import cmd_reset_disable
    from ggwork_pick.observe.lease import DbStateStore, LeasedWriter
    from ggwork_pick.observe.state import RuntimeState

    sent: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        transport = CountingTransport()
        assert await collector_day(collector_env(obs_url), new_cipher(), clock_at(T0), transport) == ExitCode.OK
        clock = clock_at(T0)
        db = open_db(obs_url)
        try:
            writer = await LeasedWriter.acquire(db, "trends", clock=clock, owner="proc-b")  # the day above released it
            await DbStateStore(writer, new_cipher()).save(RuntimeState())
            async with writer.reading() as reader:
                await reader.execute(text("select 1"))
            clock.advance(60)
            await writer.ensure_fresh()
            await writer.release()
            assert db._engine.dialect._json_serializer is HOST_JSON_SERIALIZER
        finally:
            await db.dispose()
        assert await cmd_reset_disable.run(["--operator", "wzb"], environ=collector_env(obs_url), clock=clock_at(T0)) == ExitCode.OK
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    settings = [statement for statement in sent if re.search(r"(?i)^\s*(set|reset)\b|set_config", statement)]
    assert all(re.match(r"(?i)^\s*SET\s+LOCAL\b", s) or re.search(r"(?i)set_config\s*\(", s) for s in settings), settings
    configs = [s for s in settings if "set_config" in s.lower()]
    assert all(re.search(r"(?i),\s*true\s*\)", s) and not re.search(r"(?i),\s*(false|0)\s*\)", s) for s in configs), configs
    if is_postgres(obs_url):
        assert configs  # PostgreSQL steps set their timeouts, transaction-scoped


@pytest.mark.asyncio
async def test_selfcheck_only_and_two_heads(obs_url):
    """--selfcheck-only runs the check on the channel's database and nothing else; two heads are refused."""
    report = await selfcheck.selfcheck_only("gsc", environ=collector_env(obs_url))
    assert report.migration_head == MIN_MIGRATION_HEAD
    await execute(obs_url, "insert into ggwp_alembic_version (version_num) values ('0006')")
    with pytest.raises(Refused):
        await selfcheck.selfcheck_only("trends", environ=collector_env(obs_url))
    with pytest.raises(ValueError):
        await selfcheck.selfcheck_only("youtube", environ=collector_env(obs_url))
    row = await runtime_row(obs_url, "gsc")
    assert (row["lease_owner"], row["lease_generation"]) == (None, 0)  # the check never takes the lease
